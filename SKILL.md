---
name: workshop-video
description: Create a self-contained narrated workshop video with paced screen explanations, aligned subtitles, chapter navigation, and downloadable MP4, captions, and transcript. Use for a video alternative to a taught lesson or a recorded walkthrough of workshop materials.
---

# Workshop video

Produce the actual playable lesson and its source script. A screenplay, HTML player, or unrendered timeline alone is not a completed video request.

## Make the lesson self-contained

Establish the requested language, length, audience, practical prerequisites, and whether the duration includes breaks or exercises. Use existing session context; do not invent a fixed two-hour format. At the start, show where to obtain the repository or participant bundle, the exact working directory, how to prepare it, and what a successful baseline looks like.

Build a chapter budget before writing narration. Explain the goal, action on screen, observed result, and meaning of the result. Use real sanitized demo evidence when describing actual execution. Clearly label illustrative code and planned outcomes. The bundled renderer produces a prepared screen lesson from readable excerpts and recorded frames; it is not evidence that a live instructor performed those actions.

Read [the script and narration schema](references/schema.md) when authoring inputs. Start with [the small CSV example](assets/example-script.json), replacing its content, voice, duration, language, and navigation. Preserve the selected design system when adapting [the screen template](assets/screen.template.html).

## Voice and pacing

This skill can optionally narrate a lesson in a presenter's own cloned voice with **FireRedTTS3**, configured per person as a local voice profile; no profile ships with the skill, and cloning requires the presenter's own consented reference recording. Use whichever configured profile the request selects, without silently mixing voices within a lesson. Read [setting up a custom FireRed voice profile](references/firered-voice.md) for how to prepare references, fill in settings, run generation locally, and switch profiles. This is an opt-in capability, not a requirement to clone anyone's identity.

Use a suitable available voice provider, or an authorized human recording. Disclose synthetic narration and whether it uses a cloned voice; do not present it as a live teaching recording. A connected AI Voice Generator may supply audio; its output is not assumed to include word timestamps or a downloadable local file. The optional bundled Edge adapter supplies measured word boundaries. Do not install or require a plugin merely because this skill exists.

Generate and assess a short representative speech sample before a long render: ordinary prose, code identifiers, numbers, and unfamiliar terminology. Estimate the script length from this voice's measured pace. Revise overfull chapters instead of squeezing large amounts of speech into them. Reading time, practice, and breaks must have a stated teaching purpose; do not fill an arbitrary requested duration with unexplained silence. Keep code legible and split dense screens into smaller excerpts.

Use provider word timing, forced alignment, or explicitly reviewed human timing. Never invent synchronization by evenly dividing the narration text over an audio file. Keep exact commands on screen while writing speakable words in narration. If this environment cannot listen, report that limit and verify timing/decoding without claiming a listening review.

## Build and verify

Follow [the production procedure](references/production.md) for the selected audio provider, portable dependencies, command sequence, and checks. Scripts are invoked from this skill; inputs and outputs stay in the user's project.

The pipeline preserves four important boundaries:

- A script edit invalidates stale narration. Voice, text, audio, and caption hashes must match before planning.
- Scene screenshots must pass overflow checks. Changed screenshots, narration, or burned captions invalidate the render inputs.
- Whole video frames and exact PCM scene lengths are concatenated before one final AAC encode, avoiding cumulative audio padding drift.
- Full decoding, exact duration/frame/sample checks, and hashes of the final media gate creation of the player page.

Inspect representative burned-caption frames and all dense screens. Check final playback, chapter seeking, downloads, subtitle readability, and mobile navigation. Ask an independent reviewer to follow a realistic portion of a substantial lesson, then fix any demonstrated issues. The local publisher does not deploy anything: reuse existing authorization for deployment, otherwise deliver the verified local artifacts.

## Deliver

Provide the MP4, SRT, VTT, transcript, chapter player, and editable script. Link the player to the workshop start page, slides, demo, and cheatsheet when those exist. Keep large video assets separate from small practice bundles when useful, with a clear direct download. State the measured duration, voice type, verification performed, and any remaining limitation.

For a complete material set, `$workshop-materials` coordinates this skill with `$workshop-slidev`, `$workshop-demo`, and `$workshop-cheatsheet`. This skill also works independently.
