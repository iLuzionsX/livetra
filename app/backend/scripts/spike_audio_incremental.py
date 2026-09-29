"""Spike: is Gemma 4's audio path incremental enough to reuse a preview's state?

Option 2 for the decode cost work is to stop throwing away the preview that was
decoding when an utterance commits, and have the final pick up from it instead of
re-encoding audio it already encoded. The reading so far says the library will not
hand that over on request, and this script answers the question underneath it: if
the encoder were incremental, the only thing standing in the way is the library's
own guard rather than the model.

The test is the only one that matters. Encode a clip, then encode a longer clip
that starts with exactly the same audio, and compare the embeddings of the
overlapping region. If appending audio changes the embeddings of the frames that
came before it, no cache can be carried across the two calls and the idea is dead
regardless of what the library allows. If they are identical, the model is
genuinely streaming and the block is the library's conservatism.

Result on gemma-4-e4b-it-8bit, 3.14s clip against the same clip extended to 4.20s:
they are not identical. 29 of 79 shared frames differ by more than 1e-5 and the
largest difference is 13.9, so the audio path is not embedding-preserving and the
conformer encoder's chunked local attention does not make it so. The captions
nevertheless came out the same; see `spike_inherited_final.py` for that.

Run from app/backend with the same interpreter the worker uses::

    uv run python -m scripts.spike_audio_incremental
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import mlx.core as mx
import numpy as np
import soundfile as sf

MODEL_PATH = "mlx-community/gemma-4-e4b-it-8bit"
SAMPLE_RATE = 16_000

# The passage the soaks use, so the numbers land on the same speech the decision
# was priced against.
PASSAGE = (
    "Good morning everyone, thanks for joining. [[slnc 300]] Today I want to walk "
    "through three things. [[slnc 300]] First, the schedule slipped by about a week. "
    "[[slnc 300]] Second, we found two regressions in the caption pipeline. [[slnc 300]] "
    "Third, the projector view still drops frames under load. [[slnc 300]] None of "
    "these are blockers, but they all need owners."
)


def make_audio(directory: Path) -> np.ndarray:
    raw = directory / "passage.wav"
    subprocess.run(
        ["say", "-o", str(raw), f"--data-format=LEF32@{SAMPLE_RATE}", PASSAGE],
        check=True,
        capture_output=True,
    )
    audio, rate = sf.read(raw, dtype="float32", always_2d=True)
    assert rate == SAMPLE_RATE
    return np.ascontiguousarray(audio.mean(axis=1))


def embed(model, processor, wav: Path):
    """The embeddings the language model would see, audio soft tokens included."""
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import prepare_inputs, should_add_special_tokens

    formatted = apply_chat_template(
        processor,
        model.config,
        "Transcribe the audio and translate it to Spanish.",
        num_audios=1,
    )
    inputs = prepare_inputs(
        processor,
        images=None,
        audio=[str(wav)],
        videos=None,
        prompts=formatted,
        add_special_tokens=should_add_special_tokens(model.config.model_type, processor),
        return_tensors="mlx",
    )
    audio_mask = inputs["input_ids"] == model.config.audio_token_id
    # Raw names on purpose: get_input_embeddings inverts input_features_mask into an
    # "invalid frames" mask, and passing it through un-inverted encodes padding.
    features = model.get_input_embeddings(
        input_ids=inputs["input_ids"],
        input_features=inputs.get("input_features"),
        input_features_mask=inputs.get("input_features_mask"),
    )
    mx.eval(features.inputs_embeds)
    # bfloat16 has no numpy equivalent, so the comparison stays in MLX.
    return features.inputs_embeds, np.array(audio_mask, copy=False)


def main() -> int:
    from mlx_vlm import load

    with tempfile.TemporaryDirectory(prefix="livetra-spike-") as tmp:
        directory = Path(tmp)
        audio = make_audio(directory)
        print(f"audio: {len(audio) / SAMPLE_RATE:.2f}s")

        # A preview that covered most of an utterance, and the final that covers all
        # of it: the preview's audio is a strict prefix of the final's, which is the
        # only shape where a carried-over cache could mean anything.
        prefix_seconds = 3.14
        full_seconds = 4.20
        short = directory / "short.wav"
        long = directory / "long.wav"
        sf.write(short, audio[: int(prefix_seconds * SAMPLE_RATE)], SAMPLE_RATE, subtype="FLOAT")
        sf.write(long, audio[: int(full_seconds * SAMPLE_RATE)], SAMPLE_RATE, subtype="FLOAT")

        print("loading model...", flush=True)
        model, processor = load(MODEL_PATH)

        short_embeds, short_mask = embed(model, processor, short)
        long_embeds, long_mask = embed(model, processor, long)

        def audio_rows(embeds, mask):
            rows = mx.take(embeds[0], mx.array(np.flatnonzero(mask[0])), axis=0)
            mx.eval(rows)
            return rows

        short_audio = audio_rows(short_embeds, short_mask)
        long_audio = audio_rows(long_embeds, long_mask)
        print(f"audio soft tokens: preview {short_audio.shape[0]}, final {long_audio.shape[0]}")

        overlap = min(short_audio.shape[0], long_audio.shape[0])
        diff = mx.abs(short_audio[:overlap] - long_audio[:overlap])
        per_frame = diff.max(axis=-1)
        mx.eval(diff, per_frame)

        print()
        print(f"overlapping frames compared: {overlap}")
        print(f"max abs difference: {diff.max().item():.3e}")
        print(f"mean abs difference: {diff.mean().item():.3e}")
        for tolerance in (1e-5, 1e-4, 1e-3, 1e-2):
            changed = int((per_frame > tolerance).sum().item())
            print(f"  frames differing by more than {tolerance:g}: {changed}/{overlap}"
                  f" ({changed / overlap * 100:.1f}%)")
        changed = int((per_frame > 1e-3).sum().item())
        if changed:
            first_bad = int(mx.argmax((per_frame > 1e-3).astype(mx.int32)).item())
            last_bad = overlap - 1 - int(
                mx.argmax(mx.flip((per_frame > 1e-3).astype(mx.int32), axis=0)).item()
            )
            print(f"  first divergent frame: {first_bad} of {overlap} from the start")
            print(f"  last divergent frame:  {last_bad} of {overlap} from the start")
            print(f"  clean prefix: {first_bad} frames, clean tail: {overlap - 1 - last_bad} frames")
        else:
            print("  every shared frame is identical: the audio path is incremental")

    return 0


if __name__ == "__main__":
    sys.exit(main())
