import asyncio
import os
import time
from unittest.mock import AsyncMock

import mlx_worker
from mlx_worker import MLXWorkerService


def delayed_worker(requests, responses, temp_root, cancelled):
    time.sleep(0.5)
    responses.put({"type": "ready"})
    requests.get()


def crashed_worker(requests, responses, temp_root, cancelled):
    os._exit(4)


def test_cancelled_start_reaps_child_before_restart(monkeypatch):
    monkeypatch.setattr(mlx_worker, "_worker_process_main", delayed_worker)

    async def run():
        worker = MLXWorkerService()
        first = asyncio.create_task(worker.start())
        while worker._process is None:
            await asyncio.sleep(0.01)
        abandoned = worker._process
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        assert not abandoned.is_alive()
        assert worker._process is None
        assert not worker._started
        try:
            await asyncio.wait_for(worker.start(), 10)
            assert worker.status.state == "ready"
            assert worker._process.pid != abandoned.pid
        finally:
            await worker.stop()

    asyncio.run(run())


def test_worker_exit_during_load_reports_failure_promptly(monkeypatch):
    monkeypatch.setattr(mlx_worker, "_worker_process_main", crashed_worker)

    async def run():
        worker = MLXWorkerService()
        try:
            await asyncio.wait_for(worker.start(), 10)
        except RuntimeError as exc:
            assert "exited while loading" in str(exc)
        else:
            raise AssertionError("Worker crash was not reported")
        assert worker.status.state == "failed"
        assert worker._process is None

    asyncio.run(run())


def test_stop_waits_for_start_before_unloading(monkeypatch):
    async def run():
        worker = MLXWorkerService()
        loading = asyncio.Event()
        release = asyncio.Event()

        async def load():
            loading.set()
            await release.wait()

        monkeypatch.setattr(worker, "_start_worker_process", load)
        stop_process = AsyncMock()
        monkeypatch.setattr(worker, "_stop_worker_process", stop_process)
        start = asyncio.create_task(worker.start())
        await loading.wait()
        stop = asyncio.create_task(worker.stop())
        await asyncio.sleep(0)
        stop_process.assert_not_awaited()
        release.set()
        await asyncio.gather(start, stop)
        stop_process.assert_awaited_once()
        assert not worker._started
        assert worker._runner.done()

    asyncio.run(run())
