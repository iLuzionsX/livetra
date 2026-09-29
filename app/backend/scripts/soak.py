from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import median
from urllib.error import URLError
from urllib.request import urlopen

import numpy as np
import psutil
import soundfile as sf
import websockets

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from mlx_worker import TEMP_WAV_ROOT  # noqa: E402
from session import ARCHIVE_ROOT  # noqa: E402


FRAME_SAMPLES = 320
SAMPLE_RATE = 16_000
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE
DEFAULT_PORT = 8766


@dataclass
class SoakEvent:
    event: str
    elapsed_seconds: float
    detail: str = ""


@dataclass
class FinalEvent:
    elapsed_seconds: float
    utterance_id: int
    latency_seconds: float
    commit_reason: str
    silence_to_final_seconds: float | None


@dataclass
class SoakState:
    started_at: float = field(default_factory=time.monotonic)
    speech_started_at: dict[int, float] = field(default_factory=dict)
    finalized_ids: set[int] = field(default_factory=set)
    final_latencies: list[FinalEvent] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    status_events: list[dict] = field(default_factory=list)
    events: list[SoakEvent] = field(default_factory=list)
    latest_worker_state: str = ""
    fault_sent_at: float | None = None

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at


def read_wav_16k_mono(path: Path) -> np.ndarray:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    if sample_rate != SAMPLE_RATE:
        raise ValueError(f"Test WAV must be {SAMPLE_RATE} Hz, got {sample_rate}")
    mono = np.clip(audio.mean(axis=1), -1.0, 1.0).astype("<f4", copy=False)
    pad = (-mono.shape[0]) % FRAME_SAMPLES
    if pad:
        mono = np.pad(mono, (0, pad))
    return mono


