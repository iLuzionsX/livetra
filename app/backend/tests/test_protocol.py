from __future__ import annotations

import pytest
from pydantic import ValidationError

from protocol import (
    CommitNowMessage,
    ConfigMessage,
    JoinViewerMessage,
    ResumeSessionMessage,
    SkipPolishMessage,
    StartMessage,
    StatusMessage,
    StopMessage,
    TranscriptMessage,
    parse_control_message,
)


def test_default_config_matches_v2_contract() -> None:
    config = ConfigMessage()
    assert config.type == "config"
    assert config.version == 2
    assert config.source_lang == "English"
    assert config.target_lang == "Spanish"
    assert config.segmenter == "silero"
    assert config.custom_vocab == []
    assert config.polish_enabled is False
    assert config.apply_target == "immediate"


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        ({"type": "start"}, StartMessage),
        ({"type": "stop"}, StopMessage),
        ({"type": "resume"}, ResumeSessionMessage),
        ({"type": "commit_now"}, CommitNowMessage),
        ({"type": "skip_polish"}, SkipPolishMessage),
        ({"type": "join_viewer"}, JoinViewerMessage),
    ],
)
def test_control_messages_parse(payload: dict[str, str], expected_type: type) -> None:
    assert isinstance(parse_control_message(payload), expected_type)


def test_config_control_message_round_trips_runtime_tunables() -> None:
    payload = {
        "type": "config",
        "version": 2,
        "source_lang": "Spanish",
        "target_lang": "English",
        "custom_vocab": ["LiveTR3", "Bronx"],
        "segmenter": "silero",
        "polish_enabled": True,
        "apply_target": "next_utterance",
        "code_switching_enabled": True,
        "partial_interval_seconds": 0.25,
        "max_utterance_seconds": 12.0,
        "silero_threshold": 0.55,
        "speech_pad_ms": 300,
        "min_silence_ms": 150,
        "early_commit_enabled": True,
        "early_commit_min_seconds": 1.0,
        "early_commit_punctuation": True,
        "early_commit_stability": True,
        "stability_window": 2,
    }
    parsed = parse_control_message(payload)
    assert isinstance(parsed, ConfigMessage)
    assert parsed.model_dump(exclude_none=True, exclude_unset=True) == payload


def test_unknown_control_message_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown control message type"):
        parse_control_message({"type": "launch_missiles"})


def test_config_rejects_non_silero_segmenter() -> None:
    with pytest.raises(ValidationError):
        ConfigMessage(segmenter="rms")


@pytest.mark.parametrize(
    "commit_reason",
    ["punctuation", "stability", "silero_end", "max_utterance_cap", None],
)
def test_transcript_message_accepts_documented_commit_reasons(commit_reason: str | None) -> None:
    message = TranscriptMessage(
        type="final",
        utterance_id=42,
        original="Hello world.",
        translation="Hola mundo.",
        commit_reason=commit_reason,
    )
    assert message.utterance_id == 42
    assert message.commit_reason == commit_reason


def test_transcript_message_rejects_unknown_commit_reason() -> None:
    with pytest.raises(ValidationError):
        TranscriptMessage(
            type="final",
            utterance_id=42,
            original="Hello",
            translation="Hola",
            commit_reason="timer",
        )


@pytest.mark.parametrize("state", ["starting", "ready", "recovering", "failed"])
def test_status_message_accepts_worker_states(state: str) -> None:
    status = StatusMessage(state=state, message="state changed")
    assert status.state == state


def test_status_message_rejects_unknown_worker_state() -> None:
    with pytest.raises(ValidationError):
        StatusMessage(state="stuck", message="bad state")
