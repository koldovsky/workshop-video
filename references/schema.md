# Lesson inputs

## Script

All files are UTF-8. `assets/example-script.json` is a complete small example.

| Field | Meaning |
| --- | --- |
| `title`, `language` | Lesson title and language tag, such as `en` or `uk`. |
| `durationSeconds` | Explicit requested total, including any declared breaks/practice. |
| `voice`, `rate` | Chosen voice identifier and rate (default `+0%`); narration metadata must match. |
| `disclosure` | Accurate description of the voice and prepared/live nature of the material; also shown on the player and transcript. Keep it short enough for a screen footer. |
| `brand`, `description` | Optional brand text and player introduction. |
| `links` | Optional array of `{ "label": "Slides", "href": "slides.html" }`. Relative and HTTP(S) URLs only. |
| `chapters` | Ordered nonempty array. Each has `id`, `title`, `targetSeconds`, and `segments`. Chapter budgets total the requested duration. |
| `fps` | Optional positive integer divisor of 24000; default 10 is suitable for prepared text/code screens. Chapter budgets must be whole frames. |
| `maxSpeechSpeed` | Maximum chapter acceleration, default 1.12. The script accepts 1–1.25; use a higher limit only after checking the actual voice. Prefer revising prose. |
| `audioProcessing` | Optional `preserve-voice` for approved voice profiles: requires `maxSpeechSpeed: 1.0`, omits time stretching and the limiter, and checks input true-peak headroom before rendering. Omit for the existing generic processing path. |
| `labels` | Optional localized interface text, listed below. |

Each segment has a plain identifier `id`, `title`, nonempty `narration`, and a `screen` object. Use letters, digits, underscores, and hyphens for IDs. Chapter IDs and flattened audio IDs (`chapter-id--segment-id`) must be unique ignoring case so their generated files remain distinct on Windows and macOS.

The renderer is 1600 × 900, with a reserved subtitle strip and readable minimum text sizes. Adapt the supplied HTML/CSS template to the user's design rather than putting branding assumptions into the script schema. Long content that still does not fit fails capture; split the screen instead of hiding the overflow.

### Screen variants

Common fields: `kind`, optional `heading` (defaults to segment title), and optional `bullets`.

- `text`: explanatory bullets.
- `exercise` or `break`: a clearly stated task/pause and resumption instructions. Put a pause in its own chapter when its duration must be controlled. Every scene has some narration, including the instructions before a pause.
- `code` with `code`: literal teaching example; it is labelled as an example.
- `code` with `path`: read an actual UTF-8 file under `--source-root` and an explicit `--allow-source` entry. `lineStart` and `lineEnd` are inclusive, one-based; `focusLines` refers to the original line numbers. No source path is allowed by default. Excerpts beyond the file fail.
- `recording`: `runId`, zero-based `frameIndex`, optional inclusive `excerptStartLine`/`excerptEndLine`. `contentField` may extract a string field from a recorded frame containing a JSON object. Preserve the recorded content; do not fabricate or silently rewrite output to fit an explanation.

### Recording interchange

Pass an already sanitized JSON export with `--recordings`. It is compatible with the demo skill's exported runs:

```json
{"runs":[{"id":"example-run","title":"Illustrative example","evidence":"illustrative","frames":[
  {"kind":"output","tool":"terminal","text":"example output"}
]}]}
```

This illustrative schema is not evidence of an execution. Capture real output for an actual lesson, redact private data before export, and retain enough context to interpret results. Set `evidence: "recorded"` only for real captured execution. The video renderer preserves this classification: missing or `illustrative` evidence gets an illustrative walkthrough label, never an actual recording label. Do not upgrade the demo skill's sample fixtures to recorded evidence. Only selected frame text is rendered; raw recordings are not copied into final downloads.

### Localization labels

Optional `labels` keys default to English. Translate the keys used by the selected screens/player:

`text`, `code`, `codeSource`, `recording`, `illustrativeRecording`, `exercise`, `break`, `practicePause`, `materials`, `videoFallback`, `downloadVideo`, `transcript`, `chapters`.

The countdown displays seconds numerically. Choose a local font whose glyph coverage includes the lesson language; `--font-family` must be the supplied font's actual family name. No font is redistributed in the skill.

## Narration input from an existing provider

For an optional custom cloned voice, use the `voice` ID from your own configured FireRed profile (see [profiles.json](../assets/voices/profiles.example.json)), with `rate: "+0%"`, `maxSpeechSpeed: 1.0`, and `audioProcessing: "preserve-voice"`. See [generation and alignment](firered-voice.md). The FireRed helper's `takes.unaligned.json` deliberately cannot be imported until real caption timings have been added.

For the optional Edge adapter, provide script/voice/rate and use `synthesize.py`; it writes the normalized manifest itself. For AI Voice Generator, a human recording, or another provider, obtain local audio first and use `import_narration.py` with an input file like:

```json
{
  "provider": "selected-provider",
  "voice": "chosen-voice",
  "rate": "+0%",
  "alignmentMethod": "provider-timestamps",
  "segments": [{
    "id": "intro--hello",
    "audio": "hello.wav",
    "wordBoundaries": [{"start": 0.1, "end": 0.6, "text": "Hello."}]
  }]
}
```

Audio paths resolve relative to this input file and must stay inside its directory, or the explicit `--allow-audio-root`. Supported input extensions: WAV, MP3, M4A, OGG, FLAC. All durations are measured by decoding audio, rather than inferred from compressed file size.

For `forced-alignment` or `human-reviewed`, `captions` may replace word boundaries, with `{ "start": 0.1, "end": 1.2, "text": "Hello." }` in seconds. These must be real alignment results or timing that was reviewed against the speech. The adapter validates word coverage, monotonic timing, and duration, but cannot prove that a claimed human review occurred. `fixture-test` is reserved for labelled test tones and must never be represented as spoken teaching material.

The normalized manifest records the exact script-byte SHA-256, text/voice/rate provenance per segment, measured duration, caption timing, and audio/SRT hashes. Final SRT, VTT, and transcript are written with LF newlines for stable cross-platform bytes. Do not edit the manifest to bypass a stale-audio failure: regenerate/reimport the changed takes.

### Word boundary edge case

Some services give a real offset but zero duration to a short word. The adapter extends that word only to the next measured word's later start, recording `serviceDuration: 0` and `endSource: next-word-start`. It rejects an ambiguous last word, missing next offset, backward offset, or negative duration. It never guesses a missing word interval. Caption grouping also clamps small service overlaps to the next cue's start.
