"""Bind a completed render to the exact inputs that were checked before rendering."""
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_hashes(work, plan):
    names = ['timeline.json', 'screens.html', 'screen-validation.json']
    for scene in plan['segments']:
        names.extend(['screens/'+scene['id']+'.png', 'captions/'+scene['id']+'.ass'])
    return {'files': {name: digest(work/name) for name in names},
            'narration': {scene['id']: digest(Path(scene['audio'])) for scene in plan['segments']}}


def output_hashes(work, plan):
    names = ['lesson.mp4', 'poster.png']
    for scene in plan['segments']:
        names.extend(['parts/'+scene['id']+suffix for suffix in ('.mp4', '.wav')])
    return {name: digest(work/name) for name in names}


def check_render(work, plan):
    try:
        receipt = json.loads((work/'render-manifest.json').read_text(encoding='utf-8'))
        if (receipt.get('formatVersion') != 1
                or receipt.get('inputs') != input_hashes(work, plan)
                or receipt.get('outputs') != output_hashes(work, plan)):
            raise ValueError('Completed render is stale; capture and render the current plan')
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('Completed render evidence is missing; render the current plan') from error
    return receipt
