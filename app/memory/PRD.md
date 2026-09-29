# LiveTR3 PRD

> Historical planning document. LiveTR3 now uses a native SwiftUI interface and
> Unix-socket engine transport; the browser client and design gallery have been
> removed. See `../README.md` for the current architecture and commands.

## Goal

Build a local macOS web app for a mic'd speaker that shows live dual-pane captions:

- Top: original speech language
- Bottom: translation

The system is local-first and offline after initial dependency/model download.

## Fixed Target

- Apple M1 Pro
- 32 GB unified memory
- macOS 14+
- Python 3.13
- React + Vite + Tailwind frontend
- FastAPI backend
- WebSocket at `ws://localhost:8765`
- Gemma model: `mlx-community/gemma-4-e4b-it-8bit`
- Runtime: `mlx-vlm` on MLX/Metal

## Architecture

```text
Browser
  AudioWorklet
  16 kHz mono float32
  20 ms binary frames
  dual-pane caption UI
        |
        | ws://127.0.0.1:8765
        v
FastAPI session
  config/start/stop JSON
  ring buffer of last 30 seconds
  Silero VAD segmenter
  partial scheduler
  final scheduler
  last two committed utterances as context
        |
        v
Single MLX worker
  asyncio.PriorityQueue
  final jobs high priority
  partial jobs low priority
  no concurrent generate calls
```

## Model Constraints

- Audio chunks are force-flushed at 25 seconds to stay below Gemma's 30 second hard limit.
- Audio input is 16 kHz, mono, float32 in `[-1, 1]`.
- The audio slot is inserted before text through `apply_chat_template(..., num_audios=1)`.
- Sampling params are fixed: `temperature=1.0`, `top_p=0.95`, `top_k=64`.
- Thinking mode is not used in the live path.
- `mlx-vlm` is used directly; HuggingFace Transformers is not used.

## Prompts

Primary AST prompt:

```text
Transcribe the following speech segment in {src}, then translate it into {tgt}. When formatting the answer, first output the transcription in {src}, then one newline, then output the string '{tgt}: ', then the translation in {tgt}.
```

Polish prompt:

```text
You will receive a rough transcription. Remove filler words (um, uh, er, you know, like), fix punctuation, fix capitalization, and keep the exact meaning and wording. Return ONLY the cleaned text with no preamble.

Transcription: {text}
```

## Segmentation

Silero:

- `segmenter: "silero"`
- 16 kHz
- 512-sample VAD frames
- probability threshold `0.5`
- speech pad `300 ms`
- min silence `400 ms`
- 800 ms minimum utterance
- 25 second force flush

RMS segmentation is not used in this build. The `level` message keeps an `rms` field only as a waveform/mic-level readout.

## Frontend Requirements Covered

- Full-screen dark UI
- Two horizontal 50/50 caption panes
- Large readable text
- Partial captions grey, italic, lower opacity
- Final captions white and upright
- Polished captions pulse subtly
- Auto-scroll with scroll-up pinning
- Mic selector
- Source/target language inputs with MVP language suggestions
- Custom vocabulary textarea
- Silero VAD status
- Polish toggle
- Start/Stop button
- Waveform and mic level
- TXT/SRT/VTT export
- Space, Cmd+E, Cmd+, shortcuts
- `data-testid` on interactive and live text elements

## Build History

### 2026-04-20

Initial greenfield scaffold under `/app`.

Backend added:

- `server.py`
- `session.py`
- `segmenter.py`
- `mlx_worker.py`
- `protocol.py`
- `smoke_ast.py`
- `stream_wav_client.py`
- `pyproject.toml`
- `requirements.txt`
- `.env`

Frontend added:

- Vite React/Tailwind app
- AudioWorklet downsampler
- WebSocket audio/config client
- Dual-pane transcript UI
- Waveform meter
- export helpers
- transcript store with local agreement prefix tracking

Known current limitation:

- Long-duration memory stability and Wi-Fi-off operation still need a dedicated timed run.
- Browser mic verification depends on the selected input device. The local Safari UI captured mic levels, but macOS speaker playback did not reliably feed back into the selected mic for a clean browser transcript test.

### Verification Update

Completed on 2026-04-20:

- Installed `uv` and `yarn` with Homebrew.
- Ran `uv sync` with Python 3.13.
- Downloaded `mlx-community/gemma-4-e4b-it-8bit`; Hugging Face cache size was about 8.4 GB.
- Fixed `mlx-vlm 0.4.4` result handling by reading `GenerationResult.text`.
- Ran CLI smoke test on a 16 kHz mono float WAV generated with macOS `say`; output included both English transcript and Spanish translation.
- Started backend on `127.0.0.1:8765`; `/health` returned ok.
- Verified prerecorded WebSocket streaming produced `level`, `speech_start`, `partial`, `final`, and `polished` JSON messages.
- Verified custom vocabulary over WebSocket preserved `Amy`, `Morgan`, and `Surreal Coffee` in the final transcript.
- Built frontend with `yarn build`.
- Opened the app in Safari via Computer Use; Start/Stop worked and live mic levels rendered.
- Export was changed to separate TXT/SRT/VTT buttons because Safari allows one generated blob download per user gesture. Empty-session TXT/SRT/VTT files were downloaded successfully from Safari.

### Segmentation Decision Update

Updated after user direction to avoid RMS segmentation:

- Backend default segmenter is now Silero.
- Client config sends `segmenter: "silero"`.
- RMS is not used as a fallback when Silero fails.
- Frontend no longer exposes an RMS/Silero toggle; it shows Silero VAD as the active mode.
- The `rms` field remains only in level telemetry for the waveform/mic meter.
