from __future__ import annotations

from scripts.recorded_validation import word_error_rate


def test_word_error_rate_exact_match_ignores_case_and_punctuation() -> None:
    assert word_error_rate("Hello, WORLD!", "hello world") == 0.0


def test_word_error_rate_counts_substitution() -> None:
    assert word_error_rate("one two three", "one four three") == 1 / 3


def test_word_error_rate_handles_empty_reference() -> None:
    assert word_error_rate("", "") == 0.0
    assert word_error_rate("", "unexpected words") == 1.0
