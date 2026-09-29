from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


FRAME_SAMPLES = 320


@dataclass
class CaseResult:
    name: str
    passed: bool
    final_count: int
    duplicate_final_ids: list[int] = field(default_factory=list)
    final_ids: list[int] = field(default_factory=list)
    first_partial_ms: float | None = None
    final_after_audio_ms: float | None = None
    source_wer: float | None = None
    target_wer: float | None = None
    original: str = ""
    translation: str = ""
    errors: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold(), flags=re.UNICODE)


def word_error_rate(reference: str, hypothesis: str) -> float:
    reference_words = _words(reference)
    hypothesis_words = _words(hypothesis)
    if not reference_words:
        return 0.0 if not hypothesis_words else 1.0

    previous = list(range(len(hypothesis_words) + 1))
    for row, reference_word in enumerate(reference_words, start=1):
        current = [row]
        for column, hypothesis_word in enumerate(hypothesis_words, start=1):
            substitution = previous[column - 1] + (reference_word != hypothesis_word)
            insertion = current[column - 1] + 1
            deletion = previous[column] + 1
            current.append(min(substitution, insertion, deletion))
        previous = current
    return previous[-1] / len(reference_words)


def read_wav_16k_mono(path: Path) -> Any:
    import numpy as np
    import soundfile as sf

    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    if sample_rate != 16_000:
        raise ValueError(f"{path}: expected 16 kHz audio, got {sample_rate} Hz")
    mono = np.clip(audio.mean(axis=1), -1.0, 1.0).astype("<f4", copy=False)
    pad = (-mono.shape[0]) % FRAME_SAMPLES
    if pad:
        mono = np.pad(mono, (0, pad))
    return mono


