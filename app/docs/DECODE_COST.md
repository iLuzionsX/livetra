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
not a systematically truncated caption. Once the gate existed, its own
accounting measured the real in-flight gap at close to three times this on a
comparable passage; the correction is in the promotion section below.

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
prefix for the final looks like it buys the same 0.70 s at no accuracy cost,
because the final still decodes the whole utterance. The next section is that
attempt, and it fails.

## The prefix-reuse spike: yes, with a caveat

`uv run python -m scripts.spike_inherited_final` answers the three questions that
decide whether that is buildable. Two of the three came back clean and one did
not.

**The library does accept a saved state.** `stream_generate` takes
`prompt_cache_state`, a raw `prompt_cache`, or a full `apc_manager`, and the cache
plumbing is public. It will not, however, hand it over on request: reuse is gated
on `prefix_leaves_text_only_suffix`, which requires the cached prefix to cover
*every* media placeholder token, because "until media-feature slicing is
model-aware, restored prefixes must include every media placeholder token so the
suffix can be embedded as text-only" (`apc.py`). A final that heard more audio
than its preview has exactly the suffix this refuses. Any implementation has to
slice the audio features itself and override the guard deliberately.

**The model is not incremental, though.** Encoding a 3.14 s clip and the same
clip extended to 4.20 s and comparing the shared region, 29 of 79 audio frames
differ by more than 1e-5 and the largest difference is 13.9
(`scripts/spike_audio_incremental.py`). The conformer encoder's chunked local
attention does not make the embeddings boundary-stable, so a reused prefix is not
carrying the values a from-scratch final would compute. Attention is at least
causal over an audio-only prompt — the bidirectional overlay is gated on
`has_visual_tokens` and excludes audio token type 3 — so nothing in the mask
makes it worse.

