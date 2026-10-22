# Production procedure

## Dependencies and preparation

- Python 3.10+ for the standard-library build/import/verify/publish scripts.
- Node.js and Playwright with a Chromium browser for readable screen captures. The default module is `playwright`; set `PLAYWRIGHT_MODULE` to an existing module path if needed. Do not assume the target project already has it, and do not change global packages.
- An FFmpeg executable with H.264, AAC, libass/subtitles, drawtext, and the standard audio filters. The extracted pipeline was exercised with FFmpeg 7.1. Supply the executable explicitly.
- A local TTF/OTF font covering the chosen language, and its actual family name. For example, an available Noto Sans/DejaVu Sans font on Linux or an appropriate installed font on Windows. No platform-specific path is built into the renderer.
- Only if Edge is selected: `edge-tts==7.2.8`. `imageio-ffmpeg==0.6.0` is an optional way to locate an FFmpeg executable; otherwise pass `--ffmpeg`. The scripts never install dependencies automatically.

Keep intermediate audio, logs, manifests containing local paths, and copied render fonts in a private work directory outside the publication allowlist. Package only final verified outputs. Use ordinary local paths and structured command arguments; no shell evaluation of narration or recorded command text occurs.

## Select the voice path

**Optional custom cloned voice:** [set up a FireRed profile](firered-voice.md) if you want a presenter's own voice. The helper generates local per-scene FLOAT WAVs and provenance; align those recordings before importing them. For these profiles set `maxSpeechSpeed: 1.0` and `audioProcessing: "preserve-voice"` so video assembly preserves the selected pace and dynamics. Edge remains an optional provider for an explicit different voice choice.

**Available provider or human recording:** use the available service only within the user's authorized scope, obtain local per-scene audio, and align it. The installed AI Voice Generator can create audio and preview a short transcript, but its exposed contract does not guarantee word timestamps or a local downloadable artifact. Do not assume those exist. If audio download/alignment is unavailable, choose a supported provider or state the specific limitation rather than presenting an unrendered lesson as complete.

**Optional Edge adapter:** sends narration text to Microsoft's Edge speech service; it does not upload the repository or screen files. Use it for public or authorized narration. Voice availability is provider-dependent, so select a voice for the requested language and test a short sample. Service failure has bounded retries; changing narration/voice/rate invalidates only affected take caches. No speech-service call is needed for this skill's smoke test.

## Commands

In these examples `SKILL/scripts` means the absolute scripts directory of this installed skill. Replace paths with actual project paths; keep commands in the active shell's native syntax. `WORK` is a private output directory, `PUBLIC` is the intended local publication directory, and `FFMPEG` is the executable path. The script JSON contains the explicit voice/rate/language/duration.

Generate narration with the optional adapter:

```text
python SKILL/scripts/synthesize.py PROJECT/script.json --output WORK/narration --ffmpeg FFMPEG
```

Or import already recorded/aligned audio:

```text
python SKILL/scripts/import_narration.py --script PROJECT/script.json --input PROJECT/takes.json --output WORK/narration --ffmpeg FFMPEG
```

Build a frame-exact plan. Add source/recording arguments only when those screens exist:

```text
python SKILL/scripts/build_video.py plan --script PROJECT/script.json --narration WORK/narration --work WORK --font-family "FONT FAMILY" --source-root PROJECT --allow-source examples --recordings PROJECT/public-recordings.json
node SKILL/scripts/render_screens.cjs WORK
python SKILL/scripts/build_video.py render --work WORK --ffmpeg FFMPEG --font FONT_FILE --workers 3
python SKILL/scripts/verify_video.py --work WORK --ffmpeg FFMPEG
python SKILL/scripts/publish_video.py --work WORK --destination PUBLIC --name video-lesson
```

All `--allow-source` entries are relative to `--source-root`; repeat the option for several known public files/directories. `--font-family` is selected at planning time and must match the actual font file used for rendering.

### What the stages guarantee

1. **Plan** requires the exact current script hash and matching per-take text/audio/subtitle hashes. It rejects oversized narration rather than silently truncating speech. Each chapter gets an exact frame budget; extra reading time is distributed with more weight for practice scenes. Inspect the printed speech-seconds/chapter-seconds ratios for unexplained slack and revise before rendering.
2. **Capture** renders the actual screen HTML, checks browser errors and overflow, and binds each PNG to the plan and HTML hashes. Examine dense screens and representative text/code/recording/exercise screens visually. The default screen template reserves a separate caption area.
3. **Render** checks capture and narration/caption hashes; its cache includes render code, FFmpeg version, font, audio, image, and subtitle bytes. It produces one video-only MP4 and exact 24 kHz mono PCM WAV per scene. It concatenates those and performs one final AAC encoding, preserving the overall timeline. Chapter metadata and fast-start playback are embedded. Long planned pauses display a label and countdown.
4. **Verify** requires the completed render receipt, which binds the original checked timeline, captures, narration, captions, scene parts, and final media hashes. Replanning cannot certify an older video; every render attempt invalidates the previous receipt before work starts. Verification fully decodes both final streams, checks the requested duration, exact video frames, exact per-scene/total PCM samples, subtitle count/order, and unchanged planned text assets. It writes a final report tied to the render receipt, timeline, and output hashes.
5. **Publish locally** refuses missing/stale verification or modified outputs. It creates `video-lesson.html` and `video-lesson-media/` containing MP4, SRT, VTT, transcript, poster, and a download hash manifest. It never pushes or deploys.

Existing output is preserved by default. For an authorized regeneration, add `--replace`; it requires this skill's ownership markers on the existing page/media directory. It rejects media/file symlinks and does not remove unrelated files. Do not create ownership markers by hand to overwrite another project's content; choose a new output name or deliberately integrate that project instead. This is a preservation mechanism, not an additional approval step.

The publication gate is an integrity check, not a substitute for checking narration quality, factual accuracy, navigation links, or whether a purported demo really happened. Review those separately. Check a streamed beginning/middle/end, chapter seeking, downloads, narrow screen layout, and the final visible subtitle frames. For a public deployment, verify that the served files match the local release hashes and that large-file/range behavior works on the chosen host.

## Offline validation of this skill

Run the regression suite:

```text
python -m unittest discover -s SKILL/scripts -p test_video.py -v
```

Run the complete pipeline in an empty scratch directory:

```text
python SKILL/scripts/smoke_test.py --work EMPTY_SCRATCH --ffmpeg FFMPEG --font FONT_FILE --font-family "FONT FAMILY"
```

The smoke test generates a labelled local tone, two CSV teaching screens, aligned fixture captions, and a **12-second test video**. It performs real browser capture, H.264/AAC render, full stream decode, 120-frame/288,000-PCM-sample checks, and local player generation. It does not call a speech service or claim that the test tone is narration. Output stays in the requested scratch directory. `--node` can select a specific Node executable; `PLAYWRIGHT_MODULE` can point at an existing Playwright installation.

The packaged tests cover randomized timing budgets, excessive narration rejection, whole-frame constraints, zero-duration service-word repair, nonoverlapping captions, subtitle minute rollover, source allowlists, stale narration, and publication blocked by changed bytes or failed full decoding. They are supplemented by the smoke render, not replaced by assertions about generated wording.
