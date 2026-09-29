# Native pipeline validation — 2026-09-25

- Backend: 57 tests passed; import gate passed.
- Swift: 13 tests passed, including continuous resampling at 44.1/48 kHz and
  rejection of a 12 kHz tone when converting to 16 kHz.
- Release application built and launched; native UI showed Ready with English
  to Spanish selected.
- Offline local-engine test used `sample.wav` (5.232875 seconds), real-time
  20 ms PCM frames over a temporary Unix socket, Parakeet ASR, Gemma translation,
  250 ms partial scheduling, 300 ms silence threshold, and no early commit.
  This silence threshold differs from the app's 150 ms default.
- Latest end-to-end replay: source final arrived 1.42 seconds after audio end;
  Spanish final arrived 3.53 seconds after audio end, with no error messages.
  An earlier replay measured 0.53 / 3.34 seconds respectively. These are two
  observations, not percentiles or a latency guarantee.
- A warm standalone Parakeet decode of that sample took 223 ms before the MLX
  dependency upgrade. It is an ASR-only observation, not end-to-end latency.

The existing cached Gemma model failed to load with mlx-vlm 0.4.4 because its
quantized per-layer projection was unsupported. Updating the locked dependency
to 0.7.3 resolved that failure in the real engine test.

These checks do not establish word error rate, translation accuracy across
languages, live microphone quality, sustained thermal performance, or a
self-contained distributable app. A representative recording corpus with human
references is still needed for an accuracy benchmark.

## Intermittent-stall follow-up

A before/after Unix-socket replay sent the same 5.232875-second sample six times
with 500 ms of silence between repetitions, at real-time speed. Both runs used
250 ms partial scheduling and a 300 ms silence threshold. The baseline was a copy
of the backend immediately before the stall fixes; both used the same installed
models/dependencies. No errors were emitted in either run.

| Observed metric | Before | After |
| --- | ---: | ---: |
| Median source final after audio end | 1.36 s | 1.13 s |
| Median translation final after audio end | 10.29 s | 3.81 s |
| Worst translation final after audio end | 20.26 s | 4.74 s |
| Largest gap between source partial messages within one utterance | 0.66 s | 0.58 s |

The first source partial for utterances 2–6 arrived 1.11–1.39 seconds after the
recording began before the changes, versus 0.75–1.11 seconds afterward. These
figures come from one paired replay, not a broad latency distribution. The replay
reproduced a translation backlog, not a multi-second mid-utterance source freeze.

Fixes remove cross-utterance partial blocking, prioritize final translations over
previews, release pending commits on failures/empty results/cancellation, reset
Parakeet IPC after timeouts, and run Parakeet directly on in-memory PCM instead of
launching FFmpeg for every update. Regression tests cover the scheduling and
failure paths. Slow inference now logs queue and execution time separately without
logging transcript text. Logging and the final-timeout no-retry branch were added
after the replay and verified by tests.

Evidence: `evidence/stall-comparison-2026-09-25.json`, plus the corresponding
`stall-before` and `stall-after` event logs. Level messages were omitted. This
follow-up changes only the backend; the previous 13 passing Swift tests still
apply to the unchanged native capture code.

## Periodic chunk-boundary pause follow-up

The live runtime log showed ASR finals processing 12-second chunks in roughly
1.5–2 seconds including queue time. Old Gemma previews continued executing at
those boundaries. This matches the reported periodic pause, but does not prove
that every observed pause had that cause.

Changes preserve Silero state across a size-limit rollover, retire queued previews
as soon as audio commits, and cooperatively cancel a running translation preview
between generated tokens when its chunk commits. Cancelled previews return no text;
final translations are never cancelled by this mechanism. Tests exercise both
cancellation and complete, uncancelled streamed output.

A continuous replay concatenated six copies of the existing 5.232875-second clip,
with a 1-second silence threshold to force two 12-second size boundaries. This is
a diagnostic configuration, not the native app's 150 ms silence default. At those
two boundaries:

| Observation | Before fixes | With cancellable previews |
| --- | ---: | ---: |
| First next-chunk source caption after boundary 1 | 2.15 s | 0.73 s |
| First next-chunk source caption after boundary 2 | 2.81 s | 0.87 s |
| Gap from last old-chunk partial to first new-chunk partial, boundary 1 | 2.62 s | 1.68 s |
| Same visible partial gap, boundary 2 | 2.97 s | 1.48 s |

No inference errors appeared in the final replay. These are observations from
individual runs, not a guarantee or a statistically controlled benchmark. The
final replay ran after the user resumed the task, separately from the earlier
baseline. Word error rate and live-microphone accuracy were not established.

Queue retirement and detector continuity alone did not consistently remove the
pause. An experimental 6-second cap also left a long boundary gap, so the shipped
limit remains 12 seconds. The final implementation includes active preview
cancellation; the original source audio and final ASR path remain intact.

Evidence: `evidence/cap-comparison-2026-09-25.json` and the corresponding `cap-before`,
`cap-after`, `cap-short`, and `cap-cancel` event logs. Backend: 52 passing tests.

## Spanish preview display follow-up

The teleprompter previously substituted the English source whenever translation
was empty. It now displays a translating placeholder instead; the source remains
in its smaller reference line. A completed translation of an unchanged source
prefix may now be displayed alongside newer ASR words, and remains visible while
the next translation is pending. Changed source words invalidate that preview.
The backend separately tracks which source text was translated, so a shorter
preview cannot be promoted as the final translation of a longer sentence.

Validation: 57 backend tests and 13 Swift tests passed. A six-utterance native-socket
replay produced Spanish partial messages before finalization (see
`evidence/spanish-previews-2026-09-25.json`). The short cold-start replay produced
only a final translation; startup/inference delay still applies. Cancellation and
final-priority behavior remain enabled.

### Spanish token streaming regression fix

Translation previews previously buffered every generated token until the whole response completed. The worker now sends incremental translation text over its response queue, and the session publishes it against the latest compatible source transcript. Incomplete translations cannot be promoted to final text; finalizing utterances reject late progress. Repeated prefixes from a new decode do not erase longer text already shown. Silent audio warmup is capped at eight generated tokens.

A six-utterance recorded replay after a 15-second warmup produced Spanish before the first source-final event in all six utterances, including the first (16.296s versus 20.853s on the replay clock). This verifies early Spanish delivery through the backend socket, not microphone accuracy or native display timing. Timing evidence is in `evidence/spanish-token-stream-2026-09-25.json`. The prefix-preservation refinement was unit tested after that replay. The backend suite passed 59 tests, and the additional prefix-preservation test passed in the focused eight-test preview suite.
