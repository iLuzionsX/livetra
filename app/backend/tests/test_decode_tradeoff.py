"""Pairing commits with the preview that was decoding when they landed.

`scripts/decode_tradeoff.py` decides the promote-a-preview trade from this
pairing, so a wrong pairing would quietly turn a measured trade into a guess.
"""

from scripts.decode_tradeoff import per_utterance


def records(decodes: list[dict], commits: list[dict]) -> dict:
    return {"decodes": decodes, "commits": commits}


def partial(utterance_id: int, audio_seconds: float, outcome: str = "cancelled") -> dict:
    return {
        "priority": "partial",
        "utterance_id": utterance_id,
        "audio_seconds": audio_seconds,
        "outcome": outcome,
        "inference_seconds": 0.7,
    }


def commit(utterance_id: int, audio_seconds: float, voiced_seconds: float) -> dict:
    return {
        "utterance_id": utterance_id,
        "audio_seconds": audio_seconds,
        "voiced_seconds": voiced_seconds,
    }


def test_promoting_a_preview_that_already_heard_everything_costs_no_accuracy():
    # The preview reached the end of the speech; the commit only added silence.
    rows = per_utterance(
        records(
            [partial(1, 2.18), partial(1, 0.82, outcome="partial")],
            [commit(1, 2.74, 2.18)],
        )
    )

    assert len(rows) == 1
    assert rows[0]["preview_audio_seconds"] == 2.18
    assert rows[0]["voiced_missed_seconds"] == 0.0


def test_speech_after_the_last_preview_is_what_promotion_would_lose():
    rows = per_utterance(
        records([partial(1, 2.18)], [commit(1, 3.14, 3.14)]),
    )

    assert rows[0]["voiced_missed_seconds"] == 0.96


def test_pairing_uses_the_in_flight_preview_not_the_early_one():
    rows = per_utterance(
        records(
            [partial(1, 0.82, outcome="partial"), partial(1, 2.18)],
            [commit(1, 2.74, 2.18)],
        )
    )

    assert rows[0]["preview_audio_seconds"] == 2.18
    assert rows[0]["spent_before_cancel_seconds"] == 0.7


def test_an_utterance_with_no_preview_is_skipped_rather_than_guessed():
    assert per_utterance(records([], [commit(1, 2.74, 2.18)])) == []
