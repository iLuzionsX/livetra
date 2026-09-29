import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from mlx_worker import ASTResult, MLXWorkerService
from protocol import ConfigMessage
from segmenter import RMSGate
from session import SessionHub, TranscriptionSession


def make_session(monkeypatch):
    monkeypatch.setattr(TranscriptionSession, "_build_segmenter", lambda *_: RMSGate())
    value = TranscriptionSession(SimpleNamespace(query_params={}), Mock(), SessionHub())
    value._send = AsyncMock()
    value._send_and_broadcast = AsyncMock()
    return value


@pytest.mark.parametrize("source,code_switching", [("English", False), ("Japanese", False), ("Spanish", True)])
def test_every_session_routes_audio_through_combined_gemma(monkeypatch, source, code_switching):
    async def run():
        value = make_session(monkeypatch)
        value.state.config = ConfigMessage(source_lang=source, code_switching_enabled=code_switching)
        value._run_mlx_ast = AsyncMock()
        audio = np.ones(320, dtype=np.float32)
        await value._run_ast("partial", 1, audio)
        value._run_mlx_ast.assert_awaited_once_with("partial", 1, audio)

    asyncio.run(run())


def test_final_ast_precedes_other_utterance_preview_and_retires_its_own():
    async def run():
        worker = MLXWorkerService()

        async def submit(priority, utterance_id):
            return await worker.submit_ast(
                priority=priority, utterance_id=utterance_id,
                audio_f32_16k=np.ones(320, dtype=np.float32), src="English", tgt="Spanish",
                prior_context=[], custom_vocab=[], code_switching_enabled=False, max_tokens=192,
            )

        retired = asyncio.create_task(submit("partial", 1))
        await asyncio.sleep(0)
        worker.finish_partials(1)
        assert await retired is None
        assert await submit("partial", 1) is None
        preview = asyncio.create_task(submit("partial", 2))
        final = asyncio.create_task(submit("final", 1))
        await asyncio.sleep(0)
        queued = worker._queue.get_nowait()
        assert queued.payload["priority"] == "final"
        assert queued.payload["utterance_id"] == 1
        preview.cancel()
        final.cancel()
        await asyncio.gather(preview, final, return_exceptions=True)

    asyncio.run(run())


def test_archive_save_keeps_legacy_profile_untouched(monkeypatch, tmp_path):
    async def run():
        import session

        profile = tmp_path / "learning_profile.json"
        original = '{"asr_corrections": [["ninety", "nineteen"]], "custom": {"keep": true}}'
        profile.write_text(original)
        monkeypatch.setattr(session, "LEARNING_PROFILE_PATH", profile)
        value = make_session(monkeypatch)
        value._load_global_learning_profile()
        value.state.config.custom_vocab = ["Northstar Chapel"]
        value._archive_dir = tmp_path / "archive"
        value._archive_dir.mkdir()
        value._archive_started_at = datetime.now().astimezone()
        await value._write_archive_snapshot()
        assert profile.read_text() == original
        metadata = json.loads((value._archive_dir / "meta.json").read_text())
        assert metadata["asr_corrections"] == [["ninety", "nineteen"]]
        assert metadata["config"]["custom_vocab"] == ["Northstar Chapel"]
        snapshot = value.export_session_snapshot()
        restored = make_session(monkeypatch)
        await restored._restore_from_snapshot(snapshot)
        assert restored.state.asr_corrections == [("ninety", "nineteen")]

    asyncio.run(run())


def test_gemma_health_uses_actual_worker_state_without_alternate_dependency(monkeypatch):
    import server

    monkeypatch.setattr(server, "worker", SimpleNamespace(status=SimpleNamespace(state="starting")))
    health = asyncio.run(server.health())
    assert health["transcription_engine"] == "gemma"
    assert health["asr_model"] == health["translation_model"]
    assert health["asr_state"] == health["translation_state"] == "starting"


@pytest.mark.parametrize("complete", [True, False])
def test_smoke_cli_handles_structured_result(monkeypatch, capsys, complete):
    import smoke_ast

    monkeypatch.setattr("sys.argv", ["smoke_ast", "example.wav"])
    monkeypatch.setattr(smoke_ast, "read_wav_16k_mono", lambda _: np.ones(320, dtype=np.float32))
    monkeypatch.setattr(smoke_ast, "MLXWorker", lambda: SimpleNamespace(
        ast=lambda *args, **kwargs: ASTResult("We cannot go", "No podemos ir", complete, not complete)
    ))
    if complete:
        smoke_ast.main()
        assert capsys.readouterr().out == "We cannot go\nSpanish: No podemos ir\n"
    else:
        with pytest.raises(SystemExit, match="complete transcription"):
            smoke_ast.main()
        assert capsys.readouterr().out == ""
