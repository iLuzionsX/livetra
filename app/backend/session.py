from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal
from uuid import uuid4

import numpy as np
from mlx_worker import (
    ASTResult,
    DecodeStats,
    InferenceTimeoutError,
    MLXWorkerService,
    WorkerStatusEvent,
    _parse_ast_response,
)
from protocol import (
    ConfigMessage,
    ErrorMessage,
    LevelMessage,
    SpeechStartMessage,
    StatusMessage,
    TranscriptMessage,
    parse_control_message,
)
from segmenter import FRAME_SAMPLES, RMSGate, make_segmenter
from transport import SessionTransport

logger = logging.getLogger("uvicorn.error")

MAINTENANCE_INTERVAL_SECONDS = max(
    60.0, float(os.getenv("MAINTENANCE_INTERVAL_SECONDS", str(20 * 60)))
)
MAINTENANCE_INTERVAL_UTTERANCES = max(
    1, int(os.getenv("MAINTENANCE_INTERVAL_UTTERANCES", "100"))
)
ARCHIVE_AUTOSAVE_SECONDS = max(5, int(os.getenv("SESSION_AUTOSAVE_SECONDS", "60")))
ARCHIVE_ROOT = (
    Path.home() / "Library" / "Application Support" / "LiveTR3" / "sessions"
)
# Set to a directory to persist per-session decode accounting. Off by default so a
# live service writes nothing extra.
CAPTION_METRICS_DIR = os.getenv("CAPTION_METRICS_DIR", "")
CAPTION_METRICS_MAX_RECORDS = 20_000
LEARNING_PROFILE_PATH = (
    Path.home() / "Library" / "Application Support" / "LiveTR3" / "learning_profile.json"
)
PARTIAL_INTERVAL_MIN_SECONDS = 0.2
PARTIAL_INTERVAL_MAX_SECONDS = 3.0
SOURCE_END_PUNCTUATION = ".?!"
TRAILING_PUNCTUATION_QUOTES = " \t\r\n\"'“”‘’)]}"
ABBREVIATIONS_BEFORE_END_PUNCTUATION = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "st",
    "jr",
    "sr",
    "vs",
    "etc",
    "ie",
    "i.e",
    "eg",
    "e.g",
}
DEFAULT_EARLY_COMMIT_ENABLED = os.getenv("EARLY_COMMIT_ENABLED", "false").lower() not in {
    "0",
    "false",
    "no",
    "off",
}
DEFAULT_EARLY_COMMIT_MIN_SECONDS = max(
    0.0, float(os.getenv("EARLY_COMMIT_MIN_SECONDS", "1.0"))
)
DEFAULT_EARLY_COMMIT_PUNCTUATION = os.getenv(
    "EARLY_COMMIT_PUNCTUATION", "true"
).lower() not in {"0", "false", "no", "off"}
DEFAULT_EARLY_COMMIT_STABILITY = os.getenv("EARLY_COMMIT_STABILITY", "true").lower() not in {
    "0",
    "false",
    "no",
    "off",
}
DEFAULT_STABILITY_WINDOW = max(2, int(os.getenv("STABILITY_WINDOW", "2")))
DEFAULT_PARTIAL_MIN_AUDIO_SECONDS = max(
    0.0, float(os.getenv("PARTIAL_MIN_AUDIO_SECONDS", "0.25"))
)
DEFAULT_PARTIAL_MIN_NEW_SPEECH_SECONDS = max(
    0.1, float(os.getenv("PARTIAL_MIN_NEW_SPEECH_SECONDS", "0.20"))
)
DEFAULT_PARTIAL_AST_ENABLED = os.getenv("PARTIAL_AST_ENABLED", "true").lower() not in {
    "0",
    "false",
    "no",
    "off",
}
MIN_TRANSCRIBABLE_RMS = max(0.0, float(os.getenv("MIN_TRANSCRIBABLE_RMS", "0.0004")))
MIN_TRANSCRIBABLE_PEAK = max(0.0, float(os.getenv("MIN_TRANSCRIBABLE_PEAK", "0.003")))
MIN_TRANSCRIBABLE_FRAME_RMS = max(
    0.0, float(os.getenv("MIN_TRANSCRIBABLE_FRAME_RMS", "0.0006"))
)
MIN_TRANSCRIBABLE_VOICED_MS = max(
    0, int(os.getenv("MIN_TRANSCRIBABLE_VOICED_MS", "60"))
)
TRANSCRIBABLE_TRIM_PAD_SECONDS = min(
    1.0, max(0.0, float(os.getenv("TRANSCRIBABLE_TRIM_PAD_SECONDS", "0.30")))
)


CommitReason = Literal["punctuation", "stability", "silero_end", "max_utterance_cap"]


@dataclass(slots=True)
class UtteranceRuntime:
    partials: deque[str] = field(default_factory=deque)
    last_audio_frame_unix_seconds: float | None = None
    last_partial_wall_seconds: float = 0.0
    last_partial_audio_samples: int = 0
    slowest_partial_turnaround_seconds: float = 0.0
    last_voiced_audio_samples: int = 0
    voiced_audio_samples: int = 0
    latest_partial_original: str = ""
    latest_partial_translation: str = ""
    partials_submitted: int = 0
    partials_completed: int = 0
    last_complete_partial_voiced: tuple[int, str] | None = None


def _seconds_block(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "total": 0.0, "mean": 0.0, "median": 0.0, "max": 0.0}
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2
    )
    return {
        "count": len(ordered),
        "total": round(sum(ordered), 3),
        "mean": round(sum(ordered) / len(ordered), 3),
        "median": round(median, 3),
        "max": round(ordered[-1], 3),
    }


