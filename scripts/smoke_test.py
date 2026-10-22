#!/usr/bin/env python3
"""Render a labelled 12-second fixture using a local tone, never a speech service."""
import argparse
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import wave

HERE = Path(__file__).resolve().parent


def run(args):
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    if any(work.iterdir()):
        raise ValueError('Use an empty scratch directory for the smoke test')
    text = 'This is a pipeline test tone, not spoken narration.'
    script = {'title': 'CSV pipeline test', 'language': 'en', 'durationSeconds': 12,
              'voice': 'local-test-tone', 'rate': '+0%', 'brand': 'TEST FIXTURE',
              'description': 'Local media verification; not an actual teaching lesson.',
              'disclosure': 'Test tone only. No speech service was used.', 'chapters': []}
    inputs = {'provider': 'local-test-fixture', 'voice': 'local-test-tone', 'rate': '+0%',
              'alignmentMethod': 'fixture-test', 'segments': []}
    for index in range(2):
        cid = 'part'+str(index+1)
        screen = {'kind': 'code', 'heading': 'CSV has a header and records', 'code': 'name,city\nAda,London\nGrace,New York'} if not index else {
            'kind': 'exercise', 'heading': 'Read across the row', 'bullets': ['Find a name.', 'Read the city in the same row.']}
        script['chapters'].append({'id': cid, 'title': 'Test part '+str(index+1), 'targetSeconds': 6,
                                  'segments': [{'id': 'scene', 'title': 'Fixture screen', 'narration': text, 'screen': screen}]})
        file = work/(cid+'.wav')
        with wave.open(str(file), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
            audio.writeframes(b''.join(struct.pack('<h', int(2000*math.sin(2*math.pi*440*n/24000))) for n in range(48000)))
        inputs['segments'].append({'id': cid+'--scene', 'audio': file.name,
                                   'captions': [{'start': 0, 'end': 2, 'text': text}]})
    (work/'script.json').write_text(json.dumps(script, indent=2), encoding='utf-8')
    (work/'inputs.json').write_text(json.dumps(inputs, indent=2), encoding='utf-8')
    py = sys.executable
    def call(name, *arguments):
        subprocess.run([py, str(HERE/name), *map(str, arguments)], check=True)
    call('import_narration.py', '--script', work/'script.json', '--input', work/'inputs.json', '--output', work/'narration', '--ffmpeg', args.ffmpeg)
    call('build_video.py', 'plan', '--script', work/'script.json', '--narration', work/'narration', '--work', work, '--font-family', args.font_family)
    subprocess.run([args.node, str(HERE/'render_screens.cjs'), str(work)], check=True)
    call('build_video.py', 'render', '--work', work, '--ffmpeg', args.ffmpeg, '--font', args.font)
    call('verify_video.py', '--work', work, '--ffmpeg', args.ffmpeg)
    call('publish_video.py', '--work', work, '--destination', work/'public')
    print('SMOKE_PASS', work/'public/lesson.html')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--ffmpeg', type=Path, required=True)
    p.add_argument('--font', type=Path, required=True)
    p.add_argument('--font-family', default='DejaVu Sans')
    p.add_argument('--node', default='node')
    run(p.parse_args())
