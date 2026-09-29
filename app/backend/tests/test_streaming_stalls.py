import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from mlx_worker import ASTResult
from segmenter import RMSGate
from session import SessionHub, TranscriptionSession


def make_session(monkeypatch):
    monkeypatch.setattr(TranscriptionSession, "_build_segmenter", lambda *_: RMSGate())
    value = TranscriptionSession(
        SimpleNamespace(query_params={}), Mock(), SessionHub(),
    )
    value._send = AsyncMock()
    value._send_and_broadcast = AsyncMock()
    return value


def test_pending_final_does_not_block_next_utterance_partials(monkeypatch):
    async def run():
        value = make_session(monkeypatch)
        value.state.utterance_id = 1
        value._finalizing[1] = "silero_end"
        value._schedule_ast = Mock()
        for _ in range(20):
            await value._receive_frame(np.full(320, 0.1, dtype=np.float32))
        assert value.state.active_utterance_id == 2
        assert any(call.args[:2] == ("partial", 2) for call in value._schedule_ast.call_args_list)
    asyncio.run(run())


@pytest.mark.parametrize("result", [None, ASTResult("", "", False, False), TimeoutError("slow model")])
def test_empty_or_failed_final_releases_pending_commit(monkeypatch, result):
    async def run():
        value = make_session(monkeypatch)
        value._finalizing[1] = "silero_end"
        if isinstance(result, Exception):
            value.worker.submit_ast = AsyncMock(side_effect=result)
        else:
            value.worker.submit_ast = AsyncMock(return_value=result)
        await value._run_ast("final", 1, np.ones(16000, dtype=np.float32))
        assert 1 not in value._finalizing
    asyncio.run(run())
