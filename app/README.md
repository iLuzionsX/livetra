# LiveTR3

Native macOS live transcription and translation. SwiftUI renders the operator and
projector windows, AVAudioEngine captures the microphone, and a local Python/MLX
engine communicates over a Unix socket. No browser or web server is needed for the
Mac app. Inference stays on this Mac; model downloads need internet access.

## Build and run

Requires Apple Silicon, macOS 14+, Xcode command-line tools, Python 3.13+, and `uv`.
From the repository root:

```sh
cd app/backend
uv sync --extra test
cd ../..
./script/build_and_run.sh --verify
```

This creates and opens `dist/LiveTR3.app` using a release Swift build. The default
bundle contains the engine source and uses the repository's Python environment.
It is a development app, not yet a relocatable standalone distribution.
`script/package_engine.sh` supports a supplied standalone Python and wheelhouse
for packaging that runtime separately.

Download/cache this model before offline use:

- `mlx-community/gemma-4-e4b-it-8bit`

The native launcher sets offline model loading by default. Model files must
already be available in the Hugging Face cache. The engine log is
`dist/logs/backend-runtime.log`.

## Caption path

Gemma handles transcription and translation together in one normal inference
call for every source language, including code-switching sessions. The native
app and both backend transports use this same path without an engine override.

- Apple's streaming audio converter produces 16 kHz mono PCM with continuous
  resampling and anti-alias filtering. Pausing discards captured audio.
- Silero detects utterance boundaries. Native defaults request partials every
  250 ms, a 12-second utterance cap, and a 150 ms silence threshold. Scheduling
  cadence is not a promise that inference finishes within 250 ms.
- Each partial decodes the complete utterance so far and replaces the previous
  hypothesis. Final ASR runs again on the completed utterance.
- Gemma streams source text followed by translation from the same response.
  Generation uses greedy decoding. Incomplete finals get one bounded retry and
  cannot be published as complete captions.
- Preview pacing adapts to inference turnaround. Final jobs outrank previews,
  and committing audio cancels obsolete previews. Finalizing one utterance does not stop
  partials for the next. Failed or empty finals release their pending state.
- Automatic transcript correction and learning are removed. Existing vocabulary,
  archives, and legacy profile files are retained; stored corrections do not feed
  inference. Optional polish and speculative commits remain off by default.
- Segment lengths are capped at 29 seconds; oversized audio is rejected instead
  of silently trimmed. Gemma temporary audio directories are isolated by process.
- Slow jobs log queue and inference timing to the native engine log.

The native macOS app is the only UI. The browser client and alternative design
gallery have been removed.

## Validation

```sh
cd app/backend
uv run --extra test python -m pytest tests -q
uv run python -m scripts.check_imports
cd ../..
swift test --package-path macos/LiveTR3Mac
```

See `docs/GEMMA_ONLY_VALIDATION.md` for this change's evidence and limits.
`docs/NATIVE_VALIDATION.md` and its replay files describe the historical pipeline;
their timings do not characterize the current combined Gemma path.
