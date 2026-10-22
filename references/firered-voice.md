# Custom FireRedTTS3 voice profiles

This skill can optionally clone a presenter's own voice with FireRedTTS3 instead of
using a generic TTS provider. It is opt-in: no voice profile ships with the skill,
and nothing is cloned unless you deliberately set it up with your own consented
recording. See [the empty profiles folder](../assets/voices/README.md) to get started.

Only clone a voice you have clear rights and consent to use. Keep the reference
audio, transcript, and filled-in `profiles.json` private: exclude them from
participant bundles and any public distribution of this skill or its outputs.
Local synthesis does not upload audio anywhere.

## Choose and prepare a reference clip

Pick 5-15 seconds of clean, single-speaker audio from a recording you're entitled
to use: no background noise, music, or overlapping speech. Write out its exact
transcript. A second, distinct clip can be prepared as an alternative profile (for
example a calmer take for dense explanation versus a livelier take for intros).

Resample to 24 kHz mono, average stereo channels down if needed, and normalize to
about -24 dBFS RMS. No EQ, denoising, compression, or time stretching — the model
expects a natural, unprocessed reference.

## Fill in `profiles.json`

Copy `assets/voices/profiles.example.json` to `assets/voices/profiles.json`, add
your `reference-a.wav`/`reference-a.txt` (and `reference-b.*` for an alternative),
then replace the placeholders: your name/date, the language you actually
auditioned, the source description, the tested FireRedTTS3 code/weights revisions,
and the SHA-256 hashes of the reference audio and transcript files. Pick your own
`voice` IDs (for example `firered-<name>-a`); the importer rejects a script whose
`voice` does not match the selected profile.

Test new languages, unusual terminology, or changed model/runtime settings with a
short sample before trusting a profile for a full lesson.

## Generate

Use FireRedTTS3 **Base**, disable automatic language detection, text
normalization, external LLM normalization, cleanup, and splitting. Write speakable
text yourself; split a lesson into short scene paragraphs before synthesis. Use the
explicit language `Ukrainian` (`uk`) or `English` (`en`) — whichever you actually
auditioned; do not label an unauditioned language as already approved quality.

In the lesson JSON set:

```json
{"language":"uk", "voice":"firered-<name>-a", "rate":"+0%",
 "maxSpeechSpeed":1.0, "audioProcessing":"preserve-voice"}
```

Run with an isolated FireRed Python environment. `SKILL` is this skill directory;
`RUNTIME` contains `repo`, `models`, and the installed environment. Paths are
ordinary local paths, not shell-expanded by the helper.

```text
RUNTIME/venv/Scripts/python.exe SKILL/scripts/firered_narration.py PROJECT/script.json --runtime RUNTIME --output WORK/voice-a --ffmpeg FFMPEG --gpu 0
```

**To use an alternative profile:** set the script's `voice` to that profile's ID,
pass `--profile b` (or whatever key you used), and use a separate output
directory such as `WORK/voice-b`. Omitting `--profile` selects `default_profile`
from `profiles.json`; a conflicting script voice is rejected. Reimport/re-align
and rebuild the lesson after switching profiles — captions from one profile
cannot be reused for another. Existing matching takes resume by hash; changed
inputs need a new output directory.

Add `--limit 1` for a first-scene pilot. Add `--inspect` to verify references and
resolve parameters without importing the model or creating audio. `--gpu` accepts a
CUDA index or UUID. The helper supports Ukrainian and English language tags and
records which was actually used.

It creates raw FLOAT WAVs (which may have peaks above unity), then measures the
assembled narration and applies **one constant gain across all scenes** for safe
delivery WAVs. There is no EQ, denoising, compression, limiting, pitch change, or
time stretch. The target is no louder than -23 LUFS and leaves at least 2 dB
measured true-peak headroom for every scene. Raw WAVs are generation
intermediates; use the gain-adjusted `audio` paths in `takes.unaligned.json` for
alignment/import. Preserve `generation.json` and per-take receipts privately.

FireRed does not supply subtitle timings. Align the resulting audio against its
exact scene text with a suitable forced aligner or reviewed timing. Create
`takes.json` from `takes.unaligned.json`, replace `alignmentMethod: "required"`
with the actual method, and add real `wordBoundaries` or `captions` for every
scene. Preserve each `audio_sha256`: the importer rejects audio replaced after
alignment. Do not use ASR text alone as proof of exact alignment or fabricate
evenly spaced times. Then run `import_narration.py` as described in
[production.md](production.md). Keep the script voice/rate and segment IDs
unchanged. The voice-preserving render uses 128 kbit/s AAC for final video audio.

## Runtime

- Pick a runtime directory containing `repo`, `models`, and the installed
  environment; it does not need to live inside this skill or any particular
  project. Pass it with `--runtime` on each machine you run the helper from.
- Official code: <https://github.com/FireRedTeam/FireRedTTS3>. Pin a specific
  revision you've tested and record it in `profiles.json`.
- Official weights: <https://huggingface.co/FireRedTeam/FireRedTTS3>; Base, RedAE,
  CAM++, and tokenizer assets are required. Pin a specific revision you've tested
  and record it in `profiles.json`. Preserve upstream Apache-2.0 notices.
- Tested with Windows Python 3.12, PyTorch/Torchaudio 2.8.0+cu128, Transformers
  5.6.2, SoundFile/NumPy. No global package changes or automatic downloads occur.
- On Windows, apply [the SDPA compatibility patch](firered-windows-sdpa.patch) to
  your chosen source revision. It changes attention implementation only; do not
  substitute Instruct, quantize weights, or change precision while claiming a
  selected profile is unchanged. Use FP32 weights with the model's original
  autocast behavior.
- A 24 GB NVIDIA GPU is the comfortable tested option; 16 GB worked with little
  headroom. Run one model worker per selected GPU. Avoid GPU contention.

The helper records runtime source/model file hashes for cache invalidation, but
cannot guarantee identical waveforms across hardware or package versions. Review a
pilot after changing the runtime. For final video assembly preserve the voice with
`maxSpeechSpeed: 1.0` and `audioProcessing: "preserve-voice"`; shorten narration or
expand the teaching budget if speech does not fit.
