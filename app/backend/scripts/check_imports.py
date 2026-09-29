from __future__ import annotations

from mlx_worker import MLXWorkerService
from protocol import (
    CommitNowMessage,
    ConfigMessage,
    ErrorMessage,
    JoinViewerMessage,
    LevelMessage,
    ResumeSessionMessage,
    SkipPolishMessage,
    SpeechStartMessage,
    StartMessage,
    StatusMessage,
    StopMessage,
    TranscriptMessage,
)
from segmenter import make_segmenter
from server import app
from session import SessionState


def main() -> None:
    assert app.title
    assert SessionState
    assert MLXWorkerService
    assert make_segmenter
    assert ConfigMessage
    assert StartMessage
    assert StopMessage
    assert ResumeSessionMessage
    assert CommitNowMessage
    assert SkipPolishMessage
    assert JoinViewerMessage
    assert SpeechStartMessage
    assert TranscriptMessage
    assert LevelMessage
    assert ErrorMessage
    assert StatusMessage
    print("OK")


if __name__ == "__main__":
    main()