**The captions still came out the same.** Over 7 sentences, with the inherited
arm going through the hard path (a completed generation, then `trim()` back to
the shared prefix, then the final's remainder prefilled on top), all 7 finals were
token-for-token identical to a from-scratch final. So the perturbation the
embeddings do suffer is small enough that greedy decoding does not notice, on this
audio.

That last result is the one to be careful with, because the margin is not proven.
The same model forked its Spanish phrasing on 1 of the same 7 sentences —
"alrededor de una semana" against "por aproximadamente una semana" — purely from a
different prefill chunking, with identical inputs. Near-ties exist in this output,
so a reused final that lands near one can diverge. The 7/7 is encouraging and it
is not a guarantee, and it is measured on synthetic `say` audio rather than a
speaker.

## Prefix reuse was built, measured, and rejected

The 7/7 was encouraging enough to build it, so it was implemented behind
`AST_REUSE_ENABLED` (off by default) and run against the same passage twice, 150 s
each, once off and once on.

It works. 44 of 44 finals continued from a preview's cache, reusing 131 to 319
prefix tokens each, with 0 errors in both arms.

**The captions are corrupted anyway.** Comparing the two sessions' archives
utterance by utterance: the source transcript was identical in 43 of 44, and the
translation in **0 of 44** — every one truncated mid-sentence. "Gracias por
unirse." came back as "Gracias por". "Hoy quiero repasar tres cosas." came back as
"Hoy quiero repasar tres."

The cause is the guard described above, and bypassing it is what broke it.
`_prefix_cache_trim_amount` refuses to trim a cache it cannot trim safely, because
Gemma 4's sliding-window attention layers make the cache a ring buffer: trimming it
by a logical length "leaves the ring index stale: silent output corruption". The
implementation checked only that a `trim` method existed and trimmed anyway. The
corrupted state is not a crash, it is a model that quietly loses the thread and
emits an early end-of-turn, which is exactly a truncated translation.

That guard is load-bearing. It is not conservatism to be argued with on the
strength of 7 agreeing sentences.

**The saving was also smaller than projected, for a separate reason.** The final
decode went from 1.282 s to 1.169 s (8.8%), the caption after the pause from a
1.762 s median to 1.596 s, and total inference fell 3.7% — against the 25% to 37%
estimated above. The reason is that reusing the KV cache does not reuse the audio
encoding: `get_input_embeddings` re-runs the conformer over the whole clip, and
that is where most of the prefill cost is. The library offers no way to inject
precomputed audio soft tokens, because its `cached_*` hook is wired for images and
video only. So even a correct implementation of this would be worth single-digit
percent, not a third.

Both halves of the idea therefore fail: the captions break, and the prize is
small. `AST_REUSE_ENABLED` does not exist in the tree. The durable findings from it
are the two sentences above and the fact that the audio tower, not the language
model prefill, is the cost worth attacking.

## Publishing a completed preview was built, tested, and removed

The remaining cheap idea was to publish a completed preview as the final when it
had already decoded exactly the commit's voiced content — same speech, same
prompt, no re-decode. It was implemented with guards (complete previews only,
exact voiced-signature match, prompt settings unchanged since the preview) and
unit-tested, and then measured: its trigger condition occurs **0 times in over
300 utterances**, across five soaks and three single-word sessions.

The reason is the same structural one as above. A preview that covers the tail
of an utterance is still decoding when the commit lands, so it is killed, not
completed. The previews that survive to complete only ever cover the early
audio, and the match requires hash equality, so even a tenth of a second of new
speech breaks it. An earlier estimate of "a third of utterances" was a
miscategorisation: that third was the *in-flight* preview holding everything,
which is the promote-and-truncate trade, not this one. The code was removed
rather than kept behind a flag. Unexercised paths rot, and the one time this
repo kept a flag for a losing idea it had to be reverted anyway.

The same work fixed a real bug found along the way: utterance ids restart at 1
in every session, but the worker retired them globally, so after one session
ended every later session's previews were dropped unheard. Retirement keys are
now `(session_id, utterance_id)`, verified by running three sessions against one
backend and confirming each gets live previews, and by showing the old code
drops the second session's. The cross-process payload is also pinned by test to
carry only arguments the worker accepts, after a routing key leaked across it
and crashed the worker on every decode until caught by a soak.

## The trim is in; promotion was built, measured, and removed

Two changes came out of the findings above. One is in the tree for good. The
other was built behind a flag, priced by four more soaks, and removed.

**The final decodes the same transcribable clip the previews decode.** It used
to re-encode the commit's raw audio, including the VAD's trailing-silence
window, while every preview decoded the trimmed span. The final now sends the
trimmed clip, so its conformer cost covers speech plus the 0.3 s pad and
nothing else. No semantic change: the voiced span is byte-identical by the
signature the ledger already computes, and commit and decode records describe
the same clip. Its seconds were never isolated — every arm below carries it —
so the new baseline numbers absorb it rather than attribute it.

**In-flight promotion was built behind `PROMOTE_INFLIGHT_ENABLED`.** At commit,
when a preview for the utterance was mid-decode and the voiced speech its clip
had not covered was at most `PROMOTE_INFLIGHT_MAX_GAP_SECONDS` (0.30 s), the
commit would let it finish and publish its output as the final caption instead
of cancelling its spent prefill and re-decoding the utterance, falling back to
the real final whenever the preview came back unusable. The build under test
also recorded, at every commit, the gap the gate evaluated — accepted or
refused — so a soak that promoted nothing could say why.

The soaks ran over a new passage built for the purpose: eight `say` sentences
(~3.1 s each) separated by explicit 350 ms silences (`[[slnc 350]]`), 27.6 s
looped for 240 s per arm, 61 finals each, 0 errors, 0 failures. The original
passage is gone from the tree, and a plain `say` voice does not pause long
enough at periods for Silero to commit every sentence, so the explicit silences
are what makes this workload reproducible. Four arms: the passage twice at
`PARTIAL_INTERVAL_SECONDS=2`, then twice at the native 250 ms cadence, each pair
once with promotion off and once on. Evidence:
`soak_2026-09-29T20-34-28-0400.*` and `soak_2026-09-29T20-39-38-0400.*` (2 s
cadence, off and on), `soak_2026-09-29T20-47-03-0400.*` and
`soak_2026-09-29T20-52-11-0400.*` (native, off and on), in `app/docs/evidence/`.

| | 2 s off | 2 s on | native off | native on |
|---|---|---|---|---|
| finals | 61 | 61 | 61 | 61 |
| promotions | 0 | 0 | 0 | 0 |
| in-flight at commit, refused | — | 29 | — | 30 |
| refused gap, median / max | — | 0.56 / 0.74 s | — | 0.56 / 0.74 s |
| silence to final, median | 1.810 s | 1.806 s | 1.813 s | 1.818 s |
| final decode, mean | 1.462 s | 1.462 s | 1.471 s | 1.465 s |
| total inference | 187.6 s | 187.8 s | 188.5 s | 188.1 s |

**The 0.20 s premise was wrong, and the gap is not a scheduling artifact.**
The gate refused every commit it evaluated, at both cadences, because the real
in-flight gap is a median of 0.56 s (max 0.74 s) — close to three times the
0.20 s the proxy had measured on the original passage. The proxy recomputed
over these same runs agrees (median 0.58 s), so the finding does not depend on
the refusal accounting that left the tree with the feature. The identical
refusal distributions at 2 s and 250 ms preview intervals say why: the worker
decodes one job at a time, so the preview mid-decode at commit was submitted
roughly one decode turnaround before the commit. The gap tracks turnaround,
not the configured interval; no preview-cadence knob closes it.

**Half the commits had nothing to promote.** Only 29 and 30 of 61 commits had
a preview mid-decode at all — the rest had completed their last preview during
the trailing pause. Even ungated, promotion could never have covered more than
about half the commits, and each promoted caption would have missed a median
0.56 s of tail speech — about two words at this passage's rate, words the next
utterance's caption never recovers.

**The bound held where promotion would have been most dangerous.** Two earlier
240 s runs over a passage whose sentences merged into ~11.4 s utterances
(`soak_2026-09-29T20-18-24-0400.*` and `soak_2026-09-29T20-23-38-0400.*`, 19
finals each, 9 of them at the 12 s cap) priced the in-flight gap at a median
of 1.6 s. The gate refused all of them — exactly the utterances where a
promoted caption would have been most visibly truncated.

**The dormant flag was measurably free.** With promotion on but never firing,
latency, inference, and every caption were indistinguishable from baseline;
the archives are identical, 61 of 61, in both pairs.

So the trade the 37% estimate priced does not exist on this hardware: at the
0.30 s bound the trigger fires 0 times in 122 commits, and the threshold that
would make it fire — 0.56 s and up — is the systematic tail truncation that
removed publish-completed-preview and reverted prefix reuse. The feature was
removed rather than kept behind a flag, per this document's precedent:
unexercised paths rot, and this one is measured not to exercise. The durable
findings are the turnaround-bound gap law, the half-the-commits ceiling, and
the wake condition the law implies: promotion becomes live when a preview's
decode turnaround falls to roughly 0.6 s — about twice as fast as these runs —
at which point the median gap can meet the 0.30 s bound. The pricing that
would justify rebuilding it is `scripts/decode_tradeoff` over any newer soak.

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

