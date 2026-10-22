#!/usr/bin/env python3
"""Normalize existing voice audio and measured caption timings without a service call."""
import argparse
import json
from pathlib import Path
from synthesize import (audio_duration, atomic_write, captions_from_words, complete_word_timings,
                        digest, json_bytes, restore_punctuation, segments_from_script, srt_text)


def convert(script_path, input_path, output, ffmpeg, allowed_root=None):
    source = script_path.read_bytes()
    script = json.loads(source)
    supplied = json.loads(input_path.read_text(encoding='utf-8-sig'))
    if supplied['voice'] != script['voice'] or supplied['rate'] != script.get('rate', '+0%'):
        raise ValueError('Imported voice/rate must match the script')
    method = supplied['alignmentMethod']
    if method not in ('provider-timestamps', 'forced-alignment', 'human-reviewed', 'fixture-test'):
        raise ValueError('Supply measured or reviewed timing provenance')
    records = {s['id']: s for s in supplied['segments']}
    expected = segments_from_script(script)
    if len(records) != len(supplied['segments']) or set(records) != {s['id'] for s in expected}:
        raise ValueError('Imported IDs must match all script segments exactly')
    root = (allowed_root or input_path.parent).resolve()
    output.mkdir(parents=True, exist_ok=True)
    takes = []
    for segment in expected:
        item = records[segment['id']]
        audio = (input_path.parent/item['audio']).resolve()
        if not audio.is_relative_to(root) or not audio.is_file():
            raise ValueError('Imported audio is outside the allowed audio root')
        if audio.suffix.lower() not in ('.mp3', '.wav', '.m4a', '.ogg', '.flac'):
            raise ValueError('Use a supported local audio extension')
        expected_hash = item.get('audio_sha256', item.get('audioSha256'))
        if expected_hash is not None and digest(audio.read_bytes()) != expected_hash:
            raise ValueError('Audio changed after generation/alignment; regenerate or realign the take')
        duration = audio_duration(str(ffmpeg), audio)
        if 'wordBoundaries' in item:
            words = item['wordBoundaries']
            restore_punctuation(words, segment['text'])
            complete_word_timings(words, duration)
            captions = captions_from_words(words, duration)
        else:
            words = []
            captions = item['captions']
            if method == 'provider-timestamps':
                raise ValueError('Provider word metadata required for provider-timestamps mode')
        previous = 0
        for cue in captions:
            if cue['start'] < previous or cue['end'] <= cue['start'] or cue['end'] > duration:
                raise ValueError('Imported captions overlap or exceed decoded speech duration')
            if not isinstance(cue['text'], str) or not cue['text'].strip():
                raise ValueError('Imported caption text must be nonempty')
            previous = cue['end']
        if not captions:
            raise ValueError('No captions supplied')
        normalized = lambda text: ''.join(c.casefold() for c in text if c.isalnum())
        if normalized(' '.join(c['text'] for c in captions)) != normalized(segment['text']):
            raise ValueError('Caption words do not match the current script narration')
        sid = segment['id']
        audio_name = sid + audio.suffix.lower()
        subtitle_name = sid + '.srt'
        audio_bytes = audio.read_bytes()
        subtitle_bytes = srt_text(captions).encode('utf-8')
        metadata = {'id': sid, 'chapterId': segment['chapterId'], 'voice': supplied['voice'],
                    'rate': supplied['rate'], 'engine': supplied['provider'], 'alignmentMethod': method,
                    'duration': duration, 'durationSeconds': duration, 'audio': audio_name,
                    'subtitles': subtitle_name, 'wordBoundaries': words, 'captions': captions,
                    'textSha256': digest(segment['text'].encode('utf-8')),
                    'audioSha256': digest(audio_bytes), 'subtitlesSha256': digest(subtitle_bytes)}
        metadata['requestSha256'] = digest(json_bytes(metadata))
        atomic_write(output/audio_name, audio_bytes)
        atomic_write(output/subtitle_name, subtitle_bytes)
        takes.append(metadata)
    manifest = {'formatVersion': 3, 'voice': supplied['voice'], 'rate': supplied['rate'],
                'engine': supplied['provider'], 'alignmentMethod': method,
                'sampleRate': 24000, 'sourceSha256': digest(source),
                'totalNarrationDuration': sum(s['durationSeconds'] for s in takes), 'segments': takes}
    atomic_write(output/'manifest.json', json_bytes(manifest))
    print('NARRATION_IMPORTED', len(takes), manifest['totalNarrationDuration'])


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--script', required=True, type=Path)
    p.add_argument('--input', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--ffmpeg', required=True, type=Path)
    p.add_argument('--allow-audio-root', type=Path)
    args = p.parse_args()
    convert(args.script.resolve(), args.input.resolve(), args.output.resolve(), args.ffmpeg.resolve(), args.allow_audio_root)
