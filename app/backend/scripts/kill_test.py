from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import numpy as np
import soundfile as sf
import websockets

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from session import ARCHIVE_ROOT  # noqa: E402


FRAME_SAMPLES = 320
SAMPLE_RATE = 16_000
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE


def read_wav_16k_mono(path: Path) -> np.ndarray:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    if sample_rate != SAMPLE_RATE:
        raise ValueError(f"Test WAV must be {SAMPLE_RATE} Hz, got {sample_rate}")
    mono = np.clip(audio.mean(axis=1), -1.0, 1.0).astype("<f4", copy=False)
    pad = (-mono.shape[0]) % FRAME_SAMPLES
    if pad:
        mono = np.pad(mono, (0, pad))
    return mono


def start_backend(port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["SESSION_AUTOSAVE_SECONDS"] = "10"
    return subprocess.Popen(
        ["uv", "run", "python", "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def stop_backend(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


async def wait_for_health(port: int, process: subprocess.Popen, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Backend exited before readiness with code {process.returncode}")
        try:
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("ok") is True:
                return
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = exc
        await asyncio.sleep(1)
    raise TimeoutError(f"Backend did not become ready within {timeout_seconds}s: {last_error}")


class ReconnectingProducer:
    def __init__(self, port: int, session_id: str) -> None:
        self.port = port
        self.session_id = session_id
        self.ws: websockets.ClientConnection | None = None
        self.received: list[dict] = []
        self.connected = asyncio.Event()
        self.reader_task: asyncio.Task | None = None
        self.stop_requested = False

    async def connect(self, mode: str) -> None:
        self.ws = await websockets.connect(
            f"ws://127.0.0.1:{self.port}/?session={self.session_id}",
            max_size=None,
        )
        if mode == "start":
            await self.ws.send(
                json.dumps(
                    {
                        "type": "config",
                        "version": 2,
                        "source_lang": "English",
                        "target_lang": "Spanish",
                        "custom_vocab": [],
                        "segmenter": "silero",
                        "polish_enabled": False,
                    }
                )
            )
            await self.ws.send(json.dumps({"type": "start"}))
        else:
            await self.ws.send(json.dumps({"type": "resume"}))
            await self.ws.send(
                json.dumps(
                    {
                        "type": "config",
                        "version": 2,
                        "source_lang": "English",
                        "target_lang": "Spanish",
                        "custom_vocab": [],
                        "segmenter": "silero",
                        "polish_enabled": False,
                    }
                )
            )
        self.reader_task = asyncio.create_task(self._reader())
        self.connected.set()

    async def reconnect_until_ready(self) -> None:
        self.connected.clear()
        while not self.stop_requested:
            try:
                await self.connect("resume")
                return
            except Exception:
                await asyncio.sleep(1)

    async def send_audio(self, audio: np.ndarray, run_for_seconds: float) -> None:
        silence = np.zeros(6_400, dtype="<f4")
        deadline = time.monotonic() + run_for_seconds
        while time.monotonic() < deadline:
            for buffer in (audio, silence):
                for offset in range(0, buffer.shape[0], FRAME_SAMPLES):
                    if time.monotonic() >= deadline:
                        break
                    ws = self.ws
                    if ws is None:
                        await self.connected.wait()
                        continue
                    try:
                        await ws.send(buffer[offset : offset + FRAME_SAMPLES].tobytes())
                    except websockets.ConnectionClosed:
                        self.ws = None
                        asyncio.create_task(self.reconnect_until_ready())
                    await asyncio.sleep(FRAME_SECONDS)

    async def stop(self) -> None:
        self.stop_requested = True
        if self.ws is not None:
            with suppress(Exception):
                await self.ws.send(json.dumps({"type": "stop"}))
            await self.ws.close()
        if self.reader_task is not None:
            self.reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.reader_task

    async def _reader(self) -> None:
        ws = self.ws
        if ws is None:
            return
        try:
            async for raw in ws:
                self.received.append(json.loads(raw))
        except websockets.ConnectionClosed:
            if not self.stop_requested:
                self.ws = None
                asyncio.create_task(self.reconnect_until_ready())


def latest_archive_for_session(session_id: str) -> Path:
    matches: list[Path] = []
    for meta_path in ARCHIVE_ROOT.glob("*/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("session_id") == session_id:
            matches.append(meta_path.parent)
    if not matches:
        raise AssertionError(f"No archive found for session {session_id}")
    return max(matches, key=lambda path: path.stat().st_mtime)


def assert_archive(path: Path, kill_time: float) -> dict:
    events = json.loads((path / "transcript.json").read_text(encoding="utf-8"))
    final_ids = [
        int(event["payload"]["utterance_id"])
        for event in events
        if event.get("payload", {}).get("type") == "final"
    ]
    if final_ids != sorted(final_ids):
        raise AssertionError(f"Final utterance ids are not monotonic: {final_ids}")
    if len(final_ids) != len(set(final_ids)):
        raise AssertionError(f"Duplicate final utterance ids found: {final_ids}")
    if not any(event["timestamp_seconds"] < kill_time for event in events if event.get("payload", {}).get("type") == "final"):
        raise AssertionError("Archive has no pre-kill final utterances")
    if not any(event["timestamp_seconds"] > kill_time for event in events if event.get("payload", {}).get("type") == "final"):
        raise AssertionError("Archive has no post-kill final utterances")
    return {"archive": str(path), "final_count": len(final_ids), "first_final_id": final_ids[0], "last_final_id": final_ids[-1]}


async def run(args: argparse.Namespace) -> int:
    audio = read_wav_16k_mono(args.wav)
    session_id = args.session_id or f"kill-test-{uuid4()}"
    process = start_backend(args.port)
    producer = ReconnectingProducer(args.port, session_id)
    try:
        await wait_for_health(args.port, process, args.startup_timeout)
        await producer.connect("start")
        stream_task = asyncio.create_task(producer.send_audio(audio, args.pre_kill_seconds + args.post_kill_seconds))
        await asyncio.sleep(args.pre_kill_seconds)
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
        await asyncio.sleep(args.down_seconds)
        process = start_backend(args.port)
        await wait_for_health(args.port, process, args.startup_timeout)
        await producer.connected.wait()
        await stream_task
        await producer.stop()
        await asyncio.sleep(3)
        archive = latest_archive_for_session(session_id)
        summary = assert_archive(archive, args.pre_kill_seconds)
        summary.update({"session_id": session_id, "received_finals": sum(1 for msg in producer.received if msg.get("type") == "final")})
        print(json.dumps(summary, indent=2))
        return 0
    finally:
        await producer.stop()
        stop_backend(process)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kill backend and verify archive resume.")
    parser.add_argument("--wav", type=Path, default=Path("sample.wav"))
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--startup-timeout", type=float, default=240)
    parser.add_argument("--pre-kill-seconds", type=float, default=60)
    parser.add_argument("--post-kill-seconds", type=float, default=60)
    parser.add_argument("--down-seconds", type=float, default=2)
    parser.add_argument("--session-id", default="")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
