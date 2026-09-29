# Audience caption presentation

The projector observes `TranscriptStore` directly and feeds a separate, clock-driven
`ProjectorCaptionPresentation`. The operator transcript and exports retain the full
engine output. Presentation does not change inference or claim a confidence score.

- Final captions use fixed-size, high-contrast text. Focus, Split, and Stack remain
  selectable. Font size adapts to the window and selected size, never caption length.
- Two reading blocks show the previous and current pages together in the same large
  type. History is slightly softer white, not tiny or heavily dimmed. The live-draft
  strip is limited to one line to give completed text more screen space.
- Long utterances become measured pages, with clause/word boundaries when possible.
  All characters are retained; unspaced Japanese/Chinese and RTL text are supported.
  Source and translation paginate independently within the same utterance, so pages
  do not imply word-by-word bilingual alignment.
- Each page stays at least four seconds, or longer at approximately three words or
  fifteen characters per second, using whichever language needs more time. The
  previous page then remains for the current page's entire reading interval, giving
  each uncorrected page at least eight seconds on screen during normal advancement.
  Both blocks remain during silence. Corrections restart reading time and withdraw
  obsolete history. Explicit window/font changes can reflow the history block.
- Short queued utterances share the next page when both languages fit. No already
  visible page is extended by newly arriving finals. Sustained speech faster than
  the reading pace can still accumulate delay; completed text is never silently
  dropped to catch up.
- A draft prefix must remain unchanged for 700 ms. The trailing unfinished word is
  withheld. A changed suffix is withdrawn until it settles again, and final results
  override drafts. This suppresses flicker; it does not establish acoustic accuracy.
- A labeled live-draft area cannot displace finished captions. While completed pages
  are queued, it is hidden to avoid showing newer speech ahead of those pages.
- A newly joined viewer receives the newest finished caption and current draft,
  rather than replaying the entire service into the reading queue. The server keeps
  its full transcript history. Delayed partials cannot overwrite a final or polished
  caption in the native store.

Design references: [Amazon Transcribe's partial-result stabilization](https://docs.aws.amazon.com/transcribe/latest/dg/streaming-partial-results.html)
explains why hypotheses can revise as audio context grows; [BBC subtitle-rate research](https://downloads.bbc.co.uk/rd/pubs/whp/whp-pdf-files/WHP306.pdf)
discusses how delay, errors, and uneven delivery affect reading. Our local engine
provides no word-confidence or stable-token metadata, so the 700 ms rule is only a
presentation heuristic. No cloud transcription service is added.

## Verification

Run native tests and render the actual SwiftUI audience surface:

```sh
LIVETR3_PROJECTOR_RENDER_DIR="$PWD/dist/projector-review" \
  swift test --package-path macos/LiveTR3Mac
swift build -c release --package-path macos/LiveTR3Mac
(cd app/backend && uv run --extra test python -m pytest tests -q)
```

The deterministic tests cover draft growth/correction, delayed partials, reading
holds, retained history, silence, queued bursts, polishing, resizing, clear/reset, and character
preservation across long English, Spanish, Japanese, and Arabic captions. Render
checks use synthetic captions at 1280x720 and 1920x1080 in all three layouts. These
checks do not establish microphone recognition accuracy, end-to-end latency, or
readability on a physical projector.
