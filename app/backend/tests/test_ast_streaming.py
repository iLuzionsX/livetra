import sys
from types import SimpleNamespace
from types import ModuleType
import asyncio

import numpy as np
import pytest

from mlx_worker import (
    ASTResult,
    AST_MAX_AUDIO_SECONDS,
    MLXWorker,
    MLXWorkerService,
    _generation_was_truncated,
    _parse_ast_response,
)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("I cannot go\nSpanish: No puedo ir", ("I cannot go", "No puedo ir")),
        (
            "English: I cannot carry nine boxes\n**Translation (Spanish):** "
            "No puedo llevar nueve cajas",
            ("I cannot carry nine boxes", "No puedo llevar nueve cajas"),
        ),
        ("One two three\nSpanish - Uno dos tres", ("One two three", "Uno dos tres")),
        ("One two\nSpanish\nUno dos", ("One two", "Uno dos")),
    ],
)
def test_ast_parser_accepts_small_label_format_variants(response, expected):
    assert _parse_ast_response(response, "Spanish", "English") == expected


def test_ast_parser_does_not_treat_language_prefix_words_as_labels():
    assert _parse_ast_response("Spanish-speaking people arrived.", "Spanish") == (
        "Spanish-speaking people arrived.",
        "",
    )


def test_generation_completion_uses_token_limit_and_finish_reason():
    assert _generation_was_truncated(
        SimpleNamespace(generation_tokens=384, finish_reason="stop"), 384
    )
    assert _generation_was_truncated(
        SimpleNamespace(generation_tokens=20, finish_reason="length"), 384
    )
    assert not _generation_was_truncated(
        SimpleNamespace(generation_tokens=383, finish_reason="stop"), 384
    )


def _worker(tmp_path):
    worker = object.__new__(MLXWorker)
    worker._temp_wav_root = tmp_path
    worker.model = worker.processor = worker.config = object()
    return worker


def _patch_mlx(monkeypatch, streams):
    budgets = []
    stream_iterator = iter(streams)

    mlx_vlm = ModuleType("mlx_vlm")
    prompt_utils = ModuleType("mlx_vlm.prompt_utils")

    def stream_generate(*args, **kwargs):
        budgets.append(kwargs["max_tokens"])
        return iter(next(stream_iterator))

    mlx_vlm.stream_generate = stream_generate
    prompt_utils.apply_chat_template = lambda *args, **kwargs: "formatted"
    mlx_vlm.prompt_utils = prompt_utils
    monkeypatch.setitem(sys.modules, "mlx_vlm", mlx_vlm)
    monkeypatch.setitem(sys.modules, "mlx_vlm.prompt_utils", prompt_utils)
    return budgets


def test_ast_streams_source_then_translation_and_returns_complete_result(
    monkeypatch, tmp_path
):
    pieces = [
        SimpleNamespace(text="We cannot go", generation_tokens=3, finish_reason=None),
        SimpleNamespace(
            text="\nSpanish: No podemos ir",
            generation_tokens=9,
            finish_reason="stop",
        ),
    ]
    budgets = _patch_mlx(monkeypatch, [pieces])
    progress = []
    result = _worker(tmp_path).ast(
        np.ones(16_000, dtype=np.float32),
        "English",
        "Spanish",
        prior_context=[],
        max_tokens=192,
        priority="partial",
        on_progress=progress.append,
    )

    assert result == ASTResult("We cannot go", "No podemos ir", True, False)
    assert budgets == [192]
    assert progress == ["We cannot go", "We cannot go\nSpanish: No podemos ir"]


def test_truncated_final_retries_once_with_bounded_budget(monkeypatch, tmp_path):
    first = [
        SimpleNamespace(
            text="We cannot go\nSpanish: No podemos ir",
            generation_tokens=100,
            finish_reason="length",
        )
    ]
    second = [
        SimpleNamespace(
            text="We cannot go\nSpanish: No podemos ir",
            generation_tokens=20,
            finish_reason="stop",
        )
    ]
    budgets = _patch_mlx(monkeypatch, [first, second])
    result = _worker(tmp_path).ast(
        np.ones(16_000, dtype=np.float32),
        "English",
        "Spanish",
        prior_context=[],
        max_tokens=100,
        priority="final",
    )

    assert result == ASTResult("We cannot go", "No podemos ir", True, False)
    assert budgets == [100, 200]


def test_partial_ast_cancellation_closes_stream_and_returns_no_result(monkeypatch, tmp_path):
    cancelled = [False]
    closed = []

    def stream_generate(*args, **kwargs):
        try:
            yield SimpleNamespace(text="We can", generation_tokens=1)
            cancelled[0] = True
            yield SimpleNamespace(text=" go", generation_tokens=2)
        finally:
            closed.append(True)

    _patch_mlx(monkeypatch, [])
    mlx_vlm = sys.modules["mlx_vlm"]
    mlx_vlm.stream_generate = stream_generate
    result = _worker(tmp_path).ast(
        np.ones(16_000, dtype=np.float32),
        "English",
        "Spanish",
        prior_context=[],
        max_tokens=192,
        priority="partial",
        cancelled=lambda: cancelled[0],
    )

    assert result is None
    assert closed == [True]


def test_ast_rejects_audio_over_documented_limit_instead_of_trimming(tmp_path):
    worker = _worker(tmp_path)
    with pytest.raises(ValueError, match="30-second clip limit"):
        worker.ast(
            np.zeros((AST_MAX_AUDIO_SECONDS + 1) * 16_000, dtype=np.float32),
            "English",
            "Spanish",
            prior_context=[],
        )


def test_finish_partials_cancels_active_combined_ast_preview_only_for_same_utterance():
    worker = MLXWorkerService()
    worker._active_job = SimpleNamespace(
        kind="ast", sequence=17, payload={"priority": "partial", "utterance_id": 4}
    )

    worker.finish_partials(3)
    assert worker._cancelled_job_id.value == -1
    worker.finish_partials(4)
    assert worker._cancelled_job_id.value == 17

    worker._active_job = SimpleNamespace(
        kind="ast", sequence=18, payload={"priority": "final", "utterance_id": 5}
    )
    worker.finish_partials(5)
    assert worker._cancelled_job_id.value == 17


def test_service_forwards_streamed_ast_progress_before_final_result():
    class ResponseQueue:
        responses = iter(
            [
                {"type": "progress", "job_id": 12, "text": "We cannot"},
                {"type": "result", "job_id": 12, "result": "complete"},
            ]
        )

        def get(self, *args, **kwargs):
            return next(self.responses)

    async def run():
        worker = MLXWorkerService()
        worker._response_queue = ResponseQueue()
        worker._process = SimpleNamespace(is_alive=lambda: True)
        progress = []

        async def on_progress(text):
            progress.append(text)

        response = await worker._wait_for_worker_response(
            SimpleNamespace(sequence=12, on_progress=on_progress),
            timeout_seconds=1.0,
        )
        assert response == {"type": "result", "job_id": 12, "result": "complete"}
        assert progress == ["We cannot"]

    asyncio.run(run())