@dataclass(slots=True)
class DecodeLedger:
    """Every AST decode and commit in one session, measured.

    Transcript messages cannot show where inference time goes, and they cannot
    show how often a final decode repeats a preview that already decoded the
    same audio. Both questions decide whether the final pass is worth keeping.
    """

    decodes: list[dict] = field(default_factory=list)
    commits: list[dict] = field(default_factory=list)

    def record_decode(
        self,
        *,
        priority: str,
        utterance_id: int | None,
        audio_seconds: float,
        wall_seconds: float,
        stats: DecodeStats | None,
        outcome: str,
    ) -> None:
        if len(self.decodes) >= CAPTION_METRICS_MAX_RECORDS:
            return
        self.decodes.append(
            {
                "priority": priority,
                "utterance_id": utterance_id,
                "audio_seconds": round(audio_seconds, 3),
                # Wall time covers queue wait, so it bounds GPU time rather than
                # measuring it. Use the worker's own timing whenever it survived.
                "wall_seconds": round(wall_seconds, 3),
                "inference_seconds": round(
                    stats.inference_seconds if stats is not None else wall_seconds,
                    3,
                ),
                "measured_inference": stats is not None,
                "attempts": stats.attempts if stats is not None else 0,
                "generated_tokens": stats.generated_tokens if stats is not None else 0,
                "max_tokens": stats.max_tokens if stats is not None else 0,
                "truncated": bool(stats.truncated) if stats is not None else False,
                "cancelled": bool(stats.cancelled) if stats is not None else outcome == "cancelled",
                "outcome": outcome,
            }
        )

    def record_commit(
        self,
        *,
        utterance_id: int,
        reason: CommitReason,
        audio_seconds: float,
        voiced_seconds: float,
        matched_complete_partial: bool,
        partials_submitted: int,
        partials_completed: int,
    ) -> None:
        if len(self.commits) >= CAPTION_METRICS_MAX_RECORDS:
            return
        self.commits.append(
            {
                "utterance_id": utterance_id,
                "reason": reason,
                "audio_seconds": round(audio_seconds, 3),
                "voiced_seconds": round(voiced_seconds, 3),
                # True when a completed preview already decoded exactly this voiced
                # content, which makes the final pass a repeat.
                "matched_complete_partial": matched_complete_partial,
                "partials_submitted": partials_submitted,
                "partials_completed": partials_completed,
            }
        )

    def summary(self) -> dict:
        by_priority: dict[str, dict] = {}
        for priority in ("partial", "final"):
            rows = [row for row in self.decodes if row["priority"] == priority]
            by_priority[priority] = {
                "wall_seconds": _seconds_block([row["wall_seconds"] for row in rows]),
                "inference_seconds": _seconds_block(
                    [row["inference_seconds"] for row in rows]
                ),
                "audio_seconds": _seconds_block([row["audio_seconds"] for row in rows]),
                "generated_tokens": sum(row["generated_tokens"] for row in rows),
                "attempts": sum(row["attempts"] for row in rows),
                "truncated": sum(1 for row in rows if row["truncated"]),
                "cancelled": sum(1 for row in rows if row["cancelled"]),
                "measured_inference": sum(1 for row in rows if row["measured_inference"]),
                "outcomes": {
                    outcome: sum(1 for row in rows if row["outcome"] == outcome)
                    for outcome in sorted({row["outcome"] for row in rows})
                },
            }

        total_inference = sum(
            by_priority[priority]["inference_seconds"]["total"]
            for priority in by_priority
        )
        for priority, block in by_priority.items():
            block["inference_share_percent"] = (
                round(block["inference_seconds"]["total"] / total_inference * 100, 1)
                if total_inference
                else 0.0
            )

        matched = [
            commit for commit in self.commits if commit["matched_complete_partial"]
        ]
        matched_finals = [
            row
            for commit in matched
            for row in self.decodes
            if row["priority"] == "final" and row["utterance_id"] == commit["utterance_id"]
        ]
        # The final currently decodes untrimmed commit audio, so this is what reuse
        # could save once the final sends the same trimmed clip a preview sends.
        redundant_seconds = sum(row["inference_seconds"] for row in matched_finals)
        partial_totals = [
            commit["partials_submitted"] for commit in self.commits
        ]
        completed_totals = [
            commit["partials_completed"] for commit in self.commits
        ]

        return {
            "decode_count": len(self.decodes),
            "commit_count": len(self.commits),
            "by_priority": by_priority,
            "final_redundancy": {
                "commits": len(self.commits),
                "matched_complete_partial": len(matched),
                "matched_percent": (
                    round(len(matched) / len(self.commits) * 100, 1)
                    if self.commits
                    else 0.0
                ),
                "redundant_final_inference_seconds": round(redundant_seconds, 3),
                "redundant_finals": len(matched_finals),
            },
            "partials_per_utterance": {
                "submitted": _seconds_block([float(v) for v in partial_totals]),
                "completed": _seconds_block([float(v) for v in completed_totals]),
            },
            "commit_reasons": {
                reason: sum(1 for commit in self.commits if commit["reason"] == reason)
                for reason in sorted({commit["reason"] for commit in self.commits})
            },
        }


def _source_text_ends_sentence(text: str) -> bool:
    stripped = text.rstrip(TRAILING_PUNCTUATION_QUOTES)
    if not stripped or stripped[-1] not in SOURCE_END_PUNCTUATION:
        return False
    before_punctuation = stripped[:-1].rstrip(TRAILING_PUNCTUATION_QUOTES)
    match = re.search(r"([A-Za-z](?:[A-Za-z]|\.)*)$", before_punctuation)
    if match and match.group(1).rstrip(".").lower() in ABBREVIATIONS_BEFORE_END_PUNCTUATION:
        return False
    return True


def _normalize_stability_text(text: str) -> str:
    return text.strip().rstrip(TRAILING_PUNCTUATION_QUOTES + SOURCE_END_PUNCTUATION).lower()


def _bounded_partial_interval_seconds(value: float, *, source: str) -> float:
    bounded = min(max(value, PARTIAL_INTERVAL_MIN_SECONDS), PARTIAL_INTERVAL_MAX_SECONDS)
    if bounded != value:
        logger.warning(
            "%s %.3fs is outside %.1f-%.1fs; clamping to %.3fs",
            source,
            value,
            PARTIAL_INTERVAL_MIN_SECONDS,
            PARTIAL_INTERVAL_MAX_SECONDS,
            bounded,
        )
    return bounded


def _load_default_partial_interval_seconds() -> float:
    raw_value = os.getenv("PARTIAL_INTERVAL_SECONDS", "0.25")
    try:
        value = float(raw_value)
    except ValueError:
        logger.warning(
            "PARTIAL_INTERVAL_SECONDS=%r is not a float; using default 0.250s",
            raw_value,
        )
        return 0.25
    return _bounded_partial_interval_seconds(value, source="PARTIAL_INTERVAL_SECONDS")


DEFAULT_PARTIAL_INTERVAL_SECONDS = _load_default_partial_interval_seconds()


def _audio_energy_stats(audio: np.ndarray) -> tuple[float, float, int, int]:
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size < FRAME_SAMPLES:
        return 0.0, 0.0, 0, max(1, int(np.ceil(MIN_TRANSCRIBABLE_VOICED_MS / 20)))

    rms = float(np.sqrt(float(np.mean(np.square(audio)))))
    peak = float(np.max(np.abs(audio)))

    frame_count = audio.size // FRAME_SAMPLES
    if frame_count <= 0:
        return rms, peak, 0, max(1, int(np.ceil(MIN_TRANSCRIBABLE_VOICED_MS / 20)))
    framed = audio[: frame_count * FRAME_SAMPLES].reshape(frame_count, FRAME_SAMPLES)
    frame_rms = np.sqrt(np.mean(np.square(framed), axis=1))
    voiced_frames = int(np.count_nonzero(frame_rms >= MIN_TRANSCRIBABLE_FRAME_RMS))
    required_frames = max(1, int(np.ceil(MIN_TRANSCRIBABLE_VOICED_MS / 20)))
    return rms, peak, voiced_frames, required_frames


def _audio_has_transcribable_energy(audio: np.ndarray) -> bool:
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size < FRAME_SAMPLES:
        return False
    rms, peak, voiced_frames, required_frames = _audio_energy_stats(audio)
    if peak < MIN_TRANSCRIBABLE_PEAK:
        return False
    if voiced_frames < required_frames:
        return False
    return rms >= MIN_TRANSCRIBABLE_RMS or voiced_frames >= required_frames * 2


def _voiced_span(audio: np.ndarray) -> tuple[int, int] | None:
    """First and last frame that clear the transcribable-energy threshold."""
    frame_count = audio.size // FRAME_SAMPLES
    if frame_count <= 0:
        return None
    framed = audio[: frame_count * FRAME_SAMPLES].reshape(frame_count, FRAME_SAMPLES)
    frame_rms = np.sqrt(np.mean(np.square(framed), axis=1))
    voiced_indices = np.flatnonzero(frame_rms >= MIN_TRANSCRIBABLE_FRAME_RMS)
    if voiced_indices.size == 0:
        return None
    return int(voiced_indices[0]), int(voiced_indices[-1])


