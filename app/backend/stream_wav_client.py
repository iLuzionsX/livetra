from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from contextlib import suppress

import numpy as np
import soundfile as sf
import websockets


FRAME_SAMPLES = 320


def read_wav_16k_mono(path: Path) -> np.ndarray:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    if sample_rate != 16_000:
        raise ValueError("Test WAV must be 16 kHz")
    mono = np.clip(audio.mean(axis=1), -1.0, 1.0).astype("<f4", copy=False)
    pad = (-mono.shape[0]) % FRAME_SAMPLES
    if pad:
        mono = np.pad(mono, (0, pad))
    return mono


async def main() -> None:
    parser = argparse.ArgumentParser(description="Stream a 16 kHz WAV to the local WS server")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--url", default="ws://127.0.0.1:8765/")
    parser.add_argument("--source", default="English")
    parser.add_argument("--target", default="Spanish")
    parser.add_argument("--vocab", default="", help="Comma-separated custom vocabulary")
    parser.add_argument("--no-polish", action="store_true", help="Disable async polish pass")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    audio = read_wav_16k_mono(args.wav)
    custom_vocab = [item.strip() for item in args.vocab.split(",") if item.strip()]
    async with websockets.connect(args.url, max_size=None) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "config",
                    "version": 2,
                    "source_lang": args.source,
                    "target_lang": args.target,
                    "custom_vocab": custom_vocab,
                    "segmenter": "silero",
                    "polish_enabled": not args.no_polish,
                }
            )
        )
        await ws.send(json.dumps({"type": "start"}))
        done_seen = asyncio.Event()

        async def reader() -> None:
            async for message in ws:
                print(message, flush=True)
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if payload.get("type") == "error":
                    done_seen.set()
                elif args.no_polish and payload.get("type") == "final":
                    done_seen.set()
                elif not args.no_polish and payload.get("type") == "polished":
                    done_seen.set()

        reader_task = asyncio.create_task(reader())
        for offset in range(0, audio.shape[0], FRAME_SAMPLES):
            await ws.send(audio[offset : offset + FRAME_SAMPLES].tobytes())
            await asyncio.sleep(0.02)
        await ws.send(json.dumps({"type": "stop"}))
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(done_seen.wait(), timeout=args.timeout)
        reader_task.cancel()
        with suppress(asyncio.CancelledError):
            await reader_task


if __name__ == "__main__":
    asyncio.run(main())
