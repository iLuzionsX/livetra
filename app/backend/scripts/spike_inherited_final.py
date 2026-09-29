"""Spike: does a final that inherits a preview's state produce the same caption?

The question behind option 2 is not "can the cache be threaded through", it is
"do the captions come out the same". This measures that over a batch of sentences
rather than one, because one sentence proves nothing.

Three arms over each sentence:

* control, the app's own `stream_generate` call, unmodified;
* from scratch, a hand-rolled prefill, which must reproduce the control or the
  harness is wrong and every other number here is worthless;
* inherited, a preview that decoded most of the sentence, had its cache trimmed
  back to the shared prefix, and had the rest of the final prefilled on top.

The inherited arm is the hard version of the idea: the cache it inherits was
filled by a completed generation, so it has to be trimmed rather than merely
continued.

Run from app/backend::

    uv run python -m scripts.spike_inherited_final
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import mlx.core as mx
import numpy as np
import soundfile as sf

# The app's own prompt. A looser one makes the model answer "[Music]" for synthetic
# speech, which would make an equality test pass for the wrong reason.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mlx_worker import AST_PROMPT  # noqa: E402

MODEL_PATH = "mlx-community/gemma-4-e4b-it-8bit"
SAMPLE_RATE = 16_000
PROMPT = AST_PROMPT.format(src="English", tgt="Spanish")
BUDGET = 64

PASSAGE = (
    "Good morning everyone, thanks for joining. [[slnc 400]] "
    "Today I want to walk through three things. [[slnc 400]] "
    "First, the schedule slipped by about a week. [[slnc 400]] "
    "Second, we found two regressions in the caption pipeline. [[slnc 400]] "
    "Third, the projector view still drops frames under load. [[slnc 400]] "
    "None of these are blockers, but they all need owners. [[slnc 400]] "
    "I will follow up with each team this afternoon. [[slnc 400]] "
    "Let us take questions before we close."
)

# How much of the tail the preview is missing, which is the shape the app sees: a
# preview submitted on a tick, then the speaker keeps going, then the commit.
TAIL_HELD_BACK_SECONDS = 0.9
SPLIT_SILENCE_SECONDS = 0.30


def make_passage(directory: Path) -> np.ndarray:
    raw = directory / "passage.wav"
    subprocess.run(
        ["say", "-o", str(raw), f"--data-format=LEF32@{SAMPLE_RATE}", PASSAGE],
        check=True,
        capture_output=True,
    )
    audio, rate = sf.read(raw, dtype="float32", always_2d=True)
    assert rate == SAMPLE_RATE
    return np.ascontiguousarray(audio.mean(axis=1))


def split_utterances(audio: np.ndarray) -> list[np.ndarray]:
    """Cut on silence the way the app cuts on Silero, closely enough for a spike."""
    frame = 320
    count = len(audio) // frame
    rms = np.sqrt(np.mean(np.square(audio[: count * frame].reshape(count, frame)), axis=1))
    quiet = rms < 0.01
    gap = int(SPLIT_SILENCE_SECONDS * SAMPLE_RATE / frame)

    spans: list[tuple[int, int]] = []
    start = None
    run = 0
    for index, is_quiet in enumerate(quiet):
        if not is_quiet:
            if start is None:
                start = index
            run = 0
        elif start is not None:
            run += 1
            if run >= gap:
                spans.append((start, index - run + 1))
                start = None
                run = 0
    if start is not None:
        spans.append((start, count))

    return [audio[a * frame : b * frame] for a, b in spans]


def prepare(model, processor, wav: Path):
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import prepare_inputs, should_add_special_tokens

    formatted = apply_chat_template(processor, model.config, PROMPT, num_audios=1)
    inputs = prepare_inputs(
        processor,
        images=None,
        audio=[str(wav)],
        videos=None,
        prompts=formatted,
        add_special_tokens=should_add_special_tokens(model.config.model_type, processor),
        return_tensors="mlx",
    )
    ids = inputs["input_ids"]
    # Raw names on purpose: get_input_embeddings inverts input_features_mask into an
    # "invalid frames" mask, and handing it over un-inverted silently encodes the
    # padding as speech. Getting this wrong makes every caption read "[Music]".
    features = model.get_input_embeddings(
        input_ids=ids,
        input_features=inputs.get("input_features"),
        input_features_mask=inputs.get("input_features_mask"),
    )
    mx.eval(features.inputs_embeds)
    return np.array(ids[0], copy=False), features


def prefill(model, embeds, per_layer, cache):
    out = model.language_model(
        inputs_embeds=embeds, mask=None, cache=cache, per_layer_inputs=per_layer
    )
    mx.eval(out.logits)
    return out.logits


def decode(model, logits, cache, tokenizer, budget: int) -> list[int]:
    """Greedy decode, which is what the app does at temperature 0."""
    stop = set(tokenizer.all_special_ids or [])
    tokens: list[int] = []
    for _ in range(budget):
        token = int(mx.argmax(logits[0, -1, :]).item())
        if token in stop:
            break
        tokens.append(token)
        step = model.get_input_embeddings(input_ids=mx.array([[token]]))
        out = model.language_model(
            inputs_embeds=step.inputs_embeds,
            mask=None,
            cache=cache,
            per_layer_inputs=step.per_layer_inputs,
        )
        mx.eval(out.logits)
        logits = out.logits
    return tokens


def shared_prefix(short_ids: np.ndarray, long_ids: np.ndarray) -> int:
    limit = min(len(short_ids), len(long_ids))
    for index in range(limit):
        if short_ids[index] != long_ids[index]:
            return index
    return limit


def longest_common_prefix(left: list[int], right: list[int]) -> int:
    count = 0
    for a, b in zip(left, right):
        if a != b:
            break
        count += 1
    return count


def main() -> int:
    from mlx_vlm import load, stream_generate
    from mlx_vlm.models import cache as cache_module
    from mlx_vlm.prompt_utils import apply_chat_template

    with tempfile.TemporaryDirectory(prefix="livetra-spike-") as tmp:
        directory = Path(tmp)
        audio = make_passage(directory)
        utterances = [u for u in split_utterances(audio) if len(u) > 1.2 * SAMPLE_RATE]
        print(f"passage {len(audio) / SAMPLE_RATE:.1f}s, {len(utterances)} utterances")

        print("loading model...", flush=True)
        model, processor = load(MODEL_PATH)
        tokenizer = processor.tokenizer
        formatted = apply_chat_template(processor, model.config, PROMPT, num_audios=1)

        identical = 0
        harness_mismatches = 0
        for number, utterance in enumerate(utterances, start=1):
            hold = int(TAIL_HELD_BACK_SECONDS * SAMPLE_RATE)
            preview = utterance[:-hold]
            final = utterance
            if len(preview) < 0.8 * SAMPLE_RATE:
                continue

            short_wav = directory / f"{number}-preview.wav"
            long_wav = directory / f"{number}-final.wav"
            sf.write(short_wav, preview, SAMPLE_RATE, subtype="FLOAT")
            sf.write(long_wav, final, SAMPLE_RATE, subtype="FLOAT")

            control = "".join(
                piece.text or ""
                for piece in stream_generate(
                    model,
                    processor,
                    formatted,
                    audio=[str(long_wav)],
                    max_tokens=BUDGET,
                    temperature=0.0,
                    top_p=0.95,
                    top_k=64,
                )
            )

            long_ids, long_features = prepare(model, processor, long_wav)
            short_ids, short_features = prepare(model, processor, short_wav)
            prefix_len = shared_prefix(short_ids, long_ids)

            scratch_cache = cache_module.make_prompt_cache(model.language_model)
            scratch_logits = prefill(
                model,
                long_features.inputs_embeds,
                long_features.per_layer_inputs,
                scratch_cache,
            )
            scratch_tokens = decode(model, scratch_logits, scratch_cache, tokenizer, BUDGET)

            inherited_cache = cache_module.make_prompt_cache(model.language_model)
            preview_logits = prefill(
                model,
                short_features.inputs_embeds,
                short_features.per_layer_inputs,
                inherited_cache,
            )
            decode(model, preview_logits, inherited_cache, tokenizer, BUDGET)
            held = int(mx.max(mx.array([c.offset for c in inherited_cache])).item())
            for entry in inherited_cache:
                entry.trim(held - prefix_len)
            inherited_logits = prefill(
                model,
                long_features.inputs_embeds[:, prefix_len:],
                long_features.per_layer_inputs[:, prefix_len:]
                if long_features.per_layer_inputs is not None
                else None,
                inherited_cache,
            )
            inherited_tokens = decode(model, inherited_logits, inherited_cache, tokenizer, BUDGET)

            scratch_text = tokenizer.decode(scratch_tokens)
            inherited_text = tokenizer.decode(inherited_tokens)
            harness_ok = scratch_text == control
            if not harness_ok:
                harness_mismatches += 1
            same = inherited_tokens == scratch_tokens
            if same:
                identical += 1
            shared = longest_common_prefix(scratch_tokens, inherited_tokens)
            print()
            print(f"[{number}] {len(final) / SAMPLE_RATE:.1f}s, prefix {prefix_len} of {len(long_ids)}")
            print(f"    control : {control[:88]!r}")
            if not harness_ok:
                print(f"    HARNESS MISMATCH, scratch: {scratch_text[:88]!r}")
            print(f"    scratch : {scratch_text[:88]!r}")
            print(f"    inherited: {inherited_text[:88]!r}")
            print(f"    identical: {same}" + ("" if same else f"  (agreed for {shared} tokens)"))

        total = len(utterances)
        print()
        print(f"utterances: {total}")
        print(f"harness mismatches vs the app's own call: {harness_mismatches}")
        print(f"inherited identical to from scratch: {identical}/{total}")
        return 0 if identical == total and harness_mismatches == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
