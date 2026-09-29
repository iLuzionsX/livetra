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

## What this does and does not establish

Absolute seconds here reflect 3.1 s synthetic utterances with 0.3 s pauses. The
recorded-passage evidence in `stall-comparison-2026-09-25.json` shows much slower
finals (3.81 s to a translated caption) on longer speech. The structural findings
above are the durable ones; the absolute costs are not.

The largest remaining opportunity is the final decode itself, which is roughly
half of all inference. The untested idea is to let the in-flight preview finish
at commit and promote its output to the final instead of cancelling it. That
would remove most of the final decode, at the cost of a final caption built from
audio that predates the last few hundred milliseconds of speech. It changes
caption semantics, so it needs a decision before it gets an experiment.