def _trim_to_transcribable_audio(audio: np.ndarray) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    frame_count = audio.size // FRAME_SAMPLES
    if frame_count <= 0:
        return np.zeros(0, dtype=np.float32)

    span = _voiced_span(audio)
    if span is None:
        return audio

    pad_frames = max(1, int(np.ceil(TRANSCRIBABLE_TRIM_PAD_SECONDS / 0.02)))
    start_frame = max(0, span[0] - pad_frames)
    end_frame = min(frame_count, span[1] + pad_frames + 1)
    return audio[start_frame * FRAME_SAMPLES : end_frame * FRAME_SAMPLES].astype(
        np.float32,
        copy=False,
    )


def _voiced_signature(audio: np.ndarray) -> tuple[int, str] | None:
    """Identity of what was *said*, ignoring silence around it.

    Trailing silence changes the length of a clip but not its content, and
    decoding it again cannot change the caption. Comparing the voiced span
    rather than the padded slice is what makes a commit that only added silence
    recognisable as a repeat of an earlier preview.
    """
    span = _voiced_span(audio)
    if span is None:
        return None
    start_frame, end_frame = span
    contiguous = np.ascontiguousarray(audio, dtype=np.float32)
    voiced = contiguous[start_frame * FRAME_SAMPLES : (end_frame + 1) * FRAME_SAMPLES]
    return (
        int(voiced.size),
        hashlib.blake2b(voiced.tobytes(), digest_size=12).hexdigest(),
    )


@dataclass(slots=True)
class SessionState:
    config: ConfigMessage = field(default_factory=ConfigMessage)
    running: bool = False
    utterance_id: int = 0
    active_utterance_id: int | None = None
    prior_context: list[tuple[str, str]] = field(default_factory=list)
    bilingual_context: list[tuple[str, str]] = field(default_factory=list)
    asr_corrections: list[tuple[str, str]] = field(default_factory=list)
    last_maintenance_at: float = field(default_factory=time.monotonic)
    utterances_since_maintenance: int = 0


@dataclass(slots=True)
class SharedSessionRoom:
    session_id: str
    producer: TranscriptionSession | None = None
    viewers: set[TranscriptionSession] = field(default_factory=set)
    transcript_state: dict[int, dict] = field(default_factory=dict)
    saved_state: dict | None = None


class SessionHub:
    def __init__(self) -> None:
        self._rooms: dict[str, SharedSessionRoom] = {}
        self._lock = asyncio.Lock()

    async def attach_producer(self, session_id: str, session: TranscriptionSession) -> None:
        async with self._lock:
            room = self._rooms.setdefault(session_id, SharedSessionRoom(session_id=session_id))
            room.producer = session

    async def begin_producer_run(self, session_id: str) -> int:
        async with self._lock:
            room = self._rooms.setdefault(session_id, SharedSessionRoom(session_id=session_id))
            highest_utterance_id = max(room.transcript_state.keys(), default=0)
            if room.saved_state is not None:
                highest_utterance_id = max(
                    highest_utterance_id,
                    int(room.saved_state.get("utterance_id", 0)),
                )
            room.transcript_state.clear()
            room.saved_state = None
            return highest_utterance_id

    async def attach_viewer(self, session_id: str, session: TranscriptionSession) -> list[dict]:
        async with self._lock:
            room = self._rooms.setdefault(session_id, SharedSessionRoom(session_id=session_id))
            room.viewers.add(session)
            # Audience windows join at the live edge. Replaying an entire service into
            # a reading-time queue would put a newly opened projector minutes behind.
            captions = [room.transcript_state[key] for key in sorted(room.transcript_state)]
            latest_final = next(
                (caption for caption in reversed(captions)
                 if caption["type"] in {"final", "polished"}),
                None,
            )
            latest = captions[-1] if captions else None
            snapshot = [latest_final] if latest_final is not None else []
            if latest is not None and latest is not latest_final:
                snapshot.append(latest)
            return snapshot

    async def detach(self, session_id: str, session: TranscriptionSession) -> None:
        async with self._lock:
            room = self._rooms.get(session_id)
            if room is None:
                return
            if room.producer is session:
                room.saved_state = session.export_session_snapshot()
                room.producer = None
            room.viewers.discard(session)
            if room.producer is None and not room.viewers and room.saved_state is None:
                self._rooms.pop(session_id, None)

    async def broadcast_transcript(
        self,
        session_id: str,
        payload: dict,
        sender: TranscriptionSession,
    ) -> None:
        async with self._lock:
            room = self._rooms.setdefault(session_id, SharedSessionRoom(session_id=session_id))
            if payload.get("type") in {"partial", "final", "polished"}:
                room.transcript_state[payload["utterance_id"]] = payload
            viewers = list(room.viewers)
        await asyncio.gather(
            *(viewer.send_viewer_payload(payload) for viewer in viewers if viewer is not sender),
            return_exceptions=True,
        )

    async def restore_saved_state(self, session_id: str) -> dict | None:
        async with self._lock:
            room = self._rooms.get(session_id)
            if room is None:
                return None
            return room.saved_state.copy() if room.saved_state is not None else None

    async def active_producer_count(self) -> int:
        async with self._lock:
            return sum(1 for room in self._rooms.values() if room.producer is not None)


