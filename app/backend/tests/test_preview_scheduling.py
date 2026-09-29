import asyncio
from types import SimpleNamespace

import numpy as np
from unittest.mock import AsyncMock

from mlx_worker import MLXWorkerService
from protocol import ConfigMessage
from session import TranscriptionSession, UtteranceRuntime


def make_session(*, interval=0.25, turnaround=0.0):
    value = object.__new__(TranscriptionSession)
    value.state = SimpleNamespace(
        config=ConfigMessage(partial_interval_seconds=interval),
        active_utterance_id=7,
    )
    value._utterance_runtime = {
        7: UtteranceRuntime(slowest_partial_turnaround_seconds=turnaround)
    }
    value._finalizing = {}
    value._finalized = set()
    return value


def test_partial_interval_tracks_turnaround_but_respects_configured_floor():
    session = make_session(interval=0.75, turnaround=2.4)
    assert session._partial_interval_seconds() == 2.4

    session.state.config.partial_interval_seconds = 2.8
    assert session._partial_interval_seconds() == 2.8

    session._utterance_runtime[7].slowest_partial_turnaround_seconds = 8.0
    assert session._partial_interval_seconds() == 3.0


def test_partial_preview_waits_for_both_cadence_and_new_speech(monkeypatch):
    session = make_session(turnaround=1.5)
    runtime = session._utterance_runtime[7]
    runtime.last_partial_wall_seconds = 99.0
    runtime.last_partial_audio_samples = 16_000
    runtime.voiced_audio_samples = 19_200
    now = [100.0]
    monkeypatch.setattr("session.time", SimpleNamespace(monotonic=lambda: now[0]))

    assert not session._active_utterance_has_new_speech_for_partial()
    now[0] = 101.0
    runtime.voiced_audio_samples = 17_600
    assert not session._active_utterance_has_new_speech_for_partial()

    runtime.voiced_audio_samples = 19_200
    assert session._active_utterance_has_new_speech_for_partial()


def test_scheduled_preview_records_turnaround_for_adaptive_cadence(monkeypatch):
    async def run():
        session = make_session()
        session._run_ast = AsyncMock()
        times = iter([10.0, 12.25])
        monkeypatch.setattr(
            "session.time", SimpleNamespace(monotonic=lambda: next(times))
        )

        await session._run_scheduled_partial_ast(
            "partial", 7, np.zeros(16000, dtype=np.float32)
        )

        assert session._utterance_runtime[7].slowest_partial_turnaround_seconds == 2.25
        assert session._partial_interval_seconds() == 2.25

    asyncio.run(run())


def test_queued_ast_previews_coalesce_and_final_keeps_priority():
    async def run():
        worker = MLXWorkerService()

        async def submit(priority, audio):
            return await worker.submit_ast(
                priority=priority,
                utterance_id=7,
                audio_f32_16k=np.full(16000, audio, dtype=np.float32),
                src="English",
                tgt="Spanish",
                prior_context=[],
                custom_vocab=[],
                code_switching_enabled=False,
                max_tokens=128,
            )

        first_preview = asyncio.create_task(submit("partial", 0.1))
        await asyncio.sleep(0)
        latest_preview = asyncio.create_task(submit("partial", 0.2))
        await asyncio.sleep(0)

        assert await first_preview is None
        queued_preview = worker._queued_partial_jobs[7]
        np.testing.assert_array_equal(
            queued_preview.payload["audio_f32_16k"],
            np.full(16000, 0.2, dtype=np.float32),
        )

        final = asyncio.create_task(submit("final", 0.3))
        await asyncio.sleep(0)
        assert await latest_preview is None
        assert worker._queue.get_nowait().payload["priority"] == "final"

        final.cancel()
        await asyncio.gather(final, return_exceptions=True)
        assert await submit("partial", 0.4) is None

    asyncio.run(run())
