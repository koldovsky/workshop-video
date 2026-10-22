# Custom voice profiles

This folder ships with no voice data on purpose: no cloned voice, reference audio,
or personal data is distributed with this skill.

Voice cloning with FireRedTTS3 is optional. To use your own voice, follow
[the voice profile setup guide](../../references/firered-voice.md), which walks
through recording consent, extracting clean reference clips, and filling in
`profiles.json`.

To start, copy `profiles.example.json` to `profiles.json` in this folder, add your
own `reference-a.wav`/`reference-a.txt` (and optionally a second pair for an
alternative profile), then replace every `REPLACE_WITH_*` placeholder, including
the reference/transcript SHA-256 hashes.

Never commit or redistribute real reference audio, transcripts, or a filled-in
`profiles.json` from this folder — they are personal voice biometric data.
