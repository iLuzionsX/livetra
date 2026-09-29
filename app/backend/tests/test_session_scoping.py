"""Worker bookkeeping is scoped to a session, and routing stays out of the worker.

Utterance ids restart at 1 in every session, so retirement keys that ignore the
session drop a reconnected client's previews unheard. The dispatch test pins the
other half of the same boundary: the payload crossing into the worker process
must only carry arguments `worker.ast()` accepts, or the call fails inside the
process and reads as a crash and a reload on every decode.
"""

import asyncio
from types import SimpleNamespace

import numpy as np

from mlx_worker import MLXWorker, MLXWorkerService


def submit_kwargs(**overrides):
    args = {
        "priority": "partial",
        "utterance_id": 1,
        "audio_f32_16k": np.zeros(16_000, dtype=np.float32),
        "src": "English",
        "tgt": "Spanish",
        "prior_context": [],
        "custom_vocab": [],
        "code_switching_enabled": False,
        "max_tokens": 192,
    }
    args.update(overrides)
    return args


def test_retired_ids_do_not_leak_across_sessions():
    async def run():
        worker = MLXWorkerService()
        # Session A commits utterance 1: its previews are retired.
        worker.finish_partials(1, "session-a")
        # Session B says utterance 1 for the first time: nothing about A applies.
        assert ("session-b", 1) not in worker._final_utterance_ids
        # And A's own utterance really is retired.
        assert await worker.submit_ast(**submit_kwargs(session_id="session-a")) is None

    asyncio.run(run())


def test_release_session_forgets_retired_ids():
    worker = MLXWorkerService()
    worker.finish_partials(1, "session-a")
    worker.finish_partials(2, "session-b")
    worker.release_session("session-a")
    assert ("session-a", 1) not in worker._final_utterance_ids
    assert ("session-b", 2) in worker._final_utterance_ids


def test_finish_partials_only_cancels_the_same_session():
    worker = MLXWorkerService()
    worker._active_job = SimpleNamespace(
        kind="ast",
        sequence=17,
        payload={
            "priority": "partial",
            "utterance_id": 4,
            "session_id": "session-a",
        },
    )
    worker.finish_partials(4, "session-b")
    assert worker._cancelled_job_id.value == -1
    worker.finish_partials(4, "session-a")
    assert worker._cancelled_job_id.value == 17


def test_dispatched_ast_payload_only_carries_worker_arguments():
    async def run():
        import inspect

        from mlx_worker import _QueuedJob

        worker = MLXWorkerService()
        sent = []
        worker._request_queue = SimpleNamespace(put=sent.append)
        worker._response_queue = SimpleNamespace()
        worker._process = SimpleNamespace(is_alive=lambda: True)

        async def fake_wait(job, timeout_seconds):
            return {"type": "result", "job_id": job.sequence, "result": None}

        worker._wait_for_worker_response = fake_wait
        loop = asyncio.get_running_loop()
        job = _QueuedJob(
            priority=1,
            sequence=1,
            kind="ast",
            future=loop.create_future(),
            payload={
                "priority": "partial",
                "utterance_id": 1,
                "session_id": "session-a",
                "audio_f32_16k": np.zeros(16_000, dtype=np.float32),
                "src": "English",
                "tgt": "Spanish",
                "prior_context": [],
                "custom_vocab": [],
                "code_switching_enabled": False,
                "max_tokens": 192,
            },
        )
        await worker._dispatch_job(job, 1.0)

        worker_params = set(inspect.signature(MLXWorker.ast).parameters) - {
            "self",
            "cancelled",
            "on_progress",
        }
        assert set(sent[0]["payload"]) <= worker_params

    asyncio.run(run())
