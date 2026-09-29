from __future__ import annotations

import re
from pathlib import Path

from protocol import ConfigMessage, SUPPORTED_MVP_LANGUAGES


ROOT = Path(__file__).resolve().parents[3]
SWIFT_PROTOCOL = ROOT / "macos" / "LiveTR3Mac" / "Sources" / "LiveTR3Mac" / "LiveTR3Protocol.swift"


def _swift_source() -> str:
    return SWIFT_PROTOCOL.read_text(encoding="utf-8")


def test_swift_and_backend_expose_the_same_language_set() -> None:
    source = _swift_source()
    language_block = source.split("enum LiveTR3Language", 1)[1].split("struct ClientConfig", 1)[0]
    swift_languages = re.findall(r'case\s+\w+\s*=\s*"([^"]+)"', language_block)
    assert swift_languages == SUPPORTED_MVP_LANGUAGES


def test_swift_client_stays_on_protocol_version_2() -> None:
    source = _swift_source()
    assert re.search(r"var\s+version:\s*Int\s*=\s*2\b", source)
    assert 'var segmenter: String = "silero"' in source


def test_backend_accepts_the_native_clients_current_default_config() -> None:
    native_default = {
        "type": "config",
        "version": 2,
        "source_lang": "English",
        "target_lang": "Spanish",
        "custom_vocab": [],
        "segmenter": "silero",
        "polish_enabled": False,
        "code_switching_enabled": False,
        "partial_interval_seconds": 0.25,
        "max_utterance_seconds": 12.0,
        "silero_threshold": 0.5,
        "speech_pad_ms": 300,
        "min_silence_ms": 150,
        "early_commit_enabled": False,
        "early_commit_min_seconds": 1.0,
        "early_commit_punctuation": True,
        "early_commit_stability": True,
        "stability_window": 2,
    }
    parsed = ConfigMessage.model_validate(native_default)
    assert parsed.version == 2
    assert parsed.source_lang == "English"
    assert parsed.target_lang == "Spanish"
    assert parsed.segmenter == "silero"
