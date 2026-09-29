import numpy as np

from segmenter import FRAME_SAMPLES, RMSGate


def test_max_utterance_is_limited_to_29_seconds_below_gemma_window():
    assert RMSGate(max_utterance_s=29.0).max_utterance_frames == 29 * 50
    segmenter = RMSGate(max_utterance_s=30.0)
    frame = np.full(FRAME_SAMPLES, 0.1, dtype=np.float32)

    assert segmenter.max_utterance_frames == 29 * 50
    for index in range(segmenter.max_utterance_frames):
        result = segmenter.ingest(frame)
        assert result.force_flushed is (index == segmenter.max_utterance_frames - 1)

    assert result.audio is not None
    assert result.audio.size == 29 * 16_000


def test_size_rollover_repeats_only_the_configured_overlap_and_loses_no_frames():
    frames = [
        np.full(FRAME_SAMPLES, amplitude, dtype=np.float32)
        for amplitude in (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4)
    ]
    segmenter = RMSGate(max_utterance_s=0.1, overlap_s=0.04)

    first_audio = None
    for frame in frames[:5]:
        result = segmenter.ingest(frame)
        if result.force_flushed:
            first_audio = result.audio

    assert first_audio is not None
    np.testing.assert_array_equal(first_audio, np.concatenate(frames[:5]))

    for frame in frames[5:8]:
        result = segmenter.ingest(frame)

    assert result.force_flushed
    assert result.audio is not None
    expected_second_audio = np.concatenate(frames[3:8])
    np.testing.assert_array_equal(result.audio, expected_second_audio)

    # Removing the two-frame overlap reconstructs the input exactly once.
    reconstructed = np.concatenate([first_audio, result.audio[2 * FRAME_SAMPLES :]])
    np.testing.assert_array_equal(reconstructed, np.concatenate(frames[:8]))
