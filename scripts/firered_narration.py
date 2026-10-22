#!/usr/bin/env python3
"""Local FireRed narration using a locally configured custom voice profile.

No voice profile ships with this skill; see assets/voices/README.md to set one up
from your own consented reference recording.

Outputs audio and provenance only. Measured subtitle alignment remains a separate
step; takes.unaligned.json cannot be imported as a completed narration manifest.
"""
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

from synthesize import atomic_write, json_bytes, segments_from_script

SKILL = Path(__file__).resolve().parents[1]
PROFILE_FILE = SKILL / 'assets/voices/profiles.json'
LANGUAGES = {'uk': 'Ukrainian', 'en': 'English'}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def resolve(script, key=None, profile_file=PROFILE_FILE):
    if not profile_file.is_file():
        raise ValueError(f'No voice profile configured; see assets/voices/README.md ({profile_file} not found)')
    profiles = json.loads(profile_file.read_text(encoding='utf-8'))
    key = key or profiles['default_profile']
    if key not in profiles['profiles']:
        raise ValueError(f"Unknown profile {key!r}; configured profiles: {sorted(profiles['profiles'])}")
    profile = profiles['profiles'][key]
    if script.get('voice') != profile['voice']:
        raise ValueError(f"Set script voice to {profile['voice']} for profile {key.upper()}")
    if script.get('rate', '+0%') != '+0%':
        raise ValueError('Selected profiles require rate +0%')
    if script.get('maxSpeechSpeed') != 1 or script.get('audioProcessing') != 'preserve-voice':
        raise ValueError('Set maxSpeechSpeed: 1.0 and audioProcessing: preserve-voice')
    tag = script['language'].lower().split('-')[0]
    if tag not in LANGUAGES:
        raise ValueError('This adapter supports explicit uk or en language tags')
    reference = profile_file.parent / profile['reference']
    transcript = profile_file.parent / profile['transcript']
    if sha(reference) != profile['reference_sha256'] or sha(transcript) != profile['transcript_sha256']:
        raise ValueError('Saved voice reference or transcript changed')
    config = {**profiles['generation'], **profile['generation'], 'language': LANGUAGES[tag],
              'voice': profile['voice'], 'profile': key, 'reference_sha256': sha(reference),
              'prompt_text': transcript.read_text(encoding='utf-8').strip()}
    return config, reference


def runtime_signature(runtime):
    folders = [runtime/'repo/fireredtts3', *(runtime/'models'/p for p in
               ('fireredtts3_base', 'redae', 'campp', 'text_tokenizer'))]
    files = []
    for folder in folders:
        found = sorted(p for p in folder.rglob('*') if p.is_file()
                       and p.suffix in ('.py', '.json', '.safetensors', '.bin'))
        if not found:
            raise ValueError(f'Missing FireRed runtime assets: {folder}')
        files.extend(found)
    return {'files': {str(p.relative_to(runtime)): sha(p) for p in files},
            'packages': {name: importlib.metadata.version(name) for name in
                         ('torch', 'torchaudio', 'transformers', 'numpy', 'soundfile')}}


