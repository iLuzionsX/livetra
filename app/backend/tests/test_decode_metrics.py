"""Decode accounting that decides whether the final pass is worth its cost."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from mlx_worker import ASTResult, DecodeStats
from protocol import ConfigMessage
from session import (
    DecodeLedger,
    TranscriptionSession,
    UtteranceRuntime,
    _voiced_signature,
)

# Loud enough to survive the transcribable-energy and trim thresholds.
SPEECH = np.full(16_000, 0.2, dtype=np.float32)


def complete_result(inference_seconds: float = 1.0, generated_tokens: int = 24):
    return ASTResult(
        "We cannot go",
        "No podemos ir",
        True,
        False,
        DecodeStats(
            audio_seconds=1.0,
            max_tokens=384,
            attempts=1,
            generated_tokens=generated_tokens,
            inference_seconds=inference_seconds,
            complete=True,
        ),
    )


def install_worker(value, result):
    """Emulate the worker service, which always reports stats for a finished job."""

    async def submit_ast(**kwargs):
        if result is not None and result.stats is not None:
            kwargs["on_stats"](result.stats)
        return result

    value.worker.submit_ast = AsyncMock(side_effect=submit_ast)


def make_session():
    value = object.__new__(TranscriptionSession)
    value.session_id = "test-session"
    value.state = SimpleNamespace(
        config=ConfigMessage(source_lang="English"),
        prior_context=[],
        utterances_since_maintenance=0,
        active_utterance_id=1,
    )
    value._decode_ledger = DecodeLedger()
    value._utterance_runtime = {1: UtteranceRuntime()}
    value._finalized = set()
    value._finalizing = {}
    value._finalize_lock = asyncio.Lock()
    value.worker = SimpleNamespace(finish_partials=lambda utterance_id, session_id=None: None)
    value.segmenter = SimpleNamespace(reset=lambda: None)
    value._send_and_broadcast = AsyncMock()
    value._maybe_commit_early = AsyncMock()
    value._maybe_run_maintenance = AsyncMock()
    value._skip_next_polish = False
    value._schedule_ast = lambda *args, **kwargs: None
    return value


def test_completed_preview_is_recorded_as_reusable_final_input():
    async def run():
        value = make_session()
        install_worker(value, complete_result())
        await value._run_mlx_ast("partial", 1, SPEECH)

        decode = value._decode_ledger.decodes[0]
        assert decode["priority"] == "partial"
        assert decode["outcome"] == "partial"
        assert decode["generated_tokens"] == 24
        runtime = value._utterance_runtime[1]
        assert runtime.partials_completed == 1
        assert runtime.last_complete_partial_voiced == _voiced_signature(SPEECH)

    asyncio.run(run())


def test_commit_matching_a_completed_preview_is_flagged_as_redundant():
    async def run():
        value = make_session()
        install_worker(value, complete_result())
        await value._run_mlx_ast("partial", 1, SPEECH)

        # Trailing silence only: no new speech since that preview decoded, so the
        # final pass repeats a decode that already happened.
        trailing_silence = np.concatenate([SPEECH, np.zeros(8_000, dtype=np.float32)])
        assert await value._commit_utterance(
            1, trailing_silence, reason="silero_end", reset_segmenter=True
        )
        commit = value._decode_ledger.commits[0]
        assert commit["matched_complete_partial"] is True
        assert commit["voiced_seconds"] == pytest.approx(1.0)

        install_worker(value, complete_result(inference_seconds=3.0))
        await value._run_mlx_ast("final", 1, trailing_silence)

        redundancy = value._decode_ledger.summary()["final_redundancy"]
        assert redundancy["matched_percent"] == 100.0
        assert redundancy["redundant_finals"] == 1
        assert redundancy["redundant_final_inference_seconds"] == pytest.approx(3.0)

    asyncio.run(run())


def test_commit_after_new_speech_is_not_flagged_as_redundant():
    async def run():
        value = make_session()
        install_worker(value, complete_result())
        await value._run_mlx_ast("partial", 1, SPEECH)

        # The speaker kept talking, so the final decodes audio no preview covered.
        longer = np.concatenate([SPEECH, np.full(16_000, 0.2, dtype=np.float32)])
        await value._commit_utterance(
            1, longer, reason="silero_end", reset_segmenter=True
        )
        install_worker(value, complete_result(inference_seconds=3.0))
        await value._run_mlx_ast("final", 1, longer)

        redundancy = value._decode_ledger.summary()["final_redundancy"]
        assert redundancy["matched_percent"] == 0.0
        assert redundancy["redundant_finals"] == 0

    asyncio.run(run())


def test_preview_retired_without_a_result_falls_back_to_wall_time():
    async def run():
        value = make_session()
        # A worker that reports nothing: wall time stands in for the cost.
        value.worker.submit_ast = AsyncMock(return_value=None)
        await value._run_mlx_ast("partial", 1, SPEECH)

        decode = value._decode_ledger.decodes[0]
        assert decode["outcome"] == "cancelled"
        assert decode["cancelled"] is True
        assert decode["measured_inference"] is False

    asyncio.run(run())


def test_reported_stats_replace_the_wall_time_fallback():
    async def run():
        value = make_session()

        async def submit_ast(**kwargs):
            on_stats = kwargs["on_stats"]
            on_stats(
                DecodeStats(
                    audio_seconds=1.0,
                    max_tokens=192,
                    inference_seconds=0.42,
                    queue_seconds=0.31,
                    cancelled=True,
                )
            )
            return None

        value.worker.submit_ast = AsyncMock(side_effect=submit_ast)
        await value._run_mlx_ast("partial", 1, SPEECH)

        decode = value._decode_ledger.decodes[0]
        # A cancelled preview's GPU time must be visible, not inferred.
        assert decode["measured_inference"] is True
        assert decode["inference_seconds"] == pytest.approx(0.42)
        assert decode["cancelled"] is True

    asyncio.run(run())


def test_summary_reports_the_partial_versus_final_compute_split():
    async def run():
        value = make_session()
        install_worker(value, complete_result(1.0))
        await value._run_mlx_ast("partial", 1, SPEECH)
        install_worker(value, complete_result(inference_seconds=3.0))
        await value._run_mlx_ast("final", 1, SPEECH)

        summary = value._decode_ledger.summary()
        assert summary["decode_count"] == 2
        by_priority = summary["by_priority"]
        assert by_priority["partial"]["inference_share_percent"] == pytest.approx(25.0)
        assert by_priority["final"]["inference_share_percent"] == pytest.approx(75.0)
        assert summary["by_priority"]["final"]["generated_tokens"] == 24

    asyncio.run(run())


def test_voiced_signature_ignores_trailing_silence_but_not_new_speech():
    assert _voiced_signature(SPEECH) == _voiced_signature(SPEECH.copy())
    # Trailing silence lengthens the clip without changing what was said.
    assert _voiced_signature(SPEECH) == _voiced_signature(
        np.concatenate([SPEECH, np.zeros(8_000, dtype=np.float32)])
    )
    # New speech is new content.
    longer = np.concatenate([SPEECH, np.full(16_000, 0.2, dtype=np.float32)])
    assert _voiced_signature(SPEECH) != _voiced_signature(longer)
    # Same length, different content, must not collide.
    assert _voiced_signature(SPEECH) != _voiced_signature(SPEECH * 0.5)
    assert _voiced_signature(np.zeros(16_000, dtype=np.float32)) is None
