#!/usr/bin/env python3
"""Decode the complete lesson and bind publication to measured streams and byte hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import wave
from render_integrity import check_render

ASSETS = ('lesson.mp4', 'lesson.srt', 'lesson.vtt', 'transcript.md', 'poster.png')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(work, ffmpeg):
    plan_path = work/'timeline.json'
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    check_render(work, plan)
    expected = plan['duration']
    frames_expected = round(expected * plan['fps'])
    for name in ('lesson.srt', 'lesson.vtt', 'transcript.md'):
        if digest(work/name) != plan['textAssets'].get(name):
            raise ValueError('Text artifact changed after planning: '+name)
    if abs(sum(c['duration'] for c in plan['chapters']) - expected) > .001:
        raise ValueError('Chapter budgets differ from final duration')
    probe = subprocess.run([str(ffmpeg), '-hide_banner', '-i', str(work/'lesson.mp4')],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
    (work/'probe.log').write_text(probe.stderr, encoding='utf-8')
    match = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', probe.stderr)
    if not match:
        raise ValueError('Missing media duration')
    h, m, s = map(float, match.groups())
    duration = h*3600 + m*60 + s
    if abs(duration - expected) > .12:
        raise ValueError(f'Final duration {duration} differs from {expected}')
    if not re.search(r'Video: h264.*1600x900.*\b'+str(plan['fps'])+r' fps', probe.stderr):
        raise ValueError('Unexpected video encoding')
    if not re.search(r'Audio: aac.*24000 Hz, mono', probe.stderr):
        raise ValueError('Unexpected audio encoding')
    total_samples = 0
    for segment in plan['segments']:
        with wave.open(str(work/'parts'/(segment['id']+'.wav')), 'rb') as audio:
            if audio.getframerate() != 24000 or audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                raise ValueError('Invalid scene PCM format')
            if audio.getnframes() != round(segment['duration'] * 24000):
                raise ValueError('Scene audio length mismatch: '+segment['id'])
            total_samples += audio.getnframes()
    if total_samples != round(expected*24000):
        raise ValueError('PCM timeline differs from requested duration')
    result = subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-xerror',
                             '-i', str(work/'lesson.mp4'), '-map', '0:v:0', '-map', '0:a:0',
                             '-f', 'null', '-', '-progress', 'pipe:1', '-nostats'],
                            capture_output=True, text=True, encoding='utf-8', errors='replace')
    (work/'decode.log').write_text(result.stderr, encoding='utf-8')
    if result.returncode or result.stderr.strip():
        raise ValueError('Final video/audio failed full decoding')
    frames = re.findall(r'^frame=(\d+)', result.stdout, re.M)
    if not frames or int(frames[-1]) != frames_expected:
        raise ValueError('Decoded video frame count differs from timeline')
    timestamps = re.findall(r'^out_time_us=(\d+)', result.stdout, re.M)
    if not timestamps or abs(int(timestamps[-1])/1e6 - expected) > .12:
        raise ValueError('Decoded timeline has wrong duration')
    def seconds(text):
        h, m, s = text.replace(',', '.').split(':')
        return int(h)*3600 + int(m)*60 + float(s)
    srt = (work/'lesson.srt').read_text(encoding='utf-8')
    ranges = re.findall(r'(\d\d:\d\d:\d\d,\d{3}) --> (\d\d:\d\d:\d\d,\d{3})', srt)
    previous = 0
    for a, b in ranges:
        start, end = seconds(a), seconds(b)
        if start < previous or end <= start or end > expected:
            raise ValueError('Invalid final subtitle timing')
        previous = end
    if len(ranges) != plan['subtitleCues'] or not ranges:
        raise ValueError('Final subtitle cue count differs from planned cues')
    report = {'durationSeconds': duration, 'expectedDurationSeconds': expected,
              'frames': frames_expected, 'fullDecodePass': True, 'pcmSamples': total_samples,
              'subtitleCues': len(ranges), 'timelineSha256': digest(plan_path),
              'renderManifestSha256': digest(work/'render-manifest.json'),
              'assets': {name: digest(work/name) for name in ASSETS}}
    (work/'verification.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print('VIDEO_VERIFIED', json.dumps(report))
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--ffmpeg', type=Path, required=True)
    a = p.parse_args()
    verify(a.work.resolve(), a.ffmpeg.resolve())
