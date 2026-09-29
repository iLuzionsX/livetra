from __future__ import annotations

import asyncio
import itertools
import logging
import multiprocessing as mp
import os
import queue
import re
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Literal

import numpy as np
import soundfile as sf
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("uvicorn.error")

MODEL_PATH = os.getenv("MODEL_PATH", "mlx-community/gemma-4-e4b-it-8bit")
TEMP_WAV_ROOT = Path(
    os.getenv("LIVETR3_TEMP_WAV_ROOT", os.path.join(tempfile.gettempdir(), "livetr3-mlx"))
)
TEMP_WAV_STALE_SECONDS = max(60, int(os.getenv("TEMP_WAV_STALE_SECONDS", "1800")))
TEMP_WAV_SWEEP_INTERVAL_SECONDS = max(
    30, int(os.getenv("TEMP_WAV_SWEEP_INTERVAL_SECONDS", "300"))
)
PARTIAL_TIMEOUT_SECONDS = max(1.0, float(os.getenv("PARTIAL_TIMEOUT_SECONDS", "8")))
FINAL_TIMEOUT_SECONDS = max(1.0, float(os.getenv("FINAL_TIMEOUT_SECONDS", "45")))
POLISH_TIMEOUT_SECONDS = max(1.0, float(os.getenv("POLISH_TIMEOUT_SECONDS", "10")))
MAINTENANCE_TIMEOUT_SECONDS = max(1.0, float(os.getenv("MAINTENANCE_TIMEOUT_SECONDS", "5")))
AST_MAX_AUDIO_SECONDS = 30
MLX_WORKER_START_TIMEOUT_SECONDS = max(
    10.0, float(os.getenv("MLX_WORKER_START_TIMEOUT_SECONDS", "180"))
)
MLX_WORKER_RECOVERY_BACKOFF_SECONDS = max(
    0.0, float(os.getenv("MLX_WORKER_RECOVERY_BACKOFF_SECONDS", "2"))
)

AST_PROMPT = (
    "Transcribe the following speech segment in {src} into {src} text, "
    "then translate it into {tgt}. Transcribe exactly as spoken and preserve "
    "every word, especially negation and quantities. Do not add, omit, or infer words. "
    "When formatting the answer, "
    "first output the transcription in {src}, then one newline, "
    "then output the string '{tgt}: ', then the translation in {tgt}."
)

POLISH_PROMPT = (
    "You will receive a rough transcription. Remove filler words "
    "(um, uh, er, you know, like), fix punctuation, fix capitalization, "
    "and keep the exact meaning and wording. Return ONLY the cleaned text "
    "with no preamble.\n\nTranscription: {text}"
)


@dataclass(slots=True)
class DecodeStats:
    """Cost of one AST decode. Mutable and module-level so it pickles across the process boundary."""

    audio_seconds: float = 0.0
    max_tokens: int = 0
    attempts: int = 0
    generated_tokens: int = 0
    inference_seconds: float = 0.0
    queue_seconds: float = 0.0
    complete: bool = False
    truncated: bool = False
    cancelled: bool = False


@dataclass(slots=True, frozen=True)
class ASTResult:
    original: str
    translation: str
    complete: bool
    truncated: bool
    stats: DecodeStats | None = None