class TranscriptionSession:
    def __init__(
        self,
        websocket: SessionTransport,
        worker: MLXWorkerService,
        hub: SessionHub,
    ) -> None:
        self.websocket = websocket
        self.worker = worker
        self.hub = hub
        self.state = SessionState()
        self.segmenter: RMSGate = self._build_segmenter(self.state.config)
        self.ring: deque[np.ndarray] = deque(maxlen=int(30 / 0.02))
        self._send_lock = asyncio.Lock()
        self._jobs: set[asyncio.Task] = set()
        self._last_level_at = 0.0
        self._finalized: set[int] = set()
        self._finalizing: dict[int, CommitReason] = {}
        self._finalize_lock = asyncio.Lock()
        self._utterance_runtime: dict[int, UtteranceRuntime] = {}
        self._pending_config: ConfigMessage | None = None
        self._skip_next_polish = False
        self.session_id = websocket.query_params.get("session") or str(uuid4())
        self.role: Literal["unknown", "producer", "viewer"] = "unknown"
        self._archive_dir: Path | None = None
        self._archive_started_at: datetime | None = None
        self._archive_started_at_monotonic: float | None = None
        self._archive_events: list[dict] = []
        self._archive_utterances: dict[int, dict] = {}
        self._archive_autosave_task: asyncio.Task | None = None
        self._decode_ledger = DecodeLedger()

    async def run(self) -> None:
        await self.websocket.accept()
        await self.worker.add_status_listener(self._handle_worker_status)
        try:
            while True:
                message = await self.websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    await self._receive_audio(message["bytes"])
                elif message.get("text") is not None:
                    await self._receive_text(message["text"])
        finally:
            self.worker.remove_status_listener(self._handle_worker_status)
            await self.hub.detach(self.session_id, self)
            await self._finalize_archive()
            await self._write_decode_metrics()
            self.worker.release_session(self.session_id)
            for task in self._jobs:
                task.cancel()
            await asyncio.gather(*self._jobs, return_exceptions=True)

    async def _receive_text(self, text: str) -> None:
        try:
            payload = json.loads(text)
            msg = parse_control_message(payload)
        except Exception as exc:
            await self._send_error(f"Invalid control message: {exc}")
            return

        if msg.type == "join_viewer":
            self.role = "viewer"
            snapshot = await self.hub.attach_viewer(self.session_id, self)
            for payload in snapshot:
                await self._send(payload)
            return

        if self.role == "viewer":
            await self._send_error("Viewer connections are read-only")
            return

        if self.role == "unknown":
            self.role = "producer"
            await self.hub.attach_producer(self.session_id, self)

        if self._archive_dir is not None:
            self._record_archive_payload({"type": "client_control", "payload": payload})

        if msg.type == "config":
            if msg.apply_target == "next_utterance":
                self._pending_config = msg.model_copy(update={"apply_target": "immediate"})
            else:
                await self._apply_config(msg)
            return

        if msg.type == "resume":
            snapshot = await self.hub.restore_saved_state(self.session_id)
            if snapshot is not None:
                await self._restore_from_snapshot(snapshot)
            else:
                await self._resume_archive_from_disk()
                self.state.running = True
                self.segmenter.reset()
                self._finalizing.clear()
                self._utterance_runtime.clear()
            self._schedule_worker_warmup()
            return

        if msg.type == "start":
            highest_utterance_id = await self.hub.begin_producer_run(self.session_id)
            self._begin_archive()
            self.state.running = True
            self.state.utterance_id = max(self.state.utterance_id, highest_utterance_id)
            self.segmenter.reset()
            self._finalized.clear()
            self._finalizing.clear()
            self._utterance_runtime.clear()
            self.state.last_maintenance_at = time.monotonic()
            self.state.utterances_since_maintenance = 0
            self._schedule_worker_warmup()
            return

        if msg.type == "commit_now":
            await self._flush_active_final()
            return

        if msg.type == "skip_polish":
            self._skip_next_polish = True
            return

        if msg.type == "stop":
            await self._flush_active_final()
            self.state.running = False
            self.segmenter.reset()
            await self._write_archive_snapshot()

    async def _receive_audio(self, data: bytes) -> None:
        if self.role == "viewer":
            return
        if self.role == "unknown":
            self.role = "producer"
            await self.hub.attach_producer(self.session_id, self)
        if not self.state.running:
            return
        if len(data) % 4 != 0:
            await self._send_error("Audio frame was not float32-aligned")
            return

        samples = np.frombuffer(data, dtype="<f4").astype(np.float32, copy=True)
        if samples.size < FRAME_SAMPLES:
            return

        frame_count = samples.size // FRAME_SAMPLES
        for frame in np.split(samples[: frame_count * FRAME_SAMPLES], frame_count):
            await self._receive_frame(frame)

    async def _receive_frame(self, frame: np.ndarray) -> None:
        self.ring.append(frame.copy())
        result = self.segmenter.ingest(frame)
        now = time.monotonic()

        if now - self._last_level_at >= 0.05:
            self._last_level_at = now
            await self._send(LevelMessage(rms=result.rms).model_dump())

        if result.speech_started:
            if self._pending_config is not None:
                await self._apply_config(self._pending_config)
            self._pending_config = None
            self.state.utterance_id += 1
            self.state.active_utterance_id = self.state.utterance_id
            self._utterance_runtime[self.state.utterance_id] = UtteranceRuntime(
                partials=deque(maxlen=self._stability_window()),
            )
            await self._send_and_broadcast(
                SpeechStartMessage(utterance_id=self.state.utterance_id).model_dump()
            )

        if (
            result.speech_active
            and self.state.active_utterance_id is not None
            and result.rms >= MIN_TRANSCRIBABLE_FRAME_RMS
        ):
            runtime = self._utterance_runtime.setdefault(
                self.state.active_utterance_id,
                UtteranceRuntime(partials=deque(maxlen=self._stability_window())),
            )
            runtime.last_audio_frame_unix_seconds = time.time()
            runtime.last_voiced_audio_samples = self.segmenter.current_audio().shape[0]
            runtime.voiced_audio_samples += FRAME_SAMPLES

        if (
            result.speech_active
            and DEFAULT_PARTIAL_AST_ENABLED
            and self.state.active_utterance_id not in self._finalizing
            and self._active_utterance_has_new_speech_for_partial()
        ):
            audio = self.segmenter.current_audio()
            trimmed_audio = _trim_to_transcribable_audio(audio)
            if (
                trimmed_audio.size
                and trimmed_audio.shape[0] / 16_000 >= DEFAULT_PARTIAL_MIN_AUDIO_SECONDS
                and _audio_has_transcribable_energy(trimmed_audio)
            ):
                # Each hypothesis replaces the previous one; preserve the whole utterance.
                inference_audio = trimmed_audio
                runtime = self._active_utterance_runtime()
                if runtime is not None:
                    runtime.last_partial_wall_seconds = time.monotonic()
                    runtime.last_partial_audio_samples = runtime.voiced_audio_samples
                self._schedule_ast("partial", self.state.active_utterance_id, inference_audio)

        if result.speech_ended and result.audio is not None:
            utterance_id = self.state.active_utterance_id
            reason: CommitReason = "max_utterance_cap" if result.force_flushed else "silero_end"
            await self._commit_utterance(utterance_id, result.audio, reason=reason, reset_segmenter=False)

    async def _flush_active_final(self) -> None:
        if not self.segmenter.speech_active or self.state.active_utterance_id is None:
            return
        audio = self.segmenter.current_audio()
        utterance_id = self.state.active_utterance_id
        if audio.size:
            await self._commit_utterance(utterance_id, audio, reason="silero_end", reset_segmenter=True)

    async def _commit_utterance(
        self,
        utterance_id: int | None,
        audio: np.ndarray,
        *,
        reason: CommitReason,
        reset_segmenter: bool,
    ) -> bool:
        if utterance_id is None or not audio.size:
            return False
        transcribable_audio = _trim_to_transcribable_audio(audio)
        if not _audio_has_transcribable_energy(transcribable_audio):
            rms, peak, voiced_frames, required_frames = _audio_energy_stats(transcribable_audio)
            if reset_segmenter:
                self.segmenter.reset()
            if self.state.active_utterance_id == utterance_id:
                self.state.active_utterance_id = None
            self._finalized.add(utterance_id)
            self._finalizing.pop(utterance_id, None)
            self._utterance_runtime.pop(utterance_id, None)
            logger.info(
                (
                    "final_commit skipped reason=low_energy utterance_id=%s "
                    "audio_seconds=%.3f rms=%.6f peak=%.6f voiced_frames=%s required_frames=%s"
                ),
                utterance_id,
                audio.shape[0] / 16_000,
                rms,
                peak,
                voiced_frames,
                required_frames,
            )
            return False
        async with self._finalize_lock:
            if utterance_id in self._finalized or utterance_id in self._finalizing:
                return False
            self._finalizing[utterance_id] = reason
            self.worker.finish_partials(utterance_id, self.session_id)
            if self.state.active_utterance_id == utterance_id:
                self.state.active_utterance_id = None
            if reset_segmenter:
                self.segmenter.reset()
            logger.info(
                "final_commit reason=%s utterance_id=%s audio_seconds=%.3f",
                reason,
                utterance_id,
                audio.shape[0] / 16_000,
            )
            runtime = self._utterance_runtime.get(utterance_id)
            commit_voiced = _voiced_signature(transcribable_audio)
            self._decode_ledger.record_commit(
                utterance_id=utterance_id,
                reason=reason,
                audio_seconds=transcribable_audio.size / 16_000,
                voiced_seconds=(commit_voiced[0] / 16_000) if commit_voiced else 0.0,
                # True when a completed preview already decoded exactly this voiced
                # content, so the final pass cannot change the caption.
                matched_complete_partial=(
                    runtime is not None
                    and runtime.last_complete_partial_voiced is not None
                    and runtime.last_complete_partial_voiced == commit_voiced
                ),
                partials_submitted=runtime.partials_submitted if runtime else 0,
                partials_completed=runtime.partials_completed if runtime else 0,
            )
            self._schedule_ast("final", utterance_id, audio)
            return True

    def _schedule_ast(
        self,
        priority: str,
        utterance_id: int | None,
        audio: np.ndarray,
    ) -> None:
        if utterance_id is None:
            return
        if priority == "partial" and (
            utterance_id in self._finalized or utterance_id in self._finalizing
        ):
            return
        if priority == "partial":
            runtime = self._utterance_runtime.get(utterance_id)
            if runtime is not None:
                runtime.partials_submitted += 1
            scheduled = self._run_scheduled_partial_ast(
                priority, utterance_id, audio.copy()
            )
        else:
            scheduled = self._run_ast(priority, utterance_id, audio.copy())
        task = asyncio.create_task(
            scheduled,
            name=f"{priority}-ast-{utterance_id}",
        )
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    async def _run_scheduled_partial_ast(
        self, priority: str, utterance_id: int, audio: np.ndarray
    ) -> None:
        started_at = time.monotonic()
        try:
            await self._run_ast(priority, utterance_id, audio)
        finally:
            runtime = self._utterance_runtime.get(utterance_id)
            if (
                runtime is not None
                and utterance_id not in self._finalizing
                and utterance_id not in self._finalized
            ):
                # This includes queue wait, so a congested worker also slows preview submissions.
                turnaround = max(0.0, time.monotonic() - started_at)
                runtime.slowest_partial_turnaround_seconds = max(
                    runtime.slowest_partial_turnaround_seconds, turnaround
                )

    async def _run_ast(self, priority: str, utterance_id: int, audio: np.ndarray) -> None:
        started_at = time.monotonic()
        try:
            await self._run_mlx_ast(priority, utterance_id, audio)
        finally:
            # Errors, empty decodes, and cancellation must not leave a commit pending.
            if priority == "final":
                self._finalizing.pop(utterance_id, None)
                self._utterance_runtime.pop(utterance_id, None)
            elapsed = time.monotonic() - started_at
            if elapsed >= 1.0:
                logger.warning(
                    "slow_caption stage=asr priority=%s utterance_id=%s elapsed_seconds=%.3f audio_seconds=%.3f",
                    priority, utterance_id, elapsed, audio.size / 16_000,
                )

    async def _run_mlx_ast(self, priority: str, utterance_id: int, audio: np.ndarray) -> None:
        # Own accounting for exactly one decode, including the early returns where
        # a commit cancels this preview, so cancelled GPU work is not invisible.
        record: dict = {"outcome": "unknown", "stats": None}

        def capture_stats(stats: DecodeStats) -> None:
            # Replaces the fallback so a cancelled preview reports its real cost.
            record["stats"] = stats

        started_at = time.monotonic()
        try:
            await self._run_mlx_ast_body(
                priority, utterance_id, audio, record, capture_stats
            )
        finally:
            self._decode_ledger.record_decode(
                priority=priority,
                utterance_id=utterance_id,
                audio_seconds=audio.size / 16_000,
                wall_seconds=max(0.0, time.monotonic() - started_at),
                stats=record["stats"],
                outcome=record["outcome"],
            )

    async def _run_mlx_ast_body(
        self,
        priority: str,
        utterance_id: int,
        audio: np.ndarray,
        record: dict,
        on_stats: Callable[[DecodeStats], None],
    ) -> None:
        async def publish_progress(text: str) -> None:
            if utterance_id in self._finalized:
                return
            if priority == "partial" and utterance_id in self._finalizing:
                return
            original, translation = _parse_ast_response(
                text,
                self.state.config.target_lang,
                self.state.config.source_lang,
            )
            original = original.strip()
            translation = translation.strip()
            if not original:
                return
            if not self._should_translate_final(original):
                translation = original
            runtime = self._utterance_runtime.setdefault(
                utterance_id,
                UtteranceRuntime(partials=deque(maxlen=self._stability_window())),
            )
            if (
                original == runtime.latest_partial_original
                and translation == runtime.latest_partial_translation
            ):
                return
            runtime.latest_partial_original = original
            runtime.latest_partial_translation = translation
            await self._send_and_broadcast(
                TranscriptMessage(
                    type="partial",
                    utterance_id=utterance_id,
                    original=original,
                    translation=translation,
                ).model_dump(exclude_none=True)
            )

        try:
            result = await self.worker.submit_ast(
                priority="final" if priority == "final" else "partial",
                utterance_id=utterance_id,
                session_id=self.session_id,
                audio_f32_16k=audio,
                src=self.state.config.source_lang,
                tgt=self.state.config.target_lang,
                # Model-generated prior captions can repeat an earlier recognition error.
                prior_context=[],
                custom_vocab=self.state.config.custom_vocab,
                code_switching_enabled=self.state.config.code_switching_enabled,
                max_tokens=self._max_tokens_for_ast(priority, audio),
                on_progress=publish_progress,
                on_stats=on_stats,
            )
        except asyncio.CancelledError:
            record["outcome"] = "cancelled"
            raise
        except InferenceTimeoutError as exc:
            record["outcome"] = "timeout"
            if priority == "partial":
                logger.info("partial_inference dropped after timeout: %s", exc)
                return
            await self._send_error(f"Inference failed: {exc}")
            return
        except Exception as exc:
            record["outcome"] = "error"
            await self._send_error(f"Inference failed: {exc}")
            return

        if result is None:
            record["outcome"] = "cancelled" if priority == "partial" else "no_result"
            return
        if not isinstance(result, ASTResult):
            record["outcome"] = "invalid"
            await self._send_error("Inference failed: model returned an invalid AST result")
            return
        if not result.complete:
            record["outcome"] = "incomplete"
            if priority == "final":
                await self._send_error(
                    "Final inference remained incomplete after one retry; no final caption was published"
                )
            return
        original, translation = result.original, result.translation

        if priority == "partial":
            if utterance_id in self._finalized or utterance_id in self._finalizing:
                record["outcome"] = "stale_after_commit"
                return
            runtime = self._utterance_runtime.setdefault(
                utterance_id,
                UtteranceRuntime(partials=deque(maxlen=self._stability_window())),
            )
            # A completed decode over this voiced content is a reusable final input.
            runtime.partials_completed += 1
            runtime.last_complete_partial_voiced = _voiced_signature(audio)
            record["outcome"] = "partial"
            merged_original = original.strip()
            merged_translation = (
                translation.strip() if self._should_translate_final(merged_original) else merged_original
            )
            if (
                merged_original == runtime.latest_partial_original
                and merged_translation == runtime.latest_partial_translation
            ):
                return
            runtime.latest_partial_original = merged_original
            runtime.latest_partial_translation = merged_translation
            await self._send_and_broadcast(
                TranscriptMessage(
                    type="partial",
                    utterance_id=utterance_id,
                    original=merged_original,
                    translation=merged_translation,
                ).model_dump(exclude_none=True)
            )
            await self._maybe_commit_early(utterance_id, merged_original)
            return

        self._finalized.add(utterance_id)
        record["outcome"] = "final"
        commit_reason = self._finalizing.pop(utterance_id, "silero_end")
        runtime = self._utterance_runtime.pop(utterance_id, None)
        self.state.prior_context.append((original, translation))
        self.state.prior_context = self.state.prior_context[-2:]
        self.state.utterances_since_maintenance += 1
        await self._send_and_broadcast(
            TranscriptMessage(
                type="final",
                utterance_id=utterance_id,
                original=original,
                translation=translation,
                commit_reason=commit_reason,
                last_audio_frame_unix_seconds=(
                    runtime.last_audio_frame_unix_seconds if runtime is not None else None
                ),
            ).model_dump(exclude_none=True)
        )

        skip_polish = self._skip_next_polish
        self._skip_next_polish = False
        if self.state.config.polish_enabled and not skip_polish:
            task = asyncio.create_task(
                self._run_polish(utterance_id, original, translation),
                name=f"polish-{utterance_id}",
            )
            self._jobs.add(task)
            task.add_done_callback(self._jobs.discard)
        await self._maybe_run_maintenance()


    def _should_translate_final(self, original: str) -> bool:
        return (
            bool(original.strip())
            and self.state.config.source_lang.strip().lower()
            != self.state.config.target_lang.strip().lower()
        )


    def _load_global_learning_profile(self) -> None:
        try:
            profile = json.loads(LEARNING_PROFILE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        # Preserve legacy profile data for archive compatibility; never use it to
        # rewrite captions or overwrite the user's profile on disk.
        for key in ("asr_corrections", "bilingual_context"):
            pairs = profile.get(key, [])
            setattr(self.state, key, [
                (item[0], item[1]) for item in pairs
                if isinstance(item, (list, tuple)) and len(item) == 2
                and all(isinstance(value, str) for value in item)
            ])

    def _partial_inference_is_busy_or_backlogged(self) -> bool:
        return self.worker.is_busy_or_backlogged

    def _schedule_worker_warmup(self) -> None:
        task = asyncio.create_task(self._warm_mlx_worker(), name="mlx-worker-warmup")
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)


    async def _warm_mlx_worker(self) -> None:
        try:
            await self.worker.start()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._send_error(f"Model warmup failed: {exc}")

    async def _maybe_commit_early(self, utterance_id: int, original: str) -> None:
        if not self._early_commit_enabled():
            return
        if self.state.active_utterance_id != utterance_id:
            return
        audio = self.segmenter.current_audio()
        if audio.shape[0] / 16_000 < self._early_commit_min_seconds():
            return
        if self._early_commit_punctuation() and _source_text_ends_sentence(original):
            await self._commit_utterance(utterance_id, audio, reason="punctuation", reset_segmenter=True)
            return
        if self._early_commit_stability() and self._source_text_is_stable(utterance_id, original):
            await self._commit_utterance(utterance_id, audio, reason="stability", reset_segmenter=True)

    def _source_text_is_stable(self, utterance_id: int, original: str) -> bool:
        normalized = _normalize_stability_text(original)
        if not normalized:
            return False
        runtime = self._utterance_runtime.setdefault(
            utterance_id,
            UtteranceRuntime(partials=deque(maxlen=self._stability_window())),
        )
        if runtime.partials.maxlen != self._stability_window():
            runtime.partials = deque(runtime.partials, maxlen=self._stability_window())
        runtime.partials.append(normalized)
        return (
            len(runtime.partials) >= self._stability_window()
            and len(set(runtime.partials)) == 1
        )

    async def _run_polish(self, utterance_id: int, original: str, translation: str) -> None:
        try:
            polished_original = await self.worker.submit_polish(original)
            polished_translation = await self.worker.submit_polish(translation)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._send_error(f"Polish failed: {exc}")
            return
        await self._send_and_broadcast(
            TranscriptMessage(
                type="polished",
                utterance_id=utterance_id,
                original=polished_original,
                translation=polished_translation,
            ).model_dump(exclude_none=True)
        )

    async def _send_error(self, message: str) -> None:
        await self._send(ErrorMessage(message=message).model_dump())

    async def _send(self, payload: dict) -> None:
        self._record_archive_payload(payload)
        async with self._send_lock:
            await self.websocket.send_json(payload)

    async def _send_and_broadcast(self, payload: dict) -> None:
        await self._send(payload)
        if self.role == "producer":
            await self.hub.broadcast_transcript(self.session_id, payload, sender=self)

    async def _handle_worker_status(self, event: WorkerStatusEvent) -> None:
        await self._send(
            StatusMessage(
                state=event.state,
                message=event.message,
            ).model_dump()
        )

    async def _maybe_run_maintenance(self) -> None:
        if self.state.active_utterance_id is not None or not self.state.running:
            return

        now = time.monotonic()
        due_for_time = now - self.state.last_maintenance_at >= MAINTENANCE_INTERVAL_SECONDS
        due_for_utterances = (
            self.state.utterances_since_maintenance >= MAINTENANCE_INTERVAL_UTTERANCES
        )
        if not due_for_time and not due_for_utterances:
            return

        self.segmenter.reset()
        self.state.last_maintenance_at = now
        self.state.utterances_since_maintenance = 0

        task = asyncio.create_task(self._run_maintenance(), name="session-maintenance")
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    async def _run_maintenance(self) -> None:
        try:
            await self.worker.submit_maintenance()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._send_error(f"Worker maintenance failed: {exc}")

    async def send_viewer_payload(self, payload: dict) -> None:
        if self.role != "viewer":
            return
        await self._send(payload)

    async def _apply_config(self, config: ConfigMessage) -> None:
        partial_interval_seconds = config.partial_interval_seconds
        if partial_interval_seconds is not None:
            partial_interval_seconds = _bounded_partial_interval_seconds(
                float(partial_interval_seconds),
                source="config.partial_interval_seconds",
            )
        self.state.config = config.model_copy(
            update={
                "apply_target": "immediate",
                "partial_interval_seconds": partial_interval_seconds,
            }
        )
        try:
            self.segmenter = self._build_segmenter(self.state.config)
        except Exception as exc:
            await self._send_error(str(exc))

    async def _restore_from_snapshot(self, snapshot: dict) -> None:
        await self._apply_config(ConfigMessage.model_validate(snapshot["config"]))
        self.state.running = bool(snapshot.get("running", False))
        self.state.utterance_id = int(snapshot.get("utterance_id", 0))
        self.state.active_utterance_id = None
        self.state.prior_context = [
            (item[0], item[1]) for item in snapshot.get("prior_context", [])
        ]
        self.state.bilingual_context = [
            (item[0], item[1]) for item in snapshot.get("bilingual_context", [])
        ]
        self.state.asr_corrections = [
            (item[0], item[1]) for item in snapshot.get("asr_corrections", [])
        ]
        self._finalized = set(snapshot.get("finalized_ids", []))
        self._finalizing.clear()
        self._utterance_runtime.clear()
        self.segmenter.reset()

    def export_session_snapshot(self) -> dict:
        return {
            "config": self.state.config.model_dump(),
            "running": self.state.running,
            "utterance_id": self.state.utterance_id,
            "prior_context": self.state.prior_context,
            "bilingual_context": self.state.bilingual_context,
            "asr_corrections": self.state.asr_corrections,
            "finalized_ids": sorted(self._finalized),
        }

    def _build_segmenter(self, config: ConfigMessage) -> RMSGate:
        rms_threshold = config.rms_threshold or float(os.getenv("RMS_THRESHOLD", "0.01"))
        silero_threshold = min(max(config.silero_threshold or 0.5, 0.1), 0.95)
        speech_pad_ms = min(max(config.speech_pad_ms or 300, 0), 2000)
        min_silence_ms = min(max(config.min_silence_ms or 300, 100), 5000)
        max_utterance_seconds = min(max(config.max_utterance_seconds or 12.0, 5.0), 29.0)
        return make_segmenter(
            config.segmenter,
            rms_threshold,
            silero_threshold=silero_threshold,
            speech_pad_ms=speech_pad_ms,
            min_silence_ms=min_silence_ms,
            max_utterance_s=max_utterance_seconds,
        )

    def _max_tokens_for_ast(self, priority: str, audio: np.ndarray) -> int:
        if priority == "partial":
            return 192
        duration_seconds = audio.shape[0] / 16_000
        if duration_seconds <= 8:
            return 384
        if duration_seconds <= 15:
            return 512
        return 640

    def _partial_interval_seconds(self) -> float:
        configured = (
            self.state.config.partial_interval_seconds
            or DEFAULT_PARTIAL_INTERVAL_SECONDS
        )
        runtime = self._active_utterance_runtime()
        if runtime is None:
            return configured
        service_interval = min(
            PARTIAL_INTERVAL_MAX_SECONDS,
            runtime.slowest_partial_turnaround_seconds,
        )
        return max(configured, service_interval)

    def _active_utterance_runtime(self) -> UtteranceRuntime | None:
        if self.state.active_utterance_id is None:
            return None
        return self._utterance_runtime.get(self.state.active_utterance_id)

    def _active_utterance_has_new_speech_for_partial(self) -> bool:
        runtime = self._active_utterance_runtime()
        if runtime is None:
            return False

        now = time.monotonic()
        if (
            runtime.last_partial_wall_seconds > 0
            and now - runtime.last_partial_wall_seconds < self._partial_interval_seconds()
        ):
            return False

        voiced_samples = runtime.voiced_audio_samples
        if voiced_samples <= 0:
            return False

        if runtime.last_partial_audio_samples <= 0:
            return voiced_samples / 16_000 >= DEFAULT_PARTIAL_MIN_AUDIO_SECONDS

        new_speech_seconds = (
            voiced_samples - runtime.last_partial_audio_samples
        ) / 16_000
        return new_speech_seconds >= DEFAULT_PARTIAL_MIN_NEW_SPEECH_SECONDS

    def _early_commit_enabled(self) -> bool:
        if self.state.config.early_commit_enabled is not None:
            return self.state.config.early_commit_enabled
        return DEFAULT_EARLY_COMMIT_ENABLED

    def _early_commit_min_seconds(self) -> float:
        if self.state.config.early_commit_min_seconds is not None:
            return max(0.0, self.state.config.early_commit_min_seconds)
        return DEFAULT_EARLY_COMMIT_MIN_SECONDS

    def _early_commit_punctuation(self) -> bool:
        if self.state.config.early_commit_punctuation is not None:
            return self.state.config.early_commit_punctuation
        return DEFAULT_EARLY_COMMIT_PUNCTUATION

    def _early_commit_stability(self) -> bool:
        if self.state.config.early_commit_stability is not None:
            return self.state.config.early_commit_stability
        return DEFAULT_EARLY_COMMIT_STABILITY

    def _stability_window(self) -> int:
        if self.state.config.stability_window is not None:
            return max(2, self.state.config.stability_window)
        return DEFAULT_STABILITY_WINDOW

    def _begin_archive(self) -> None:
        if self._archive_dir is not None:
            return
        self._load_global_learning_profile()
        started_at = datetime.now().astimezone()
        started_at_slug = started_at.strftime("%Y-%m-%dT%H-%M-%S%z")
        self._archive_dir = ARCHIVE_ROOT / started_at_slug
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        self._archive_started_at = started_at
        self._archive_started_at_monotonic = time.monotonic()
        self._archive_events = []
        self._archive_utterances = {}
        if self._archive_autosave_task is None:
            self._archive_autosave_task = asyncio.create_task(
                self._run_archive_autosave(),
                name=f"archive-autosave-{self.session_id}",
            )

    async def _run_archive_autosave(self) -> None:
        try:
            while True:
                await asyncio.sleep(ARCHIVE_AUTOSAVE_SECONDS)
                await self._write_archive_snapshot()
        except asyncio.CancelledError:
            raise

    def _record_archive_payload(self, payload: dict) -> None:
        if self.role == "viewer" or self._archive_dir is None or payload.get("type") == "level":
            return
        elapsed_seconds = self._archive_elapsed_seconds()
        self._archive_events.append(
            {
                "timestamp_seconds": elapsed_seconds,
                "payload": payload,
            }
        )

        payload_type = payload.get("type")
        if payload_type == "speech_start":
            self._archive_utterances[payload["utterance_id"]] = {
                "utterance_id": payload["utterance_id"],
                "started_at": elapsed_seconds,
                "ended_at": elapsed_seconds,
                "original": "",
                "translation": "",
                "state": "partial",
            }
            return

        if payload_type not in {"partial", "final", "polished"}:
            return

        utterance = self._archive_utterances.setdefault(
            payload["utterance_id"],
            {
                "utterance_id": payload["utterance_id"],
                "started_at": elapsed_seconds,
                "ended_at": elapsed_seconds,
                "original": "",
                "translation": "",
                "state": payload_type,
            },
        )
        utterance["original"] = payload["original"]
        utterance["translation"] = payload["translation"]
        utterance["state"] = payload_type
        if payload.get("commit_reason") is not None:
            utterance["commit_reason"] = payload["commit_reason"]
        if payload_type != "partial":
            utterance["ended_at"] = elapsed_seconds

    def _archive_elapsed_seconds(self) -> float:
        if self._archive_started_at_monotonic is None:
            return 0.0
        return max(0.0, time.monotonic() - self._archive_started_at_monotonic)

    async def _write_archive_snapshot(self) -> None:
        if self._archive_dir is None or self._archive_started_at is None:
            return
        archive_dir = self._archive_dir
        events = list(self._archive_events)
        utterances = [self._archive_utterances[key] for key in sorted(self._archive_utterances)]
        duration_seconds = self._archive_elapsed_seconds()
        meta = {
            "session_id": self.session_id,
            "started_at": self._archive_started_at.isoformat(),
            "duration_seconds": duration_seconds,
            "config": self.state.config.model_dump(),
            "device": {
                "id": self.state.config.input_device_id,
                "label": self.state.config.input_device_label,
            },
            "bilingual_context": self.state.bilingual_context,
            "asr_corrections": self.state.asr_corrections,
        }
        await asyncio.to_thread(
            self._write_archive_files,
            archive_dir,
            utterances,
            events,
            meta,
        )

    async def _resume_archive_from_disk(self) -> None:
        archive_dir = await asyncio.to_thread(self._find_latest_archive_dir, self.session_id)
        if archive_dir is None:
            self._begin_archive()
            return

        self._archive_dir = archive_dir
        self._archive_events = self._load_archive_events(archive_dir)
        self._archive_utterances = self._utterances_from_events(self._archive_events)
        meta = self._load_archive_meta(archive_dir)
        self._load_global_learning_profile()
        self.state.bilingual_context = [
            (item[0], item[1]) for item in meta.get("bilingual_context", [])
        ]
        self.state.asr_corrections = [
            (item[0], item[1]) for item in meta.get("asr_corrections", [])
        ]
        started_at = meta.get("started_at")
        try:
            self._archive_started_at = datetime.fromisoformat(started_at)
        except (TypeError, ValueError):
            self._archive_started_at = datetime.now().astimezone()
        elapsed_seconds = max(
            [float(event.get("timestamp_seconds", 0.0)) for event in self._archive_events],
            default=0.0,
        )
        self._archive_started_at_monotonic = time.monotonic() - elapsed_seconds
        if self._archive_utterances:
            self.state.utterance_id = max(self.state.utterance_id, max(self._archive_utterances))
            self._finalized = {
                utterance["utterance_id"]
                for utterance in self._archive_utterances.values()
                if utterance.get("state") in {"final", "polished"}
            }
        if self._archive_autosave_task is None:
            self._archive_autosave_task = asyncio.create_task(
                self._run_archive_autosave(),
                name=f"archive-autosave-{self.session_id}",
            )

    async def _finalize_archive(self) -> None:
        if self._archive_dir is None:
            return
        if self._archive_autosave_task is not None:
            self._archive_autosave_task.cancel()
            try:
                await self._archive_autosave_task
            except asyncio.CancelledError:
                pass
            self._archive_autosave_task = None
        await self._write_archive_snapshot()
        self._archive_dir = None

    async def _write_decode_metrics(self) -> None:
        summary = self._decode_ledger.summary()
        if not summary["decode_count"]:
            return
        summary["session_id"] = self.session_id
        logger.info("decode_metrics %s", json.dumps(summary))
        if not CAPTION_METRICS_DIR:
            return
        await asyncio.to_thread(self._write_decode_metrics_files, dict(summary))

    def _write_decode_metrics_files(self, summary: dict) -> None:
        metrics_dir = Path(CAPTION_METRICS_DIR)
        metrics_dir.mkdir(parents=True, exist_ok=True)
        name = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in self.session_id
        )
        (metrics_dir / f"decode-metrics-{name}.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        (metrics_dir / f"decode-metrics-{name}.records.json").write_text(
            json.dumps(
                {
                    "decodes": self._decode_ledger.decodes,
                    "commits": self._decode_ledger.commits,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def _write_archive_files(
        self,
        archive_dir: Path,
        utterances: list[dict],
        events: list[dict],
        meta: dict,
    ) -> None:
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "transcript.srt").write_text(
            self._render_srt(utterances),
            encoding="utf-8",
        )
        (archive_dir / "transcript.vtt").write_text(
            self._render_vtt(utterances),
            encoding="utf-8",
        )
        (archive_dir / "transcript.json").write_text(
            json.dumps(events, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (archive_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _find_latest_archive_dir(self, session_id: str) -> Path | None:
        if not ARCHIVE_ROOT.exists():
            return None
        matches: list[Path] = []
        for meta_path in ARCHIVE_ROOT.glob("*/meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if meta.get("session_id") == session_id:
                matches.append(meta_path.parent)
        if not matches:
            return None
        return max(matches, key=lambda path: path.stat().st_mtime)

    def _load_archive_events(self, archive_dir: Path) -> list[dict]:
        try:
            raw_events = json.loads((archive_dir / "transcript.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        events: list[dict] = []
        seen_finals: set[int] = set()
        for event in raw_events:
            payload = event.get("payload", {})
            if payload.get("type") == "final":
                utterance_id = int(payload.get("utterance_id", 0))
                if utterance_id in seen_finals:
                    continue
                seen_finals.add(utterance_id)
            events.append(event)
        return events

    def _load_archive_meta(self, archive_dir: Path) -> dict:
        try:
            return json.loads((archive_dir / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}


    def _utterances_from_events(self, events: list[dict]) -> dict[int, dict]:
        utterances: dict[int, dict] = {}
        for event in events:
            elapsed_seconds = float(event.get("timestamp_seconds", 0.0))
            payload = event.get("payload", {})
            payload_type = payload.get("type")
            utterance_id = payload.get("utterance_id")
            if not isinstance(utterance_id, int):
                continue
            if payload_type == "speech_start":
                utterances.setdefault(
                    utterance_id,
                    {
                        "utterance_id": utterance_id,
                        "started_at": elapsed_seconds,
                        "ended_at": elapsed_seconds,
                        "original": "",
                        "translation": "",
                        "state": "partial",
                    },
                )
                continue
            if payload_type not in {"partial", "final", "polished"}:
                continue
            utterance = utterances.setdefault(
                utterance_id,
                {
                    "utterance_id": utterance_id,
                    "started_at": elapsed_seconds,
                    "ended_at": elapsed_seconds,
                    "original": "",
                    "translation": "",
                    "state": payload_type,
                },
            )
            utterance["original"] = payload.get("original", "")
            utterance["translation"] = payload.get("translation", "")
            utterance["state"] = payload_type
            if payload.get("commit_reason") is not None:
                utterance["commit_reason"] = payload["commit_reason"]
            if payload_type != "partial":
                utterance["ended_at"] = elapsed_seconds
        return utterances

    def _render_srt(self, utterances: list[dict]) -> str:
        cues: list[str] = []
        for index, utterance in enumerate(utterances, start=1):
            cues.append(
                "\n".join(
                    [
                        str(index),
                        f"{_format_subtitle_time(utterance['started_at'])} --> {_format_subtitle_time(utterance['ended_at'])}",
                        utterance["original"],
                        utterance["translation"],
                    ]
                )
            )
        return "\n\n".join(cues).strip() + ("\n" if cues else "")

    def _render_vtt(self, utterances: list[dict]) -> str:
        cues = ["WEBVTT"]
        for utterance in utterances:
            cues.append(
                "\n".join(
                    [
                        f"{_format_subtitle_time(utterance['started_at'], vtt=True)} --> {_format_subtitle_time(utterance['ended_at'], vtt=True)}",
                        utterance["original"],
                        utterance["translation"],
                    ]
                )
            )
        return "\n\n".join(cues).strip() + "\n"


def _format_subtitle_time(seconds: float, *, vtt: bool = False) -> str:
    total_millis = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    separator = "." if vtt else ","
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"
