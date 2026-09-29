from types import SimpleNamespace

from protocol import ConfigMessage
from session import TranscriptionSession, UtteranceRuntime


def session(source="English", code_switching=False):
    value = object.__new__(TranscriptionSession)
    value.state = SimpleNamespace(config=ConfigMessage(
        source_lang=source, code_switching_enabled=code_switching))
    return value


def test_gemma_replaces_partial_instead_of_appending_or_retranslating():
    import asyncio
    from unittest.mock import AsyncMock
    import numpy as np
    from mlx_worker import ASTResult

    async def run():
        value = session()
        value.worker = SimpleNamespace(
            submit_ast=AsyncMock(
                return_value=ASTResult("We cannot go", "No podemos ir", True, False)
            )
        )
        value.state.prior_context = []
        value._finalized = set()
        value._finalizing = {}
        value._utterance_runtime = {1: UtteranceRuntime(
            latest_partial_original="We can go", latest_partial_translation="Podemos ir")}
        value._send_and_broadcast = AsyncMock()
        value._maybe_commit_early = AsyncMock()
        await value._run_mlx_ast("partial", 1, np.zeros(16000, dtype=np.float32))
        payload = value._send_and_broadcast.call_args.args[0]
        assert payload["original"] == "We cannot go"
        assert payload["translation"] == "No podemos ir"
        args = value.worker.submit_ast.call_args.kwargs
        assert args["custom_vocab"] == value.state.config.custom_vocab
        assert args["prior_context"] == []

    asyncio.run(run())


def test_ast_budgets_leave_translation_headroom():
    import numpy as np

    value = session()
    assert value._max_tokens_for_ast("partial", np.zeros(29 * 16000)) == 192
    assert value._max_tokens_for_ast("final", np.zeros(8 * 16000)) == 384
    assert value._max_tokens_for_ast("final", np.zeros(12 * 16000)) == 512
    assert value._max_tokens_for_ast("final", np.zeros(29 * 16000)) == 640


def test_streamed_final_progress_stays_partial_when_result_is_incomplete():
    import asyncio
    from unittest.mock import AsyncMock
    import numpy as np
    from mlx_worker import ASTResult

    async def run():
        value = session()
        value.state.config.custom_vocab = ["Northstar Chapel"]
        value.state.prior_context = []
        value._finalized = set()
        value._finalizing = {1: "silero_end"}
        value._utterance_runtime = {1: UtteranceRuntime()}
        value._send_and_broadcast = AsyncMock()
        value._send_error = AsyncMock()

        async def submit_ast(**kwargs):
            await kwargs["on_progress"]("We cannot go\nSpanish: No podemos")
            return ASTResult("We cannot go", "No podemos", False, True)

        value.worker = SimpleNamespace(submit_ast=AsyncMock(side_effect=submit_ast))
        await value._run_mlx_ast("final", 1, np.ones(16000, dtype=np.float32))

        messages = [call.args[0] for call in value._send_and_broadcast.await_args_list]
        assert [message["type"] for message in messages] == ["partial"]
        assert messages[0]["original"] == "We cannot go"
        assert messages[0]["translation"] == "No podemos"
        assert value._send_error.await_args.args[0].startswith(
            "Final inference remained incomplete"
        )
        args = value.worker.submit_ast.call_args.kwargs
        assert args["custom_vocab"] == ["Northstar Chapel"]
        assert args["prior_context"] == []

    asyncio.run(run())