class MLXWorker:
    def __init__(self, temp_wav_root: Path | None = None) -> None:
        from mlx_vlm import load

        self._temp_wav_root = temp_wav_root or TEMP_WAV_ROOT / f"gemma-{os.getpid()}"
        self._temp_wav_root.mkdir(parents=True, exist_ok=True)
        sweep_stale_temp_wavs(self._temp_wav_root, TEMP_WAV_STALE_SECONDS)
        self.model, self.processor = load(MODEL_PATH)
        self.config = self.model.config
        self._warmup()

    def _warmup(self) -> None:
        silent = np.zeros(16_000, dtype=np.float32)
        self.ast(
            silent,
            "English",
            "Spanish",
            prior_context=[],
            max_tokens=8,
            priority="partial",
        )

    def ast(
        self,
        audio_f32_16k: np.ndarray,
        src: str,
        tgt: str,
        prior_context: list[tuple[str, str]],
        custom_vocab: list[str] | None = None,
        code_switching_enabled: bool = False,
        max_tokens: int = 256,
        priority: Literal["partial", "final"] = "final",
        cancelled: Callable[[], bool] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> ASTResult | None:
        audio_f32_16k = np.asarray(audio_f32_16k, dtype=np.float32).reshape(-1)
        if audio_f32_16k.shape[0] > AST_MAX_AUDIO_SECONDS * 16_000:
            raise ValueError(
                f"Gemma AST audio exceeds the {AST_MAX_AUDIO_SECONDS}-second clip limit"
            )

        from mlx_vlm import stream_generate
        from mlx_vlm.prompt_utils import apply_chat_template

        fd, wav_path_raw = tempfile.mkstemp(
            suffix=".wav",
            prefix="ast-",
            dir=self._temp_wav_root,
        )
        os.close(fd)
        wav_path = Path(wav_path_raw)

        try:
            sf.write(wav_path, audio_f32_16k, 16_000, subtype="FLOAT")
            prompt_text = AST_PROMPT.format(src=src, tgt=tgt)
            if custom_vocab:
                vocabulary = [" ".join(item.split()) for item in custom_vocab if item.strip()]
                vocabulary = vocabulary[:12]
                prompt_text = (
                    f"Possible names or terms include: {', '.join(vocabulary)}. "
                    "Use a listed term only when the audio supports it; do not insert one "
                    "because it appears in this list.\n\n"
                    + prompt_text
                )
            if code_switching_enabled:
                prompt_text = (
                    f"Speaker may code-switch between {src} and {tgt}; "
                    f"transcribe in the spoken language, translate to {tgt}.\n\n"
                    + prompt_text
                )
            # mlx-vlm expands num_audios before the prompt text; do not hand-roll templates.
            formatted = apply_chat_template(
                self.processor,
                self.config,
                prompt_text,
                num_audios=1,
            )
            stats = DecodeStats(
                audio_seconds=float(audio_f32_16k.shape[0]) / 16_000,
                max_tokens=max_tokens,
            )
            started_at = time.perf_counter()

            def generate_once(token_budget: int) -> ASTResult | None:
                if cancelled is not None and cancelled():
                    stats.cancelled = True
                    return None
                stats.attempts += 1
                stream = stream_generate(
                    self.model,
                    self.processor,
                    formatted,
                    audio=[str(wav_path)],
                    max_tokens=token_budget,
                    temperature=0.0,
                    top_p=0.95,
                    top_k=64,
                )
                pieces: list[str] = []
                latest_response: object | None = None
                last_progress_text = ""
                try:
                    for response in stream:
                        if cancelled is not None and cancelled():
                            stats.cancelled = True
                            return None
                        latest_response = response
                        generated = getattr(response, "generation_tokens", None)
                        if isinstance(generated, int):
                            stats.generated_tokens = generated
                        piece = getattr(response, "text", None)
                        if isinstance(piece, str):
                            pieces.append(piece)
                        output = "".join(pieces)
                        if output and output != last_progress_text and on_progress is not None:
                            on_progress(output)
                            last_progress_text = output
                finally:
                    close = getattr(stream, "close", None)
                    if callable(close):
                        close()

                output = "".join(pieces)
                original, translation = _parse_ast_response(output, tgt, src)
                truncated = _generation_was_truncated(latest_response, token_budget)
                complete = bool(original and translation and not truncated)
                # Report the attempt that produced the returned text. A retry that
                # succeeds is not a truncated result; attempts counts the retry.
                stats.truncated = truncated
                stats.complete = complete
                return ASTResult(original, translation, complete, truncated, stats)

            try:
                result = generate_once(max_tokens)
                if result is None:
                    return None
                if priority == "final" and not result.complete:
                    retry_budget = min(768, max_tokens * 2)
                    if retry_budget > max_tokens:
                        result = generate_once(retry_budget)
                return result
            finally:
                stats.inference_seconds = time.perf_counter() - started_at
        finally:
            _safe_unlink(wav_path)

    def polish(self, text: str, max_tokens: int = 256) -> str:
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        prompt = POLISH_PROMPT.format(text=text)
        formatted = apply_chat_template(self.processor, self.config, prompt, num_audios=0)
        out = _generation_text(
            generate(
                self.model,
                self.processor,
                formatted,
                max_tokens=max_tokens,
                temperature=0.0,
                top_p=0.95,
                top_k=64,
                verbose=False,
            )
        )
        return out.strip()


    def clear_caches(self) -> None:
        import gc

        gc.collect()
        try:
            import mlx.core as mx

            mx.metal.clear_cache()
        except Exception:
            return


def _generation_text(result: object) -> str:
    if isinstance(result, str):
        return result
    text = getattr(result, "text", None)
    if isinstance(text, str):
        return text
    return str(result)


def _generation_was_truncated(result: object | None, max_tokens: int) -> bool:
    if result is None:
        return False
    finish_reason = getattr(result, "finish_reason", None)
    if isinstance(finish_reason, str) and finish_reason.casefold() in {
        "length",
        "max_tokens",
        "token_limit",
    }:
        return True
    generated = getattr(result, "generation_tokens", None)
    return isinstance(generated, int) and generated >= max_tokens


def _parse_ast_response(
    response: str,
    target_language: str,
    source_language: str | None = None,
) -> tuple[str, str]:
    """Parse Gemma's source-first output across minor label/markdown variations."""
    text = response.replace("\r\n", "\n").replace("\r", "\n").strip()
    for marker in ("<turn|>", "</s>", "<|end_of_turn|>"):
        text = text.replace(marker, "")
    lines = text.strip().splitlines()
    target = re.escape(target_language.strip())
    label = re.compile(
        rf"^\s*(?:[-*]\s*)?(?:\*{{1,2}}|__)?\s*"
        rf"(?:translation\s*\(\s*{target}\s*\)|{target})"
        rf"\s*(?:\*{{1,2}}|__)?\s*"
        rf"(?:(?::|：)\s*(?P<colon>.*)|\s+[-–—]\s+(?P<dash>.*))?\s*$",
        re.IGNORECASE,
    )
    for index, line in enumerate(lines):
        match = label.match(line)
        if match is None:
            continue
        source = "\n".join(lines[:index]).strip(" \n*`_")
        if source_language:
            source = re.sub(
                rf"^\s*(?:\*{{1,2}}|__)?\s*{re.escape(source_language.strip())}"
                rf"\s*(?:\*{{1,2}}|__)?\s*[:：-]\s*",
                "",
                source,
                count=1,
                flags=re.IGNORECASE,
            )
        first_translation_line = match.group("colon") or match.group("dash")
        translated_lines = (
            [first_translation_line]
            if first_translation_line and first_translation_line.strip()
            else []
        ) + lines[index + 1 :]
        translation = "\n".join(translated_lines).strip(" \n*`_")
        return source, translation

    source = "\n".join(lines).strip(" \n*`_")
    if source_language:
        source = re.sub(
            rf"^\s*(?:\*{{1,2}}|__)?\s*{re.escape(source_language.strip())}"
            rf"\s*(?:\*{{1,2}}|__)?\s*[:：-]\s*",
            "",
            source,
            count=1,
            flags=re.IGNORECASE,
        )
    return source, ""


class InferenceTimeoutError(RuntimeError):
    pass


class WorkerProcessError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class WorkerStatusEvent:
    state: Literal["starting", "ready", "recovering", "failed"]
    message: str


@dataclass(order=True)
class _QueuedJob:
    priority: int
    sequence: int
    kind: Literal["ast", "polish", "maintenance"] = field(compare=False)
    future: asyncio.Future = field(compare=False)
    payload: dict = field(compare=False)
    enqueued_at: float = field(default_factory=time.monotonic, compare=False)
    on_progress: Callable[[str], Awaitable[None]] | None = field(default=None, compare=False)
    on_stats: Callable[[DecodeStats], None] | None = field(default=None, compare=False)


class MLXWorkerService:
    """Async facade around one synchronous MLXWorker and one Metal context."""

    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue[_QueuedJob] = asyncio.PriorityQueue()
        self._sequence = itertools.count()
        self._runner: asyncio.Task | None = None
        self._sweeper: asyncio.Task | None = None
        self._start_lock = asyncio.Lock()
        self._started = False
        self._closed = asyncio.Event()
        self._temp_wav_root = TEMP_WAV_ROOT / f"gemma-{os.getpid()}"
        self._process: mp.Process | None = None
        self._request_queue: mp.Queue | None = None
        self._response_queue: mp.Queue | None = None
        self._active_job_kind: Literal["ast", "polish", "maintenance"] | None = None
        self._queued_partial_jobs: dict[int, _QueuedJob] = {}
        self._final_utterance_ids: set[int] = set()
        self._mp_context = mp.get_context("spawn")
        self._cancelled_job_id = self._mp_context.Value("q", -1)
        self._active_job: _QueuedJob | None = None
        self._status = WorkerStatusEvent(state="starting", message="Loading Gemma model worker")
        self._status_listeners: set[Callable[[WorkerStatusEvent], Awaitable[None]]] = set()

    async def start(self) -> None:
        async with self._start_lock:
            if self._started:
                return
            self._closed.clear()
            self._temp_wav_root.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(
                sweep_stale_temp_wavs, self._temp_wav_root, TEMP_WAV_STALE_SECONDS
            )
            try:
                await self._start_worker_process()
            except Exception as exc:
                await self._emit_status(
                    WorkerStatusEvent(state="failed", message=f"Model worker failed to start: {exc}")
                )
                raise
            self._runner = asyncio.create_task(self._run(), name="mlx-worker-queue")
            self._sweeper = asyncio.create_task(
                self._run_temp_wav_sweeper(), name="mlx-temp-wav-sweeper"
            )
            self._started = True

    async def stop(self) -> None:
        # Idle unloading must not tear down a worker while start() is loading it.
        async with self._start_lock:
            await self._stop()

    async def _stop(self) -> None:
        self._closed.set()
        if self._sweeper:
            self._sweeper.cancel()
            try:
                await self._sweeper
            except asyncio.CancelledError:
                pass
        if self._runner:
            self._runner.cancel()
            try:
                await self._runner
            except asyncio.CancelledError:
                pass
        await self._stop_worker_process(force=True)
        await asyncio.to_thread(sweep_stale_temp_wavs, self._temp_wav_root, 0)
        self._started = False

    async def submit_ast(
        self,
        *,
        priority: Literal["partial", "final"],
        utterance_id: int | None,
        audio_f32_16k: np.ndarray,
        src: str,
        tgt: str,
        prior_context: list[tuple[str, str]],
        custom_vocab: list[str],
        code_switching_enabled: bool,
        max_tokens: int,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stats: Callable[[DecodeStats], None] | None = None,
    ) -> ASTResult | None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        job = _QueuedJob(
            priority=1 if priority == "partial" else 0,
            sequence=next(self._sequence),
            kind="ast",
            future=future,
            on_progress=on_progress,
            on_stats=on_stats,
            payload={
                "priority": priority,
                "utterance_id": utterance_id,
                "audio_f32_16k": audio_f32_16k,
                "src": src,
                "tgt": tgt,
                "prior_context": prior_context,
                "custom_vocab": custom_vocab,
                "code_switching_enabled": code_switching_enabled,
                "max_tokens": max_tokens,
            },
        )
        if utterance_id is not None:
            previous = self._queued_partial_jobs.get(utterance_id)
            if priority == "partial":
                if utterance_id in self._final_utterance_ids:
                    future.set_result(None)
                    return await future
                if previous is not None and not previous.future.done():
                    previous.future.set_result(None)
                self._queued_partial_jobs[utterance_id] = job
            else:
                self._final_utterance_ids.add(utterance_id)
                if previous is not None:
                    self._queued_partial_jobs.pop(utterance_id, None)
                    if not previous.future.done():
                        previous.future.set_result(None)
        await self._queue.put(job)
        return await future

    async def submit_polish(self, text: str) -> str:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        await self._queue.put(
            _QueuedJob(
                priority=20,
                sequence=next(self._sequence),
                kind="polish",
                future=future,
                payload={"text": text},
            )
        )
        return await future

    def finish_partials(self, utterance_id: int) -> None:
        """Retire queued previews immediately when audio commits, before final ASR finishes."""
        self._final_utterance_ids.add(utterance_id)
        active = self._active_job
        if (active is not None and active.kind == "ast"
                and active.payload.get("priority") == "partial"
                and active.payload.get("utterance_id") == utterance_id):
            self._cancelled_job_id.value = active.sequence
        job = self._queued_partial_jobs.pop(utterance_id, None)
        if job is not None and not job.future.done():
            job.future.set_result(None)


    async def submit_maintenance(self) -> None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        await self._queue.put(
            _QueuedJob(
                priority=30,
                sequence=next(self._sequence),
                kind="maintenance",
                future=future,
                payload={},
            )
        )
        await future

    async def add_status_listener(
        self, listener: Callable[[WorkerStatusEvent], Awaitable[None]]
    ) -> None:
        self._status_listeners.add(listener)
        await listener(self._status)

    def remove_status_listener(
        self, listener: Callable[[WorkerStatusEvent], Awaitable[None]]
    ) -> None:
        self._status_listeners.discard(listener)

    @property
    def status(self) -> WorkerStatusEvent:
        return self._status

    @property
    def is_busy_or_backlogged(self) -> bool:
        return self._active_job_kind is not None or self._queue.qsize() > 0

    async def _run(self) -> None:
        while not self._closed.is_set():
            job = await self._queue.get()
            try:
                if job.future.done():
                    continue
                if self._should_skip_job(job):
                    if not job.future.done():
                        job.future.set_result(None)
                    continue
                self._active_job_kind = job.kind
                self._active_job = job
                dispatched_at = time.monotonic()
                result = await self._execute_job(job)
                finished_at = time.monotonic()
                if job.on_stats is not None:
                    job.on_stats(self._job_stats(job, result, dispatched_at, finished_at))
                if finished_at - job.enqueued_at >= 1.0:
                    logger.warning(
                        "slow_inference engine=gemma priority=%s utterance_id=%s queue_seconds=%.3f inference_seconds=%.3f",
                        job.payload.get("priority"), job.payload.get("utterance_id"),
                        dispatched_at - job.enqueued_at, finished_at - dispatched_at,
                    )
                if not job.future.cancelled():
                    job.future.set_result(result)
            except Exception as exc:
                if not job.future.cancelled():
                    job.future.set_exception(exc)
            finally:
                self._active_job_kind = None
                self._active_job = None
                self._queue.task_done()

    def _job_stats(
        self,
        job: _QueuedJob,
        result: object,
        dispatched_at: float,
        finished_at: float,
    ) -> DecodeStats:
        """Cost of one finished AST job, including ones cancelled to no result.

        A preview retired by a commit returns no result, so without this its GPU
        time is invisible, and invisible work is the work nobody optimises.
        """
        queue_seconds = round(max(0.0, dispatched_at - job.enqueued_at), 3)
        inference_seconds = round(max(0.0, finished_at - dispatched_at), 3)
        if isinstance(result, ASTResult) and result.stats is not None:
            result.stats.queue_seconds = queue_seconds
            return result.stats
        audio = job.payload.get("audio_f32_16k")
        return DecodeStats(
            audio_seconds=(float(audio.size) / 16_000) if audio is not None else 0.0,
            max_tokens=int(job.payload.get("max_tokens", 0)),
            inference_seconds=inference_seconds,
            queue_seconds=queue_seconds,
            cancelled=job.payload.get("priority") == "partial",
        )

    def _should_skip_job(self, job: _QueuedJob) -> bool:
        if job.payload.get("priority") != "partial":
            return False
        utterance_id = job.payload.get("utterance_id")
        if not isinstance(utterance_id, int):
            return False
        if utterance_id in self._final_utterance_ids:
            return True
        if job.kind == "ast":
            queued_job = self._queued_partial_jobs.get(utterance_id)
            queued_jobs = self._queued_partial_jobs
        else:
            return False
        if queued_job is None:
            return False
        if queued_job.sequence != job.sequence:
            return True
        queued_jobs.pop(utterance_id, None)
        return False

    async def _run_temp_wav_sweeper(self) -> None:
        while not self._closed.is_set():
            await asyncio.to_thread(
                sweep_stale_temp_wavs,
                self._temp_wav_root,
                TEMP_WAV_STALE_SECONDS,
            )
            try:
                await asyncio.wait_for(
                    self._closed.wait(),
                    timeout=TEMP_WAV_SWEEP_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                continue

    async def _execute_job(self, job: _QueuedJob) -> object:
        timeout_seconds = self._job_timeout_seconds(job)
        try:
            return await self._dispatch_job(job, timeout_seconds)
        except InferenceTimeoutError:
            await self._recover_worker(
                f"{job.kind.upper()} timed out after {timeout_seconds:.1f}s; reloading model worker"
            )
            raise
        except WorkerProcessError as exc:
            recovered = await self._recover_worker(
                f"Model worker crashed during {job.kind}; reloading once"
            )
            if recovered:
                return await self._dispatch_job(job, timeout_seconds)
            raise RuntimeError(f"Inference failed after worker recovery attempt: {exc}") from exc

    async def _dispatch_job(self, job: _QueuedJob, timeout_seconds: float) -> object:
        if self._request_queue is None or self._response_queue is None or self._process is None:
            raise WorkerProcessError("MLX worker process is not started")
        if not self._process.is_alive():
            raise WorkerProcessError("MLX worker process exited unexpectedly")

        payload = job.payload
        if job.kind == "ast":
            payload = {
                key: value
                for key, value in job.payload.items()
                if key != "utterance_id"
            }
        request = {
            "job_id": job.sequence,
            "kind": job.kind,
            "cancellable": (
                job.kind == "ast"
                and job.payload.get("priority") == "partial"
            ),
            "payload": payload,
        }
        await asyncio.to_thread(self._request_queue.put, request)
        response = await self._wait_for_worker_response(job, timeout_seconds)

        if response.get("type") == "result" and response.get("job_id") == job.sequence:
            return response["result"]
        if response.get("type") == "fatal" and response.get("job_id") == job.sequence:
            await self._stop_worker_process(force=True)
            raise WorkerProcessError(response.get("error", "Unknown worker error"))
        raise WorkerProcessError(f"Unexpected worker response: {response!r}")

    async def _wait_for_worker_response(
        self,
        job: _QueuedJob,
        timeout_seconds: float,
    ) -> dict:
        if self._response_queue is None or self._process is None:
            raise WorkerProcessError("MLX worker process is not started")

        deadline = time.monotonic() + timeout_seconds
        while True:
            if not self._process.is_alive():
                raise WorkerProcessError("MLX worker process exited unexpectedly")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await self._stop_worker_process(force=True)
                raise InferenceTimeoutError(
                    f"{job.kind.upper()} timed out after {timeout_seconds:.1f}s"
                )
            try:
                response = await asyncio.to_thread(
                    self._response_queue.get,
                    True,
                    min(0.25, remaining),
                )
                if response.get("type") == "progress" and response.get("job_id") == job.sequence:
                    if job.on_progress is not None:
                        await job.on_progress(response["text"])
                    continue
                return response
            except queue.Empty:
                continue

    async def _recover_worker(self, message: str) -> bool:
        if self._closed.is_set():
            return False
        await self._emit_status(WorkerStatusEvent(state="recovering", message=message))
        await self._stop_worker_process(force=True)
        if MLX_WORKER_RECOVERY_BACKOFF_SECONDS:
            await asyncio.sleep(MLX_WORKER_RECOVERY_BACKOFF_SECONDS)
        try:
            await self._start_worker_process()
            return True
        except Exception as exc:
            await self._emit_status(
                WorkerStatusEvent(state="failed", message=f"Model worker recovery failed: {exc}")
            )
            return False

    async def _start_worker_process(self) -> None:
        await self._emit_status(WorkerStatusEvent(state="starting", message="Loading Gemma model worker"))
        request_queue: mp.Queue = self._mp_context.Queue()
        response_queue: mp.Queue = self._mp_context.Queue()
        process = self._mp_context.Process(
            target=_worker_process_main,
            args=(request_queue, response_queue, str(self._temp_wav_root), self._cancelled_job_id),
            daemon=True,
        )
        process.start()
        self._process = process
        self._request_queue = request_queue
        self._response_queue = response_queue
        try:
            deadline = time.monotonic() + MLX_WORKER_START_TIMEOUT_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        f"Timed out loading MLX worker after {MLX_WORKER_START_TIMEOUT_SECONDS:.1f}s"
                    )
                try:
                    response = await asyncio.to_thread(
                        response_queue.get, True, min(0.25, remaining)
                    )
                    break
                except queue.Empty:
                    if not process.is_alive():
                        raise RuntimeError("MLX worker exited while loading")
            if response.get("type") != "ready":
                raise RuntimeError(response.get("error", "MLX worker failed to report ready"))
            await self._emit_status(WorkerStatusEvent(state="ready", message="Model worker ready"))
        except BaseException:
            # Disconnect cancels session warmup. Reap its child before another
            # start can replace our only process/queue references.
            await self._stop_worker_process(force=True)
            raise

    async def _stop_worker_process(self, force: bool) -> None:
        process = self._process
        request_queue = self._request_queue
        response_queue = self._response_queue
        self._process = None
        self._request_queue = None
        self._response_queue = None

        if process is None:
            return

        if request_queue is not None and not force:
            try:
                await asyncio.to_thread(request_queue.put, None)
            except Exception:
                pass

        await asyncio.to_thread(process.join, 1.0)
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 2.0)
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, 2.0)

        for ipc_queue in (request_queue, response_queue):
            if ipc_queue is None:
                continue
            try:
                ipc_queue.close()
                ipc_queue.join_thread()
            except Exception:
                pass

    async def _emit_status(self, event: WorkerStatusEvent) -> None:
        self._status = event
        if not self._status_listeners:
            return
        results = await asyncio.gather(
            *(listener(event) for listener in list(self._status_listeners)),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                continue

    def _job_timeout_seconds(self, job: _QueuedJob) -> float:
        if job.kind == "maintenance":
            return MAINTENANCE_TIMEOUT_SECONDS
        if job.kind == "polish":
            return POLISH_TIMEOUT_SECONDS
        if job.kind == "ast" and job.payload.get("priority") == "partial":
            return PARTIAL_TIMEOUT_SECONDS
        return FINAL_TIMEOUT_SECONDS


def _worker_process_main(
    request_queue: mp.Queue,
    response_queue: mp.Queue,
    temp_wav_root: str,
    cancelled_job_id,
) -> None:
    # A spawned process inherits no logging configuration, so without this every
    # INFO line this worker emits is dropped and the engine log looks empty where
    # the decode actually happens. Worker-side diagnostics are the only way to see
    # what a decode did, and their absence previously read as "nothing happened".
    logging.basicConfig(
        level=os.getenv("LIVETR3_LOG_LEVEL", "INFO"),
        format="%(levelname)s  %(message)s",
        stream=sys.stderr,
    )
    try:
        worker = MLXWorker(Path(temp_wav_root))
    except Exception:
        response_queue.put({"type": "fatal", "error": traceback.format_exc()})
        return

    response_queue.put({"type": "ready"})

    while True:
        try:
            request = request_queue.get()
        except (EOFError, KeyboardInterrupt):
            return
        if request is None:
            return

        job_id = request["job_id"]
        try:
            if request["kind"] == "ast":
                cancelled = (
                    (lambda: cancelled_job_id.value == job_id)
                    if request.get("cancellable") else None
                )
                result = worker.ast(
                    **request["payload"],
                    cancelled=cancelled,
                    on_progress=lambda text: response_queue.put(
                        {"type": "progress", "job_id": job_id, "text": text}
                    ),
                )
            elif request["kind"] == "maintenance":
                result = worker.clear_caches()
            elif request["kind"] == "polish":
                result = worker.polish(**request["payload"])
            else:
                raise ValueError(f"Unknown worker job: {request["kind"]}")
        except Exception:
            response_queue.put(
                {
                    "type": "fatal",
                    "job_id": job_id,
                    "error": traceback.format_exc(),
                }
            )
            return

        response_queue.put({"type": "result", "job_id": job_id, "result": result})


def sweep_stale_temp_wavs(temp_wav_root: Path, stale_after_seconds: float) -> int:
    temp_wav_root.mkdir(parents=True, exist_ok=True)
    now = time.time()
    removed = 0
    for wav_path in temp_wav_root.glob("*.wav"):
        try:
            age_seconds = now - wav_path.stat().st_mtime
        except FileNotFoundError:
            continue
        if age_seconds < stale_after_seconds:
            continue
        _safe_unlink(wav_path)
        removed += 1
    return removed


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