def start_backend(port: int, backend_dir: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        ["uv", "run", "python", "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=backend_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )


def stop_backend(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


async def mirror_backend_log(process: subprocess.Popen, log_path: Path) -> None:
    if process.stdout is None:
        return
    with log_path.open("w", encoding="utf-8") as handle:
        while True:
            line = await asyncio.to_thread(process.stdout.readline)
            if not line:
                break
            handle.write(line)
            handle.flush()


async def wait_for_health(port: int, process: subprocess.Popen, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/health"
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Backend exited before readiness with code {process.returncode}")
        try:
            with urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("ok") is True:
                return
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = exc
        await asyncio.sleep(1)
    raise TimeoutError(f"Backend did not become ready within {timeout_seconds}s: {last_error}")


async def reader(ws: websockets.ClientConnection, state: SoakState) -> None:
    async for raw in ws:
        payload = json.loads(raw)
        now = state.elapsed()
        msg_type = payload.get("type")
        if msg_type == "speech_start":
            state.speech_started_at[int(payload["utterance_id"])] = now
        elif msg_type == "final":
            utterance_id = int(payload["utterance_id"])
            if utterance_id in state.finalized_ids:
                continue
            state.finalized_ids.add(utterance_id)
            started_at = state.speech_started_at.get(utterance_id)
            if started_at is not None:
                last_audio_frame_unix_seconds = payload.get("last_audio_frame_unix_seconds")
                silence_to_final_seconds = (
                    max(0.0, time.time() - float(last_audio_frame_unix_seconds))
                    if last_audio_frame_unix_seconds is not None
                    else None
                )
                state.final_latencies.append(
                    FinalEvent(
                        elapsed_seconds=now,
                        utterance_id=utterance_id,
                        latency_seconds=now - started_at,
                        commit_reason=str(payload.get("commit_reason") or "unknown"),
                        silence_to_final_seconds=silence_to_final_seconds,
                    )
                )
        elif msg_type == "error":
            state.errors.append({"elapsed_seconds": now, "payload": payload})
        elif msg_type == "status":
            state.latest_worker_state = str(payload.get("state", ""))
            state.status_events.append({"elapsed_seconds": now, "payload": payload})


async def stream_audio(
    ws: websockets.ClientConnection,
    audio: np.ndarray,
    duration_seconds: float,
) -> None:
    silence = np.zeros(int(0.4 * SAMPLE_RATE), dtype="<f4")
    silence = np.pad(silence, (0, (-silence.shape[0]) % FRAME_SAMPLES))
    deadline = time.monotonic() + duration_seconds
    while time.monotonic() < deadline:
        for buffer in (audio, silence):
            for offset in range(0, buffer.shape[0], FRAME_SAMPLES):
                if time.monotonic() >= deadline:
                    break
                await ws.send(buffer[offset : offset + FRAME_SAMPLES].tobytes())
                await asyncio.sleep(FRAME_SECONDS)
            if time.monotonic() >= deadline:
                break

    for offset in range(0, silence.shape[0], FRAME_SAMPLES):
        await ws.send(silence[offset : offset + FRAME_SAMPLES].tobytes())
        await asyncio.sleep(FRAME_SECONDS)


def process_tree_rss_bytes(root_pid: int) -> int:
    try:
        root = psutil.Process(root_pid)
    except psutil.NoSuchProcess:
        return 0
    processes = [root] + root.children(recursive=True)
    rss = 0
    for process in processes:
        with suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            rss += process.memory_info().rss
    return rss


def count_temp_files() -> int:
    return sum(1 for path in TEMP_WAV_ROOT.glob("*") if path.is_file())


def count_session_archives() -> int:
    if not ARCHIVE_ROOT.exists():
        return 0
    return sum(1 for path in ARCHIVE_ROOT.iterdir() if path.is_dir())


async def collect_metrics(
    process: subprocess.Popen,
    state: SoakState,
    csv_path: Path,
    interval_seconds: float,
    duration_seconds: float,
) -> list[dict]:
    rows: list[dict] = []
    fieldnames = [
        "elapsed_seconds",
        "rss_bytes",
        "temp_file_count",
        "archive_entry_count",
        "latest_final_latency_seconds",
        "latest_silence_to_final_seconds",
        "latest_commit_reason",
        "new_finals",
        "final_count",
        "error_count",
        "worker_state",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        last_final_index = 0
        while state.elapsed() < duration_seconds:
            await asyncio.sleep(interval_seconds)
            latest_final = state.final_latencies[-1] if state.final_latencies else None
            latest_latency = latest_final.latency_seconds if latest_final is not None else None
            latest_silence_latency = (
                latest_final.silence_to_final_seconds if latest_final is not None else None
            )
            new_finals = state.final_latencies[last_final_index:]
            last_final_index = len(state.final_latencies)
            row = {
                "elapsed_seconds": round(state.elapsed(), 3),
                "rss_bytes": process_tree_rss_bytes(process.pid),
                "temp_file_count": count_temp_files(),
                "archive_entry_count": count_session_archives(),
                "latest_final_latency_seconds": round(latest_latency, 3)
                if latest_latency is not None
                else "",
                "latest_silence_to_final_seconds": round(latest_silence_latency, 3)
                if latest_silence_latency is not None
                else "",
                "latest_commit_reason": latest_final.commit_reason if latest_final else "",
                "new_finals": ";".join(
                    f"{item.utterance_id}:{item.commit_reason}:"
                    f"{item.silence_to_final_seconds:.3f}"
                    if item.silence_to_final_seconds is not None
                    else f"{item.utterance_id}:{item.commit_reason}:"
                    for item in new_finals
                ),
                "final_count": len(state.final_latencies),
                "error_count": len(state.errors),
                "worker_state": state.latest_worker_state,
            }
            writer.writerow(row)
            handle.flush()
            rows.append(row)
    return rows


def find_worker_child_pid(root_pid: int) -> int:
    root = psutil.Process(root_pid)
    candidates = []
    for child in root.children(recursive=True):
        with suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            cmdline = " ".join(child.cmdline())
            if "resource_tracker" in cmdline:
                continue
            if "spawn_main" in cmdline or "multiprocessing" in cmdline:
                candidates.append(child)
    if not candidates:
        raise RuntimeError("Could not find MLX worker child process for fault injection")
    candidates.sort(key=lambda proc: proc.create_time(), reverse=True)
    return candidates[0].pid


async def inject_worker_fault(
    process: subprocess.Popen,
    state: SoakState,
    fault_at_seconds: float,
) -> None:
    await asyncio.sleep(fault_at_seconds)
    worker_pid = find_worker_child_pid(process.pid)
    state.fault_sent_at = state.elapsed()
    state.events.append(
        SoakEvent("fault_injected", state.fault_sent_at, f"SIGTERM pid={worker_pid}")
    )
    os.kill(worker_pid, signal.SIGTERM)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * pct
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def closest_row(rows: list[dict], target_seconds: float) -> dict | None:
    if not rows:
        return None
    return min(rows, key=lambda row: abs(float(row["elapsed_seconds"]) - target_seconds))


def summarize(
    rows: list[dict],
    state: SoakState,
    duration_seconds: float,
    inject_fault: bool,
) -> tuple[dict, list[str]]:
    rss_values = [float(row["rss_bytes"]) for row in rows]
    temp_counts = [float(row["temp_file_count"]) for row in rows]
    archive_counts = [float(row["archive_entry_count"]) for row in rows]
    latencies = [item.latency_seconds for item in state.final_latencies]
    silence_latencies = [
        item.silence_to_final_seconds
        for item in state.final_latencies
        if item.silence_to_final_seconds is not None
    ]
    reason_counts: dict[str, int] = {}
    for item in state.final_latencies:
        reason_counts[item.commit_reason] = reason_counts.get(item.commit_reason, 0) + 1
    total_reasons = sum(reason_counts.values())
    reason_percentages = {
        reason: (count / total_reasons * 100.0 if total_reasons else 0.0)
        for reason, count in sorted(reason_counts.items())
    }
    early_latencies = [
        item.latency_seconds
        for item in state.final_latencies
        if 0 <= item.elapsed_seconds <= 5 * 60
    ]
    late_latencies = [
        item.latency_seconds
        for item in state.final_latencies
        if max(0.0, duration_seconds - 5 * 60)
        <= item.elapsed_seconds
        <= duration_seconds + 60
    ]
    summary = {
        "duration_seconds": duration_seconds,
        "final_count": len(state.final_latencies),
        "error_count": len(state.errors),
        "rss_bytes": stats(rss_values),
        "temp_file_count": stats(temp_counts),
        "archive_entry_count": stats(archive_counts),
        "final_latency_seconds": stats(latencies),
        "silence_to_final_seconds": stats(silence_latencies),
        "commit_reason_counts": reason_counts,
        "commit_reason_percentages": reason_percentages,
        "early_final_latency_seconds": stats(early_latencies),
        "late_final_latency_seconds": stats(late_latencies),
        "status_events": state.status_events,
        "events": [event.__dict__ for event in state.events],
    }

    failures: list[str] = []
    row_5m = closest_row(rows, min(5 * 60, duration_seconds))
    row_last = rows[-1] if rows else None
    if duration_seconds >= 5 * 60 and row_5m and row_last:
        rss_5m = float(row_5m["rss_bytes"])
        rss_last = float(row_last["rss_bytes"])
        if rss_5m > 0 and rss_last > rss_5m * 1.25:
            failures.append(
                f"RSS grew above threshold: last={rss_last:.0f}, five_min={rss_5m:.0f}"
            )
    if temp_counts and max(temp_counts) > 10:
        failures.append(f"Temp-file count exceeded 10: max={max(temp_counts):.0f}")
    early_median = median(early_latencies) if early_latencies else None
    late_median = median(late_latencies) if late_latencies else None
    if early_median is not None and late_median is not None and late_median > early_median * 1.5:
        failures.append(
            f"Late final latency drifted above threshold: late={late_median:.3f}s, early={early_median:.3f}s"
        )
    if state.errors:
        failures.append(f"Received {len(state.errors)} error events")
    if not state.final_latencies:
        failures.append("No finalized utterances were received")
    if inject_fault:
        recovering_seen = any(
            event["payload"].get("state") == "recovering" for event in state.status_events
        )
        ready_after_fault = any(
            state.fault_sent_at is not None
            and event["elapsed_seconds"] >= state.fault_sent_at
            and event["payload"].get("state") == "ready"
            for event in state.status_events
        )
        resumed_latency = None
        if state.fault_sent_at is not None:
            finals_after_fault = [
                item.elapsed_seconds
                for item in state.final_latencies
                if item.elapsed_seconds >= state.fault_sent_at
            ]
            if finals_after_fault:
                resumed_latency = finals_after_fault[0] - state.fault_sent_at
        summary["fault_recovery_seconds"] = resumed_latency
        if not recovering_seen:
            failures.append("Worker fault did not emit recovering status")
        if not ready_after_fault:
            failures.append("Worker fault did not return to ready status")
        if resumed_latency is None or resumed_latency > 30:
            failures.append(
                "Inference did not resume within 30s after worker fault"
                if resumed_latency is None
                else f"Inference resumed too slowly after worker fault: {resumed_latency:.3f}s"
            )
    return summary, failures


def stats(values: list[float]) -> dict:
    if not values:
        return {"min": None, "median": None, "p95": None, "max": None}
    return {
        "min": min(values),
        "median": median(values),
        "p95": percentile(values, 0.95),
        "max": max(values),
    }


async def run(args: argparse.Namespace) -> int:
    audio = read_wav_16k_mono(args.wav)
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H-%M-%S%z")
    csv_path = Path(args.output_dir) / f"soak_{timestamp}.csv"
    summary_path = Path(args.output_dir) / f"soak_{timestamp}.summary.json"
    log_path = Path(args.output_dir) / f"soak_{timestamp}.backend.log"

    process = start_backend(args.port, BACKEND_DIR)
    log_task = asyncio.create_task(mirror_backend_log(process, log_path))
    try:
        await wait_for_health(args.port, process, args.startup_timeout)
        state = SoakState()
        async with websockets.connect(f"ws://127.0.0.1:{args.port}/", max_size=None) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "config",
                        "version": 2,
                        "source_lang": "English",
                        "target_lang": "Spanish",
                        "custom_vocab": [],
                        "segmenter": "silero",
                        "polish_enabled": not args.no_polish,
                    }
                )
            )
            await ws.send(json.dumps({"type": "start"}))

            tasks = [
                asyncio.create_task(reader(ws, state)),
                asyncio.create_task(stream_audio(ws, audio, args.duration_seconds)),
                asyncio.create_task(
                    collect_metrics(
                        process,
                        state,
                        csv_path,
                        args.metric_interval_seconds,
                        args.duration_seconds + args.drain_seconds,
                    )
                ),
            ]
            if args.inject_fault:
                tasks.append(
                    asyncio.create_task(inject_worker_fault(process, state, args.fault_at_seconds))
                )

            await tasks[1]
            await ws.send(json.dumps({"type": "stop"}))
            await asyncio.sleep(args.drain_seconds)
            tasks[0].cancel()
            with suppress(asyncio.CancelledError):
                await tasks[0]
            rows = await tasks[2]
            for task in tasks[3:]:
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

        summary, failures = summarize(rows, state, args.duration_seconds, args.inject_fault)
        summary["csv_path"] = str(csv_path)
        summary["backend_log_path"] = str(log_path)
        summary["failures"] = failures
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
        return 1 if failures else 0
    finally:
        stop_backend(process)
        log_task.cancel()
        with suppress(asyncio.CancelledError):
            await log_task


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LiveTR3 sustained-load soak test.")
    parser.add_argument("--wav", type=Path, default=Path("sample.wav"))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--duration-seconds", type=float, default=30 * 60)
    parser.add_argument("--metric-interval-seconds", type=float, default=60)
    parser.add_argument("--startup-timeout", type=float, default=240)
    parser.add_argument("--drain-seconds", type=float, default=20)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--inject-fault", action="store_true")
    parser.add_argument("--fault-at-seconds", type=float, default=3 * 60)
    parser.add_argument("--no-polish", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run(parse_args())))
    except KeyboardInterrupt:
        sys.exit(130)