def measure(ffmpeg, path):
    result = subprocess.run([str(ffmpeg), '-hide_banner', '-nostats', '-i', str(path),
        '-af', 'loudnorm=I=-23:TP=-2:LRA=11:print_format=json', '-f', 'null', '-'],
        capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
    values = json.loads(re.findall(r'\{[^{}]+\}', result.stderr)[-1])
    if not all(math.isfinite(float(values[k])) for k in ('input_i', 'input_tp')):
        raise ValueError(f'Non-finite audio measurement: {path}')
    return values


def generate(args):
    source = args.script.read_bytes()
    script = json.loads(source.decode('utf-8-sig'))
    config, reference = resolve(script, args.profile)
    segments = segments_from_script(script)
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError('--limit must be positive')
        segments = segments[:args.limit]
    if args.inspect:
        print(json.dumps({'config': config, 'reference': str(reference),
                          'segments': len(segments)}, ensure_ascii=False, indent=2))
        return
    if not args.runtime or not args.output or not args.ffmpeg:
        raise ValueError('Generation requires --runtime, --output, and --ffmpeg')
    runtime = args.runtime.resolve()
    output = args.output.resolve()
    if output == args.script.parent or output.is_relative_to(SKILL):
        raise ValueError('Choose a private generated-output directory outside the skill and script directory')
    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    runtime_info = runtime_signature(runtime)
    request = {'config': config, 'source_sha256': hashlib.sha256(source).hexdigest(),
               'runtime': runtime_info, 'adapter_sha256': sha(Path(__file__))}
    fingerprint = hashlib.sha256(json_bytes(request)).hexdigest()
    receipt_path = output/'generation.json'
    if output.exists() and any(output.iterdir()):
        if not receipt_path.is_file() or json.loads(receipt_path.read_text(encoding='utf-8')).get('fingerprint') != fingerprint:
            raise ValueError('Output contains different/unowned takes; choose a new output directory')
    output.mkdir(parents=True, exist_ok=True)
    raw = output/'raw'
    delivery = output/'audio'
    raw.mkdir(exist_ok=True)
    delivery.mkdir(exist_ok=True)
    receipt = {'fingerprint': fingerprint, **request, 'complete': False, 'segments': []}
    atomic_write(receipt_path, json_bytes(receipt))
    import numpy as np
    import soundfile as sf
    import torch
    sys.path.insert(0, str(runtime/'repo'))
    from fireredtts3.core import FireRedTTS3
    torch.set_num_threads(4)
    model = None
    prompt, reference_rate = sf.read(reference, dtype='float32', always_2d=True)
    if reference_rate != 24000 or prompt.shape[1] != 1:
        raise ValueError('Expected the saved mono 24 kHz reference')
    audio_prompt = torch.from_numpy(prompt.T.copy())
    for segment in segments:
        path = raw/(segment['id']+'.wav')
        stamp = path.with_suffix('.json')
        previous = json.loads(stamp.read_text(encoding='utf-8')) if stamp.exists() else {}
        cached = path.exists() and previous.get('fingerprint') == fingerprint and previous.get('audio_sha256') == sha(path)
        if not cached:
            if model is None:
                model = FireRedTTS3(str(runtime/'models'), use_fasttext=False,
                                   use_wetext=False, use_llm_tn=False)
            audio, rate = model.generate(text=segment['text'], prompt_audio=audio_prompt,
                prompt_audio_sr=reference_rate, prompt_text=config['prompt_text'],
                **{k: config[k] for k in ('language', 'seed', 'stop_threshold',
                    'inference_cfg', 'n_timesteps', 'do_clean', 'do_tn', 'do_split')})
            samples = audio.squeeze(0).detach().cpu().numpy()
            if rate != 24000 or samples.ndim != 1 or not len(samples) or not np.isfinite(samples).all():
                raise ValueError('Invalid generated audio')
            sf.write(path, samples, rate, subtype='FLOAT')
            previous = {**segment, 'fingerprint': fingerprint, 'audio_sha256': sha(path),
                        'duration': len(samples)/rate, 'sample_rate': rate}
            atomic_write(stamp, json_bytes(previous))
        receipt['segments'].append(previous)
        atomic_write(receipt_path, json_bytes(receipt))
        print(f"{config['profile'].upper()} {segment['id']}: {previous['duration']:.2f}s ({'cached' if cached else 'generated'})", flush=True)
    # One gain across scenes preserves relative dynamics. FLOAT protects native
    # peaks before gain, and true-peak measurement protects later PCM conversion.
    joined = output/'measurement-raw.wav'
    with sf.SoundFile(joined, 'w', samplerate=24000, channels=1, subtype='FLOAT') as stream:
        for i, segment in enumerate(segments):
            if i:
                stream.write(np.zeros(8400, dtype='float32'))
            stream.write(sf.read(raw/(segment['id']+'.wav'), dtype='float32')[0])
    total_measurement = measure(args.ffmpeg, joined)
    individual = [measure(args.ffmpeg, raw/(s['id']+'.wav')) for s in segments]
    gain = min(-23-float(total_measurement['input_i']),
               -2.1-max(float(m['input_tp']) for m in individual))
    takes = []
    for segment, raw_info in zip(segments, individual):
        samples, rate = sf.read(raw/(segment['id']+'.wav'), dtype='float32')
        target = delivery/(segment['id']+'.wav')
        sf.write(target, samples * (10**(gain/20)), rate, subtype='FLOAT')
        final_info = measure(args.ffmpeg, target)
        if float(final_info['input_tp']) > -2.0:
            raise ValueError('Delivery audio lacks true-peak headroom')
        takes.append({'id': segment['id'], 'text': segment['text'],
            'audio': target.relative_to(output).as_posix(), 'audio_sha256': sha(target),
            'duration': len(samples)/rate, 'input_measurement': raw_info,
            'delivery_measurement': final_info})
    unaligned = {'provider': 'FireRedTTS3-Base', 'voice': config['voice'], 'rate': '+0%',
                 'alignmentMethod': 'required', 'segments': takes}
    atomic_write(output/'takes.unaligned.json', json_bytes(unaligned))
    receipt.update(complete=len(segments) == len(segments_from_script(script)),
                   constant_gain_db=gain, device=torch.cuda.get_device_name(0),
                   delivery=takes, alignment='required')
    atomic_write(receipt_path, json_bytes(receipt))
    print('Audio ready; real subtitle alignment is still required.', flush=True)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('script', type=Path)
    parser.add_argument('--profile', help='Profile key from assets/voices/profiles.json (default: default_profile)')
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--ffmpeg', type=Path)
    parser.add_argument('--gpu', default='0')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--inspect', action='store_true')
    generate(parser.parse_args())


if __name__ == '__main__':
    main()
