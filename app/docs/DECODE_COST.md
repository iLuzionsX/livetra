# Decode cost findings

Two 240s soaks over one 24.6s continuous `say` passage, 16 kHz mono, English to
Spanish, `PARTIAL_INTERVAL_SECONDS=2`, Silero segmentation. Both runs had 0
errors and 0 failure conditions. Every number below comes from the decode
accounting added alongside this document; see "Decode accounting" in
`app/README.md`. The runs are `soak_2026-09-29T00-57-43-0400.*` (baseline) and
`soak_2026-09-29T01-02-59-0400.*` (coalescing) in `app/docs/evidence/`.

| | baseline | preview coalescing |
|---|---|---|
| finals | 69 | 68 |
| total inference | 193.8 s | 222.7 s |
| final decode, mean | 1.434 s | 1.829 s |
| final latency, median | 4.47 s | 5.23 s |
| final latency, p95 | 6.24 s | 7.12 s |
| silence to final, median | 1.76 s | 2.18 s |
| decodes | 206 | 187 |
| cancelled previews | 63 | 64 |
| finals a completed preview had covered | 0 of 69 | 0 of 68 |

## Per-utterance shape (baseline)

Every utterance runs the same three decodes:

1. a preview on ~0.8 s of audio that **completes** in ~0.63 s and emits ~8 tokens;
2. a preview on ~2.2 s of audio that `finish_partials` **cancels** at commit,
   having spent ~0.75 s of GPU and emitted 0 tokens;
3. the final on ~3.1 s of audio, ~1.43 s, ~25 tokens.

Cumulative: ~47 s of inference (24%) produces a caption that is discarded
unread, and the surviving preview covers about a quarter of the audio the final
decodes.

## Reuse of a completed preview is structurally impossible

The final always decodes audio that no completed preview had covered, in 69 of 69
utterances. The cause is the cancellation policy: the preview covering the end of
an utterance is by definition still decoding when the commit lands, so it is the
one that gets killed. The preview that survives to complete always covers only
the early audio. No amount of reuse bookkeeping changes this while `finish_partials`
cancels the in-flight preview.

`soak_2026-09-28T22-26-20-0400.*`, an earlier run over a different and more
disjointed sample, reproduces both findings on its own audio: 73 of 73 utterances
ran exactly one completing preview, one cancelled preview and one final, and 0 of
73 finals reused a preview. It is kept out of the table above because those two
runs share one passage, and a comparison needs that.

## Preview coalescing was tried and made things worse

Holding a new preview while one is decoding (`PREVIEW_COALESCE_ENABLED`) was
implemented, measured, and reverted. It reduced the decode count by 19 but
**eliminated no cancelled previews** (63 to 64) and made every other number worse:
14.9% more inference, 27.5% slower finals, and 17% worse final latency.

The hypothesis was wrong. Cancelled previews are not the result of submitting a
second preview too close to the first; they are the result of the utterance
ending before any preview covering its tail can finish. Coalescing only ever
blocked a third submission that was not happening, while keeping the GPU loaded
longer, which slowed every decode including the finals.

The largest remaining opportunity is the final decode itself, which is roughly
half of all inference. The untested idea is to let the in-flight preview finish
at commit and promote its output to the final instead of cancelling it. That
changes caption semantics, so it needs a decision before it gets an experiment.

## What promoting would actually cost, measured

`uv run python -m scripts.decode_tradeoff` reads the runs above and prices the
promotion. Two of its numbers change how the decision should be framed.

**The accuracy cost is much smaller than the clip lengths suggest.** The promoted
preview holds 76% of the final's audio, but most of the difference is silence. The
*voiced* speech it would fail to decode is a median of 0.20 s (mean 0.33 s, max
1.54 s) on continuous speech, and 0.00 s on the disjointed sample. A third of
continuous-speech utterances would lose nothing at all, because everything said
was already in the preview. That is a word or two, on two utterances in three —
not a systematically truncated caption.

**Most of the saving is not the final disappearing.** A preview killed at commit
has already spent 0.70 s of GPU and emitted 0 tokens. Promoting saves an estimated
1.02 s of the 2.77 s an utterance costs today (37%, bounded by 25% and 52%) — but
only 0.33 s of that is the final decode that never runs. The other 0.70 s is
prefill that is paid today and discarded. A preview that reaches 0 tokens after
0.70 s also says prefill, not decode, is most of a decode's cost, which is the
opposite of what the 1.13 s to 3.81 s source-to-translation gap in
`stall-comparison-2026-09-25.json` was taken to mean.

So the same waste is reachable two ways. Promotion buys it by publishing a
caption built from slightly less audio. Reusing the killed preview's encoded
prefix for the final buys the same 0.70 s at no accuracy cost, because the final
still decodes the whole utterance — it just stops re-encoding the part it already
encoded. That one depends on whether `mlx-vlm` accepts a caller-supplied prompt
cache, which is a spike rather than a product decision, and it is the cheaper
thing to find out first.

## What this does and does not establish

Absolute seconds here reflect 3.1 s synthetic utterances with 0.3 s pauses. The
recorded-passage evidence in `stall-comparison-2026-09-25.json` shows much slower
finals (3.81 s to a translated caption) on longer speech. The structural findings
above are the durable ones; the absolute costs are not.

The counts are sturdier than the timings, too. Zero reuse across 210 utterances
in three runs, a surviving preview that only ever covers the early audio, and a
killed preview that never emits a token are counts, and they hold in every run.
The seconds are not so safe: median RSS across three runs of identical work
ranged from 2.4 GB to 5.6 GB, which is enough machine-state drift that one run per
arm cannot separate a change from the machine it ran on. The coalescing result is
directionally consistent across nine metrics and worth believing as a warning; it
is not a controlled measurement.

