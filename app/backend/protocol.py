from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SUPPORTED_MVP_LANGUAGES = [
    "English",
    "Spanish",
    "French",
    "German",
    "Italian",
    "Portuguese",
    "Japanese",
    "Korean",
    "Mandarin",
    "Arabic",
]


class ConfigMessage(BaseModel):
    type: Literal["config"] = "config"
    version: int = 2
    source_lang: str = "English"
    target_lang: str = "Spanish"
    custom_vocab: list[str] = Field(default_factory=list)
    segmenter: Literal["silero"] = "silero"
    polish_enabled: bool = False
    rms_threshold: float | None = None
    apply_target: Literal["immediate", "next_utterance"] = "immediate"
    input_device_id: str | None = None
    input_device_label: str | None = None
    code_switching_enabled: bool = False
    partial_interval_seconds: float | None = None
    max_utterance_seconds: float | None = None
    silero_threshold: float | None = None
    speech_pad_ms: int | None = None
    min_silence_ms: int | None = None
    early_commit_enabled: bool | None = None
    early_commit_min_seconds: float | None = None
    early_commit_punctuation: bool | None = None
    early_commit_stability: bool | None = None
    stability_window: int | None = None


class StartMessage(BaseModel):
    type: Literal["start"]


class StopMessage(BaseModel):
    type: Literal["stop"]


class ResumeSessionMessage(BaseModel):
    type: Literal["resume"]


class CommitNowMessage(BaseModel):
    type: Literal["commit_now"]


class SkipPolishMessage(BaseModel):
    type: Literal["skip_polish"]


class JoinViewerMessage(BaseModel):
    type: Literal["join_viewer"]


ClientControlMessage = (
    ConfigMessage
    | StartMessage
    | StopMessage
    | ResumeSessionMessage
    | CommitNowMessage
    | SkipPolishMessage
    | JoinViewerMessage
)


class SpeechStartMessage(BaseModel):
    type: Literal["speech_start"] = "speech_start"
    utterance_id: int


class TranscriptMessage(BaseModel):
    type: Literal["partial", "final", "polished"]
    utterance_id: int
    original: str
    translation: str
    commit_reason: Literal["punctuation", "stability", "silero_end", "max_utterance_cap"] | None = None
    last_audio_frame_unix_seconds: float | None = None


class LevelMessage(BaseModel):
    type: Literal["level"] = "level"
    rms: float


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    message: str


class StatusMessage(BaseModel):
    type: Literal["status"] = "status"
    state: Literal["starting", "ready", "recovering", "failed"]
    message: str


def parse_control_message(payload: dict) -> ClientControlMessage:
    msg_type = payload.get("type")
    if msg_type == "config":
        return ConfigMessage.model_validate(payload)
    if msg_type == "start":
        return StartMessage.model_validate(payload)
    if msg_type == "stop":
        return StopMessage.model_validate(payload)
    if msg_type == "resume":
        return ResumeSessionMessage.model_validate(payload)
    if msg_type == "commit_now":
        return CommitNowMessage.model_validate(payload)
    if msg_type == "skip_polish":
        return SkipPolishMessage.model_validate(payload)
    if msg_type == "join_viewer":
        return JoinViewerMessage.model_validate(payload)
    raise ValueError(f"Unknown control message type: {msg_type!r}")
