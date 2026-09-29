# Combined Gemma pipeline — 2026-09-26

The native app and both backend transports now use Gemma for transcription and
translation in one normal inference call. This preserves the streaming version
the user tested before the separate accuracy/latency follow-up.

The cleanup removes the alternate ASR worker, its text translation/correction
jobs, automatic correction learning, engine selection, and 19 packages from the
lockfile (the recognizer plus 18 exclusive dependencies). Versions of all retained
packages are unchanged. Existing profile files are read for archive compatibility
but never rewritten, and legacy correction/bilingual metadata remains inert.
Vocabulary and session archives are retained.

## Validation

- A fresh environment installed successfully with `uv sync --locked --offline
  --extra test --extra dev`; the removed recognizer is not installed.
- Backend: 68 tests passed in that environment, including streaming completion,
  bounded retry, cancellation, final priority, scheduling, audio rollover, archive
  preservation, and the smoke CLI's structured result handling.
- Real `mlx_vlm` and `stream_generate` imports passed without the removed packages.
- Backend import gate, Python compilation, focused Ruff checks, and
  `git diff --check` passed.
- Native Swift: 14 tests passed; release build passed with existing
  `AudioCaptureEngine.swift` warnings.
- A syntax-tree comparison against the tested pre-cleanup implementation confirmed
  unchanged Gemma inference/warmup, parser, truncation detection, job submission,
  token budgets, adaptive pacing, and audio segmentation. The combined session
  path differs only by removing unused bilingual-learning bookkeeping.

Tests for the removed split ASR/translation path were retired or ported to Gemma;
the lower suite count does not represent an acoustic-quality measurement.

The already-running test app was not restarted or changed during cleanup. No
microphone capture or competing model replay was run. These checks establish code
and build compatibility, not a fresh end-to-end latency or word-error-rate result.
The separate follow-up owns real-model replay and further streaming refinements.

`NATIVE_VALIDATION.md`, `evidence/`, and older soak files remain historical records
of their original pipeline. Their timings must not be attributed to this version.