async def run_case(
    case: dict[str, Any],
    *,
    manifest_dir: Path,
    url: str,
    timeout: float,
    quiet_drain_seconds: float,
    max_final_after_audio_ms: float | None,
    max_source_wer: float | None,
    max_target_wer: float | None,
) -> CaseResult:
    import websockets

    name = str(case.get("name") or Path(str(case["wav"])).stem)
    wav_path = (manifest_dir / str(case["wav"])).resolve()
    audio = read_wav_16k_mono(wav_path)
    source = str(case.get("source", "English"))
    target = str(case.get("target", "Spanish"))
    custom_vocab = list(case.get("custom_vocab", []))

    started_at = time.perf_counter()
    audio_ended_at: float | None = None
    first_partial_at: float | None = None
    last_final_at: float | None = None
    last_message_at = started_at
    final_payloads: list[dict[str, Any]] = []
    final_ids_seen: set[int] = set()
    duplicate_final_ids: list[int] = []
    open_utterance_ids: set[int] = set()
    errors: list[str] = []

    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "config",
                    "version": 2,
                    "source_lang": source,
                    "target_lang": target,
                    "custom_vocab": custom_vocab,
                    "segmenter": "silero",
                    "polish_enabled": False,
                }
            )
        )
        await ws.send(json.dumps({"type": "start"}))

        async def reader() -> None:
            nonlocal first_partial_at, last_final_at, last_message_at
            async for raw_message in ws:
                now = time.perf_counter()
                last_message_at = now
                try:
                    payload = json.loads(raw_message)
                except (json.JSONDecodeError, TypeError):
                    errors.append("server returned non-JSON text")
                    continue

                message_type = payload.get("type")
                utterance_id = payload.get("utterance_id")
                if message_type == "speech_start" and isinstance(utterance_id, int):
                    open_utterance_ids.add(utterance_id)
                elif message_type == "partial":
                    if first_partial_at is None:
                        first_partial_at = now
                elif message_type == "final":
                    if isinstance(utterance_id, int):
                        if utterance_id in final_ids_seen:
                            duplicate_final_ids.append(utterance_id)
                        final_ids_seen.add(utterance_id)
                        open_utterance_ids.discard(utterance_id)
                    final_payloads.append(payload)
                    last_final_at = now
                elif message_type == "error":
                    errors.append(str(payload.get("message", "unknown backend error")))

        reader_task = asyncio.create_task(reader())
        try:
            for offset in range(0, audio.shape[0], FRAME_SAMPLES):
                await ws.send(audio[offset : offset + FRAME_SAMPLES].tobytes())
                await asyncio.sleep(0.02)

            audio_ended_at = time.perf_counter()
            await ws.send(json.dumps({"type": "stop"}))

            deadline = time.perf_counter() + timeout
            while time.perf_counter() < deadline:
                await asyncio.sleep(0.1)
                quiet_for = time.perf_counter() - last_message_at
                if final_payloads and not open_utterance_ids and quiet_for >= quiet_drain_seconds:
                    break
            else:
                errors.append(f"timed out after {timeout:.1f}s waiting for final captions")
        finally:
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass

    final_ids = [
        int(payload["utterance_id"])
        for payload in final_payloads
        if isinstance(payload.get("utterance_id"), int)
    ]
    original = " ".join(str(payload.get("original", "")).strip() for payload in final_payloads).strip()
    translation = " ".join(
        str(payload.get("translation", "")).strip() for payload in final_payloads
    ).strip()

    result = CaseResult(
        name=name,
        passed=True,
        final_count=len(final_payloads),
        duplicate_final_ids=sorted(set(duplicate_final_ids)),
        final_ids=final_ids,
        first_partial_ms=(first_partial_at - started_at) * 1_000 if first_partial_at else None,
        final_after_audio_ms=(last_final_at - audio_ended_at) * 1_000
        if last_final_at is not None and audio_ended_at is not None
        else None,
        original=original,
        translation=translation,
        errors=errors,
    )

    expected_original = case.get("expected_original")
    expected_translation = case.get("expected_translation")
    if isinstance(expected_original, str):
        result.source_wer = word_error_rate(expected_original, original)
    if isinstance(expected_translation, str):
        result.target_wer = word_error_rate(expected_translation, translation)

    if errors:
        result.failures.extend(errors)
    if not final_payloads:
        result.failures.append("no final captions received")
    if result.duplicate_final_ids:
        result.failures.append(f"duplicate final utterance IDs: {result.duplicate_final_ids}")
    if final_ids != sorted(final_ids):
        result.failures.append(f"final utterance IDs are not monotonic: {final_ids}")
    if len(final_ids) != len(set(final_ids)):
        result.failures.append("final utterance IDs are not unique")
    if (
        max_final_after_audio_ms is not None
        and result.final_after_audio_ms is not None
        and result.final_after_audio_ms > max_final_after_audio_ms
    ):
        result.failures.append(
            f"final latency {result.final_after_audio_ms:.1f}ms exceeds "
            f"{max_final_after_audio_ms:.1f}ms"
        )
    if max_source_wer is not None and result.source_wer is not None and result.source_wer > max_source_wer:
        result.failures.append(
            f"source WER {result.source_wer:.3f} exceeds {max_source_wer:.3f}"
        )
    if max_target_wer is not None and result.target_wer is not None and result.target_wer > max_target_wer:
        result.failures.append(
            f"target WER {result.target_wer:.3f} exceeds {max_target_wer:.3f}"
        )

    result.passed = not result.failures
    return result


async def async_main(args: argparse.Namespace) -> int:
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("manifest must contain a non-empty 'cases' list")

    results: list[CaseResult] = []
    for case in cases:
        if not isinstance(case, dict) or "wav" not in case:
            raise ValueError("every case must be an object containing a 'wav' path")
        result = await run_case(
            case,
            manifest_dir=manifest_path.parent,
            url=args.url,
            timeout=args.timeout,
            quiet_drain_seconds=args.quiet_drain_seconds,
            max_final_after_audio_ms=args.max_final_after_audio_ms,
            max_source_wer=args.max_source_wer,
            max_target_wer=args.max_target_wer,
        )
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        latency = (
            f"{result.final_after_audio_ms:.0f}ms"
            if result.final_after_audio_ms is not None
            else "n/a"
        )
        print(
            f"{status} {result.name}: finals={result.final_count} "
            f"final_after_audio={latency} source_wer={result.source_wer} "
            f"target_wer={result.target_wer}",
            flush=True,
        )
        for failure in result.failures:
            print(f"  - {failure}", flush=True)

    summary = {
        "passed": all(result.passed for result in results),
        "case_count": len(results),
        "passed_count": sum(result.passed for result in results),
        "results": [asdict(result) for result in results],
    }
    rendered = json.dumps(summary, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if summary["passed"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run prerecorded WAV cases through a running LiveTR3 backend and enforce invariants."
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--url", default="ws://127.0.0.1:8765/")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--quiet-drain-seconds", type=float, default=0.75)
    parser.add_argument("--max-final-after-audio-ms", type=float)
    parser.add_argument("--max-source-wer", type=float)
    parser.add_argument("--max-target-wer", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
