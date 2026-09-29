# LiveTR3 Validation

LiveTR3 validation is split into three tiers so fast deterministic regressions block pull requests without pretending that CI can reproduce Apple Silicon ML inference or a physical microphone.

## Tier 1 — required pull-request gate

Run from the repository root on a development Mac:

```bash
bash script/validate.sh
```

This runs:

1. Python protocol, transport, cross-client contract, and validation-harness tests.
2. Native Swift unit tests for server-message parsing and transcript-state invariants.
3. A release build of the native Swift package.

GitHub Actions runs the same deterministic backend coverage on Ubuntu and the native Swift tests/build on a macOS runner.

Required result: zero failures.

## Tier 2 — Apple Silicon ML and prerecorded audio

These tests must run on the target Apple Silicon Mac with the model and backend dependencies installed.

### Five-minute soak

```bash
cd app/backend
uv run --extra test python scripts/soak.py \
  --duration-seconds 300 \
  --metric-interval-seconds 30 \
  --drain-seconds 15
```

Or run Tier 1 plus the soak:

```bash
LIVETR3_RUN_SOAK=1 bash script/validate.sh
```

### Worker-fault recovery

```bash
cd app/backend
uv run --extra test python scripts/kill_test.py
```

### Prerecorded corpus

Create a JSON manifest next to a curated set of 16 kHz WAV fixtures:

```json
{
  "cases": [
    {
      "name": "english-spanish-basic",
      "wav": "fixtures/english-spanish-basic.wav",
      "source": "English",
      "target": "Spanish",
      "expected_original": "The expected source transcript.",
      "expected_translation": "La traducción esperada."
    }
  ]
}
```

With the backend running on port 8765:

```bash
cd app/backend
uv run python scripts/recorded_validation.py validation/manifest.json \
  --output validation/latest-results.json
```

The prerecorded validator always fails on backend errors, missing finals, duplicate final utterance IDs, and non-monotonic final IDs. It reports first-partial latency, end-of-audio-to-final latency, source WER, and target WER.

Do the first target-Mac run without invented performance thresholds. Save that baseline, inspect failures, and only then promote measured limits into explicit gates with:

```bash
uv run python scripts/recorded_validation.py validation/manifest.json \
  --max-final-after-audio-ms <MEASURED_GATE> \
  --max-source-wer <MEASURED_GATE> \
  --max-target-wer <MEASURED_GATE> \
  --output validation/latest-results.json
```

The corpus should eventually include short commands, long sentences, rapid consecutive speech, long pauses, code switching, proper nouns/custom vocabulary, background noise, and both English→Spanish and Spanish→English cases.

## Tier 3 — physical native-app acceptance

Run this on the actual event Mac with the intended microphone and projector/display path.

- Fresh launch and microphone permission.
- Start/stop a session repeatedly.
- English→Spanish and Spanish→English.
- Pause capture, speak a unique phrase while paused, resume, and verify that the paused phrase is never emitted or replayed; also verify commit-now separately.
- Change the input microphone during a session.
- Open, close, and reopen the native projector window.
- Verify projector captions match finalized operator captions and ordering.
- Export TXT, SRT, and VTT and inspect ordering/timestamps.
- Unplug/replug the microphone and verify recovery is controlled rather than a crash.
- Restart the backend and verify reconnect/session behavior.
- Sleep/wake the Mac and verify the app either resumes cleanly or exposes a clear recoverable state.
- Run continuously for 30–60 minutes while watching memory, CPU, thermals, duplicate captions, dropped utterances, and latency drift.

Tier 3 cannot be truthfully certified by a Linux container or generic hosted CI because it depends on Core Audio devices, AVAudioEngine, macOS windowing, Metal/MLX performance, and the actual room/audio chain.

## Change policy

A failing validation must be diagnosed before production parameters are changed. Do not loosen a threshold or tune VAD/inference merely to convert a failure into a pass. Preserve the failing evidence, identify the cause, make the smallest justified correction, and rerun the full relevant tier.
