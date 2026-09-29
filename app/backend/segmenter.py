from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import sqrt

import numpy as np

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 320
MAX_UTTERANCE_SECONDS = 29.0
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE


@dataclass(slots=True)
class SegmentResult:
    rms: float
    speech_active: bool
    speech_started: bool = False
    speech_ended: bool = False
    force_flushed: bool = False
    audio: np.ndarray | None = None


class RMSGate:
    """Zero-dependency streaming utterance segmenter for 20 ms 16 kHz float32 frames."""

    def __init__(
        self,
        threshold: float = 0.01,
        trailing_silence_ms: int = 400,
        min_utterance_ms: int = 800,
        max_utterance_s: float = 25.0,
        overlap_s: float = 0.5,
    ) -> None:
        self.threshold = threshold
        self.trailing_silence_frames = max(1, trailing_silence_ms // 20)
        self.min_utterance_frames = max(1, min_utterance_ms // 20)
        # Gemma's audio window is 30 seconds; keep segment boundaries below it so
        # no caller can hand the worker a chunk that needs to be silently trimmed.
        safe_max_utterance_s = min(float(max_utterance_s), MAX_UTTERANCE_SECONDS)
        self.max_utterance_frames = max(1, int(safe_max_utterance_s / FRAME_SECONDS))
        self.overlap_frames = max(0, int(overlap_s / 0.02))
        self._pre_roll: deque[np.ndarray] = deque(maxlen=self.overlap_frames)
        self._current: list[np.ndarray] = []
        self._speech_active = False
        self._below_threshold_frames = 0

    @property
    def speech_active(self) -> bool:
        return self._speech_active

    def current_audio(self) -> np.ndarray:
        if not self._current:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._current).astype(np.float32, copy=False)

    def reset(self) -> None:
        tail = self._current[-self.overlap_frames :] if self.overlap_frames else []
        self._pre_roll = deque((x.copy() for x in tail), maxlen=self.overlap_frames)
        self._current = []
        self._speech_active = False
        self._below_threshold_frames = 0

    def _rollover(self) -> None:
        self.reset()

    def ingest(self, frame: np.ndarray) -> SegmentResult:
        frame = _coerce_frame(frame)
        rms = float(sqrt(float(np.mean(np.square(frame)))))
        is_speech = rms > self.threshold
        speech_started = False
        speech_ended = False
        force_flushed = False
        audio: np.ndarray | None = None

        if not self._speech_active:
            if is_speech:
                speech_started = True
                self._speech_active = True
                self._below_threshold_frames = 0
                self._current = [x.copy() for x in self._pre_roll]
                self._current.append(frame)
            else:
                self._pre_roll.append(frame)
            return SegmentResult(
                rms=rms,
                speech_active=self._speech_active,
                speech_started=speech_started,
            )

        self._current.append(frame)
        if is_speech:
            self._below_threshold_frames = 0
        else:
            self._below_threshold_frames += 1

        if len(self._current) >= self.max_utterance_frames:
            audio = self.current_audio()
            force_flushed = True
            speech_ended = True
            self._rollover()
        elif self._below_threshold_frames >= self.trailing_silence_frames:
            if len(self._current) >= self.min_utterance_frames:
                audio = self.current_audio()
                speech_ended = True
            self.reset()

        return SegmentResult(
            rms=rms,
            speech_active=self._speech_active,
            speech_started=speech_started,
            speech_ended=speech_ended,
            force_flushed=force_flushed,
            audio=audio,
        )


class SileroVAD(RMSGate):
    """Silero-backed VAD with the same streaming output contract as RMSGate."""

    def __init__(
        self,
        threshold: float = 0.5,
        speech_pad_ms: int = 300,
        min_silence_ms: int = 400,
        min_utterance_ms: int = 800,
        max_utterance_s: float = 25.0,
    ) -> None:
        super().__init__(
            threshold=0.01,
            trailing_silence_ms=min_silence_ms,
            min_utterance_ms=min_utterance_ms,
            max_utterance_s=max_utterance_s,
            overlap_s=speech_pad_ms / 1000,
        )
        try:
            import torch
            from silero_vad import VADIterator, load_silero_vad
        except Exception as exc:  # pragma: no cover - only hit when optional deps are absent
            raise RuntimeError(
                "Silero VAD requested, but silero-vad/torch is not available. "
                "Install backend dependencies with uv sync."
            ) from exc

        self._torch = torch
        self._vad_iterator = VADIterator(
            load_silero_vad(),
            threshold=threshold,
            sampling_rate=SAMPLE_RATE,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=speech_pad_ms,
        )
        self._pending = np.zeros(0, dtype=np.float32)
        self._silero_active = False
        self._silence_flush_frames = max(1, min_silence_ms // 20)
        self._silent_frames = 0

    def reset(self) -> None:
        super().reset()
        self._silero_active = False
        self._silent_frames = 0
        try:
            self._vad_iterator.reset_states()
        except AttributeError:
            pass

    def _rollover(self) -> None:
        # A size limit is not end-of-speech. Carry the detector state and pending
        # samples into the next chunk, with the existing audio overlap.
        RMSGate.reset(self)

    def ingest(self, frame: np.ndarray) -> SegmentResult:
        frame = _coerce_frame(frame)
        rms = float(sqrt(float(np.mean(np.square(frame)))))
        self._pending = np.concatenate([self._pending, frame])
        event: dict | None = None
        while self._pending.shape[0] >= 512:
            chunk = self._pending[:512]
            self._pending = self._pending[512:]
            tensor = self._torch.from_numpy(chunk)
            maybe_event = self._vad_iterator(tensor, return_seconds=False)
            if maybe_event:
                event = maybe_event

        parent_threshold = self.threshold
        if event and "start" in event:
            self._silero_active = True
            self._silent_frames = 0
        if self._silero_active:
            self._silent_frames = self._silent_frames + 1 if rms <= 0.001 else 0
        else:
            self._silent_frames = 0
        end_event = bool(event and "end" in event) or (
            self._silero_active and self._silent_frames >= self._silence_flush_frames
        )
        active_for_parent = self._silero_active
        self.threshold = -1.0 if active_for_parent else 2.0
        result = super().ingest(frame)
        self.threshold = parent_threshold
        if end_event and result.force_flushed:
            # Real end-of-speech on the cap frame still resets the detector.
            self.reset()
        if end_event and not result.speech_ended:
            audio = self.current_audio()
            speech_ended = len(self._current) >= self.min_utterance_frames
            self.reset()
            return SegmentResult(
                rms=result.rms,
                speech_active=False,
                speech_started=result.speech_started,
                speech_ended=speech_ended,
                audio=audio if speech_ended else None,
            )
        return result


def make_segmenter(
    name: str,
    rms_threshold: float = 0.01,
    *,
    silero_threshold: float = 0.5,
    speech_pad_ms: int = 300,
    min_silence_ms: int = 400,
    max_utterance_s: float = 25.0,
) -> RMSGate:
    if name == "silero":
        return SileroVAD(
            threshold=silero_threshold,
            speech_pad_ms=speech_pad_ms,
            min_silence_ms=min_silence_ms,
            max_utterance_s=max_utterance_s,
        )
    raise ValueError("Only the Silero VAD segmenter is enabled for this build.")


def _coerce_frame(frame: np.ndarray) -> np.ndarray:
    frame = np.asarray(frame, dtype=np.float32).reshape(-1)
    if frame.shape[0] != FRAME_SAMPLES:
        raise ValueError(f"Expected {FRAME_SAMPLES} samples per frame, got {frame.shape[0]}")
    return np.clip(frame, -1.0, 1.0).astype(np.float32, copy=False)
