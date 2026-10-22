#!/usr/bin/env python3
"""Assemble a timed screen lesson from verified sources and aligned narration."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import html
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
from render_integrity import input_hashes, output_hashes
from synthesize import segments_from_script

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
FPS = 10
LEAD = 0.5



def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def timestamp(value, comma=False):
    total = round(value * 1000)
    hours, rest = divmod(total, 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, ms = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{',' if comma else '.'}{ms:03}"


def chapter_timing(durations, target, weights=None, *, fps=FPS, max_speed=1.12):
    """Whole video frames, bounded speech adjustment and exact chapter duration."""
    if (not durations or not math.isfinite(target) or target <= 0
            or any(not math.isfinite(d) or d < 0 for d in durations)
            or not math.isfinite(max_speed) or not 1 <= max_speed <= 1.25):
        raise ValueError("Invalid chapter durations")
    target_frames = round(target * fps)
    if abs(target_frames / fps - target) > 1e-7:
        raise ValueError("Chapter target must be a whole number of video frames")
    available = target - len(durations) * (LEAD + 0.6)
    if available <= 0:
        raise ValueError("Chapter has too many scenes")
    speed = max(1.0, sum(durations) / available)
    if speed > max_speed:
        raise ValueError(f"Narration exceeds chapter budget: needs {speed:.2f}x. Shorten the script.")
    frames = [math.ceil((d / speed + LEAD + 0.4) * fps) for d in durations]
    extra = target_frames - sum(frames)
    if extra < 0:
        raise ValueError("Rounding exceeded chapter budget")
    weights = weights if weights is not None else [max(1, d) for d in durations]
    if len(weights) != len(durations) or any(not math.isfinite(w) or w <= 0 for w in weights):
        raise ValueError("Each scene needs a positive finite pause weight")
    shares = [extra * w / sum(weights) for w in weights]
    additions = [math.floor(n) for n in shares]
    remainder = extra - sum(additions)
    order = sorted(range(len(shares)), key=lambda i: shares[i] - additions[i], reverse=True)
    for i in order[:remainder]:
        additions[i] += 1
    return speed, [(f + a) / fps for f, a in zip(frames, additions)]


def audio_processing_mode(script):
    mode = script.get('audioProcessing', 'standard')
    if mode not in ('standard', 'preserve-voice'):
        raise ValueError('audioProcessing must be standard or preserve-voice')
    if mode == 'preserve-voice':
        maximum = script.get('maxSpeechSpeed')
        if isinstance(maximum, bool) or not isinstance(maximum, (int, float)) or maximum != 1:
            raise ValueError('preserve-voice requires explicit maxSpeechSpeed=1')
    return mode


def scene_audio_filter(scene, mode):
    if mode == 'preserve-voice':
        if scene['speechSpeed'] != 1:
            raise ValueError('preserve-voice cannot change the speech speed')
        # Pre-normalized safe audio: only insert the existing timeline silence.
        return 'adelay=500,apad'
    if mode != 'standard':
        raise ValueError('Unknown audio processing mode')
    return f"atempo={scene['speechSpeed']:.8f},adelay=500,apad,alimiter=limit=0.95"


def final_audio_bitrate(mode):
    return {'standard': '48k', 'preserve-voice': '128k'}[mode]


def verify_preserved_audio(ffmpeg, audio):
    """Measure decoded mono/24 kHz true peak before unprocessed PCM16 conversion."""
    result = subprocess.run(
        [str(ffmpeg), '-hide_banner', '-nostats', '-i', str(audio), '-map', '0:a:0',
         '-af', 'aresample=24000,aformat=channel_layouts=mono,loudnorm=I=-18:TP=-1:LRA=11:print_format=json',
         '-f', 'null', '-'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise ValueError(f'Cannot decode preserve-voice audio: {audio}: {result.stderr[-1200:]}')
    try:
        measurement = json.loads(re.findall(r'\{[^{}]+\}', result.stderr)[-1])
        peak = float(measurement['input_tp'])
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f'Missing decoded true-peak measurement: {audio}') from error
    if not math.isfinite(peak) or peak > -1.0:
        raise ValueError(f'preserve-voice requires decoded true peak <= -1 dBTP; measured {peak:g}: {audio}. Apply constant gain to the source and reimport it.')
    # loudnorm is measurement-only here. Its normalized output is discarded.
    return {'decodedTruePeakDbtp': peak, 'maximumTruePeakDbtp': -1.0,
            'measurementSampleRate': 24000, 'measurementChannels': 1,
            'audioSha256': hashlib.sha256(Path(audio).read_bytes()).hexdigest()}


def source_lines(screen, runs, source_root, allowed_sources, labels):
    kind = screen["kind"]
    label, context = "", ""
    if kind == "code" and "path" in screen:
        rel = screen["path"]
        candidate = source_root / rel
        path = candidate.resolve()
        allowed = [(source_root / p).resolve() for p in allowed_sources]
        if (Path(rel).is_absolute() or not path.is_relative_to(source_root)
                or not any(path == p or path.is_relative_to(p) for p in allowed)
                or candidate.is_symlink()):
            raise ValueError(f"Not a public teaching source: {rel}")
        value = path.read_text(encoding="utf-8")
        label = labels.get("codeSource", "SOURCE CODE")
        context = rel
        start, end = screen.get("lineStart", 1), screen.get("lineEnd")
    elif kind == "recording":
        run = runs[screen["runId"]]
        index = screen["frameIndex"]
        if not isinstance(index, int) or index < 0:
            raise ValueError("frameIndex must be zero or a positive integer")
        frame = run["frames"][index]
        value = frame["text"]
        if screen.get("contentField"):
            value = json.loads(value)[screen["contentField"]]
            if not isinstance(value, str):
                raise ValueError("Recorded content field must be text")
        if run.get("evidence") == "recorded":
            label = labels.get("recording", "ACTUAL RECORDING EXCERPT")
        else:
            label = labels.get("illustrativeRecording", "ILLUSTRATIVE WALKTHROUGH")
        label += " · " + frame.get("tool", frame.get("kind", "output"))
        context = f"{run['title']} · {index + 1} / {len(run['frames'])}"
        start, end = screen.get("excerptStartLine", 1), screen.get("excerptEndLine")
    else:
        value = screen.get("code", "")
        start, end = 1, None
        label = labels.get(kind, {"text": "EXPLANATION", "code": "TEACHING EXAMPLE", "exercise": "PRACTICE", "break": "BREAK"}[kind])
    lines = value.splitlines()
    end = len(lines) if end is None else end
    if not isinstance(end, int) or end > len(lines):
        raise ValueError(f"Source excerpt extends beyond available lines: {context}")
    if lines and (not isinstance(start, int) or start < 1 or start > len(lines) or end < start):
        raise ValueError(f"Invalid source excerpt: {context} {start}:{end}")
    rows = [{"number": i if kind == "code" and "path" in screen else None, "text": lines[i-1],
             "focus": i in screen.get("focusLines", [])} for i in range(start, end + 1)]
    return rows, label, context


def make_plan(script_path, narration_dir, work, *, source_root=None, allowed_sources=(), recordings=None, font_family="DejaVu Sans"):
    source_root = Path(source_root or Path(script_path).parent).resolve()
    work.mkdir(parents=True, exist_ok=True)
    script = read_json(script_path)
    narration = read_json(narration_dir / "manifest.json")
    takes = {s["id"]: s for s in narration["segments"]}
    script_hash = hashlib.sha256(Path(script_path).read_bytes()).hexdigest()
    if narration.get('sourceSha256') != script_hash:
        raise ValueError('Narration manifest does not match the current script; resume synthesis first')
    segments_from_script(script)
    if len(takes) != len(narration['segments']):
        raise ValueError('Duplicate narration segment IDs')
    if narration.get("voice") != script["voice"] or narration.get("rate") != script.get("rate", "+0%"):
        raise ValueError("Narration voice/rate differs from the approved script")
    audio_processing = audio_processing_mode(script)
    expected_ids = {c['id']+'--'+s['id'] for c in script['chapters'] for s in c['segments']}
    if set(takes) != expected_ids:
        raise ValueError('Narration segment set does not match the script')
    recorded = read_json(recordings) if recordings else {"runs": []}
    runs = {r["id"]: r for r in recorded["runs"]}
    if len(runs) != len(recorded["runs"]):
        raise ValueError("Duplicate recording IDs")
    duration = script["durationSeconds"]
    fps = script.get("fps", FPS)
    if isinstance(fps, bool) or not isinstance(fps, int) or fps < 1 or 24000 % fps:
        raise ValueError("fps must be a positive divisor of the 24000 Hz audio sample rate")
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError("durationSeconds must be positive and finite")
    if abs(sum(c["targetSeconds"] for c in script["chapters"]) - duration) > 1e-7:
        raise ValueError("Chapter budgets must total the requested durationSeconds")
    labels = script.get("labels", {})
    if not re.fullmatch(r"[\w .-]{1,80}", font_family):
        raise ValueError("Use a plain font family name")
    if not script.get("language") or not script.get("disclosure"):
        raise ValueError("Specify language and an accurate narration/recording disclosure")
    segments, chapters, screens, cues = [], [], [], []
    start = 0.0
    for chapter in script["chapters"]:
        ids = [chapter["id"] + "--" + s["id"] for s in chapter["segments"]]
        if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", sid) for sid in ids):
            raise ValueError("Unsafe segment identifier")
        samples = [takes[sid] for sid in ids]
        for source, sid, take in zip(chapter['segments'], ids, samples):
            expected_text_hash = hashlib.sha256(source['narration'].strip().encode('utf-8')).hexdigest()
            if take.get('textSha256') != expected_text_hash:
                raise ValueError(f'Stale narration text for {sid}')
            if take.get('voice') != narration['voice'] or take.get('rate') != narration['rate']:
                raise ValueError(f'Inconsistent voice provenance for {sid}')
            for field, digest_field in [('audio','audioSha256'),('subtitles','subtitlesSha256')]:
                file = (narration_dir/take[field]).resolve()
                if not file.is_relative_to(narration_dir.resolve()) or file.is_symlink():
                    raise ValueError(f'Unsafe narration artifact path for {sid}')
                if hashlib.sha256(file.read_bytes()).hexdigest() != take.get(digest_field):
                    raise ValueError(f'Changed narration artifact for {sid}: {field}')
            last_end = 0
            if not take['captions']:
                raise ValueError(f'Missing captions for {sid}')
            for cue in take['captions']:
                if cue['start'] < last_end or cue['end'] <= cue['start'] or cue['end'] > take['durationSeconds'] + .01:
                    raise ValueError(f'Invalid or overlapping narration captions for {sid}')
                last_end = cue['end']
            normalized = lambda value: ''.join(c.casefold() for c in value if c.isalnum())
            if normalized(' '.join(c['text'] for c in take['captions'])) != normalized(source['narration']):
                raise ValueError(f'Caption words differ from current narration for {sid}')
        durations = [t.get("durationSeconds", t.get("duration")) for t in samples]
        weights = [max(1, d) * (3 if s["screen"]["kind"] == "exercise" else 1)
                   for s, d in zip(chapter["segments"], durations)]
        speed, lengths = chapter_timing(durations, chapter["targetSeconds"], weights, fps=fps, max_speed=script.get("maxSpeechSpeed", 1.12))
        chapter_start = start
        chapters.append({"id": chapter["id"], "title": chapter["title"], "start": start,
                         "duration": chapter["targetSeconds"], "speechSpeed": speed,
                         "speechSeconds": sum(durations) / speed})
        for source, sid, take, raw_duration, length in zip(chapter["segments"], ids, samples, durations, lengths):
            speech = raw_duration / speed
            item = {"id": sid, "chapterId": chapter["id"], "chapterTitle": chapter["title"],
                    "title": source["title"], "narration": source["narration"], "start": start,
                    "duration": length, "speechDuration": speech, "lead": LEAD, "speechSpeed": speed,
                    "audio": str((narration_dir / take["audio"]).resolve()),
                    "audioSha256": take["audioSha256"], "kind": source["screen"]["kind"]}
            segments.append(item)
            rows, label, context = source_lines(source["screen"], runs, source_root, allowed_sources, labels)
            screen = {"title": source["screen"].get("heading", source["title"]),
                      "chapterTitle": chapter["title"], "kind": item["kind"], "label": label,
                      "context": context or script.get("brand", script["title"]), "start": start, "duration": duration,
                      "brand": script.get("brand", "WORKSHOP"), "disclosure": script["disclosure"]}
            if rows:
                screen["lines"] = rows
            if source["screen"].get("bullets"):
                screen["bullets"] = source["screen"]["bullets"]
            screens.append(screen)
            segment_cues = []
            for cue in take["captions"]:
                local_start = LEAD + cue["start"] / speed
                local_end = min(length, LEAD + cue["end"] / speed)
                if local_end <= local_start:
                    raise ValueError("Invalid caption timing")
                local = {"start": round(local_start, 3), "end": round(local_end, 3), "text": cue["text"]}
                segment_cues.append(local)
                cues.append({**local, "start": round(start + local_start, 3), "end": round(start + local_end, 3)})
            ass_path = work / "captions" / (sid + ".ass")
            write_ass(ass_path, segment_cues, font_family)
            item['captionsSha256'] = hashlib.sha256(ass_path.read_bytes()).hexdigest()
            start = (round(start * fps) + round(length * fps)) / fps
        if abs(start - chapter_start - chapter["targetSeconds"]) > 0.01:
            raise ValueError("Chapter rounding mismatch")
    if len({s['id'] for s in segments}) != len(segments) or abs(start - duration) > .01:
        raise ValueError("Invalid final timeline")
    plan = {"title": script["title"], "duration": start, "voice": narration["voice"],
            "rate": narration["rate"], "chapters": chapters, "segments": segments,
            "audioProcessing": audio_processing, "maxSpeechSpeed": script.get('maxSpeechSpeed', 1.12),
            "fps": fps, "fontFamily": font_family, "language": script["language"], "subtitleCues": len(cues),
            "labels": labels, "disclosure": script["disclosure"],
            "description": script.get("description", ""), "links": script.get("links", []),
            "brand": script.get("brand", "WORKSHOP"),
            "scriptSha256": hashlib.sha256(Path(script_path).read_bytes()).hexdigest()}
    template = (ASSETS / "screen.template.html").read_text(encoding="utf-8")
    encoded = json.dumps(screens, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    (work / "screens.html").write_text(template.replace("__SCREEN_DATA__", encoded).replace("__LANGUAGE__", html.escape(script["language"], quote=True)), encoding="utf-8")
    srt = "\n\n".join(f"{i+1}\n{timestamp(c['start'], True)} --> {timestamp(c['end'], True)}\n{c['text']}" for i, c in enumerate(cues)) + "\n"
    vtt = "WEBVTT\n\n" + "\n\n".join(f"{timestamp(c['start'])} --> {timestamp(c['end'])}\n{c['text']}" for c in cues) + "\n"
    (work / "lesson.srt").write_text(srt, encoding="utf-8", newline="\n")
    (work / "lesson.vtt").write_text(vtt, encoding="utf-8", newline="\n")
    transcript = [f"# {script['title']}", "", script["disclosure"], ""]
    for chapter in chapters:
        transcript += [f"## {timestamp(chapter['start'])[:8]} · {chapter['title']}", ""]
        for s in segments:
            if s['chapterId'] == chapter['id']:
                transcript += [f"### {s['title']}", "", s['narration'], ""]
    (work / "transcript.md").write_text("\n".join(transcript), encoding="utf-8", newline="\n")
    plan['textAssets'] = {name: hashlib.sha256((work/name).read_bytes()).hexdigest()
                          for name in ('lesson.srt', 'lesson.vtt', 'transcript.md')}
    write_json(work / "timeline.json", plan)
    print(f"VIDEO_PLAN_READY: {len(segments)} screens, {len(cues)} cues, {start:.1f} seconds")
    for c in chapters:
        print(f"{c['id']}: speech={c['speechSeconds']:.1f}s / {c['duration']}s, speed={c['speechSpeed']:.3f}")


def write_ass(path, cues, font_family="DejaVu Sans"):
    def ass_time(value):
        centiseconds = round(value * 100)
        hours, rest = divmod(centiseconds, 360000)
        minutes, rest = divmod(rest, 6000)
        seconds, fraction = divmod(rest, 100)
        return f"{hours}:{minutes:02}:{seconds:02}.{fraction:02}"
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1600
PlayResY: 900
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Segoe UI,31,&H00F4F8FA,&H000000FF,&H0003080B,&H0003080B,0,0,0,0,100,100,0,0,1,1,0,2,78,78,18,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = []
    for c in cues:
        text = c["text"].replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", r"\N")
        body.append(f"Dialogue: 0,{ass_time(c['start'])},{ass_time(c['end'])},Default,,0,0,0,,{text}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header.replace("Segoe UI", font_family) + "\n".join(body) + "\n", encoding="utf-8-sig")


def run_ffmpeg(command, log):
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    log.write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"FFmpeg failed; see {log}")


def render(work, ffmpeg, workers, font):
    # A failed or interrupted attempt must never retain an older success receipt.
    (work/'render-manifest.json').unlink(missing_ok=True)
    (work/'verification.json').unlink(missing_ok=True)
    plan_bytes = (work/'timeline.json').read_bytes()
    plan = json.loads(plan_bytes)
    audio_processing = audio_processing_mode(plan)
    aac_bitrate = final_audio_bitrate(audio_processing)
    checked_inputs = input_hashes(work, plan)
    if checked_inputs['files']['timeline.json'] != hashlib.sha256(plan_bytes).hexdigest():
        raise ValueError('Plan changed while starting rendering; render again')
    validation = read_json(work / 'screen-validation.json')
    for name, field in [('timeline.json', 'timelineSha256'), ('screens.html', 'htmlSha256')]:
        if validation.get(field) != checked_inputs['files'][name]:
            raise ValueError('Screen validation is stale; recapture all screens')
    for s in plan['segments']:
        image = work/'screens'/(s['id']+'.png')
        if validation.get('screens', {}).get(s['id']) != checked_inputs['files']['screens/'+s['id']+'.png']:
            raise ValueError('Screen capture is unverified: '+s['id'])
        if checked_inputs['narration'][s['id']] != s['audioSha256']:
            raise ValueError('Narration changed since planning: '+s['id'])
        if checked_inputs['files']['captions/'+s['id']+'.ass'] != s['captionsSha256']:
            raise ValueError('Captions changed since planning: '+s['id'])
    parts = work / "parts"
    parts.mkdir(exist_ok=True)
    fonts = work / 'fonts'
    fonts.mkdir(exist_ok=True)
    if not font.is_file():
        raise ValueError("Supply a local font file covering the requested language")
    if font.suffix.lower() not in (".ttf", ".otf"):
        raise ValueError("Font must be a TTF or OTF file")
    shutil.copyfile(font, fonts / ("captions" + font.suffix.lower()))
    font_name = "fonts/captions" + font.suffix.lower()
    fps = plan["fps"]
    render_fingerprint = hashlib.sha256(Path(__file__).read_bytes() + font.read_bytes() +
        subprocess.check_output([str(ffmpeg), '-version']) + audio_processing.encode() + aac_bitrate.encode()).hexdigest()
    audio_safety_checks = {}
    def one(s):
        audio_filter = scene_audio_filter(s, audio_processing)
        if audio_processing == 'preserve-voice':
            audio_safety_checks[s['id']] = verify_preserved_audio(ffmpeg, Path(s['audio']))
        image = work / "screens" / (s['id'] + '.png')
        caption = work / "captions" / (s['id'] + '.ass')
        output = parts / (s['id'] + '.mp4')
        wave = output.with_suffix('.wav')
        key = hashlib.sha256(render_fingerprint.encode() + json.dumps(s, sort_keys=True).encode() + image.read_bytes() + caption.read_bytes() + Path(s['audio']).read_bytes()).hexdigest()
        marker = output.with_suffix('.sha256')
        if output.is_file() and wave.is_file() and marker.is_file():
            try:
                saved = read_json(marker)
                if (saved['request'] == key and saved['video'] == hashlib.sha256(output.read_bytes()).hexdigest()
                        and saved['audio'] == hashlib.sha256(wave.read_bytes()).hexdigest()):
                    return s['id'] + ' cached'
            except (ValueError, KeyError, TypeError):
                pass
        # Subtitle paths are generated identifiers, relative to a fixed working directory.
        subtitle_filter = 'subtitles=filename=captions/' + s['id'] + '.ass:fontsdir=fonts'
        end_of_voice = s['lead'] + s['speechDuration']
        pause = s['duration'] - end_of_voice
        if pause > 8:
            pause_text = plan['labels'].get('break', 'Break') if s['kind'] == 'break' else plan['labels'].get('practicePause', 'Time to read and practice')
            pause_file = work / ('pause-' + s['id'] + '.txt')
            pause_file.write_text(pause_text, encoding='utf-8')
            subtitle_filter += f",drawtext=textfile={pause_file.name}:fontfile={font_name}:fontsize=27:fontcolor=0xc4d8e2:x=(w-tw)/2:y=824:enable='gte(t,{end_of_voice + 0.1:.3f})'"
            subtitle_filter += f",drawtext=text='%{{eif\\:ceil({s['duration']:.3f}-t)\\:d}}':fontfile={font_name}:fontsize=22:fontcolor=0x87a5b5:x=(w-tw)/2:y=863:enable='gte(t,{end_of_voice + 0.1:.3f})'"
        command = [str(ffmpeg), '-y', '-hide_banner', '-loglevel', 'warning', '-loop', '1', '-framerate', str(fps), '-i', str(image), '-i', s['audio'],
                   '-map', '0:v', '-vf', subtitle_filter,
                   '-t', f"{s['duration']:.8f}", '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'stillimage', '-crf', '25',
                   '-r', str(fps), '-g', '600', '-keyint_min', '600', '-sc_threshold', '0', '-pix_fmt', 'yuv420p', '-threads', '2',
                   '-movflags', '+faststart', str(output), '-map', '1:a',
                   '-af', audio_filter,
                   '-t', f"{s['duration']:.8f}", '-c:a', 'pcm_s16le', '-ac', '1', '-ar', '24000', str(wave)]
        result = subprocess.run(command, cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        output.with_suffix('.log').write_text(result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(f"Render failed for {s['id']}: {result.stderr[-1200:]}")
        write_json(marker, {'request': key, 'video': hashlib.sha256(output.read_bytes()).hexdigest(),
                            'audio': hashlib.sha256(wave.read_bytes()).hexdigest()})
        return s['id']
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, s) for s in plan['segments']]
        for i, future in enumerate(as_completed(futures), 1):
            print(f"RENDERED {i}/{len(futures)} {future.result()}", flush=True)
    listing = work / 'parts.txt'
    listing.write_text('\n'.join("file 'parts/"+s['id']+".mp4'" for s in plan['segments'])+'\n', encoding='utf-8')
    audio_listing = work / 'audio-parts.txt'
    audio_listing.write_text('\n'.join("file 'parts/"+s['id']+".wav'" for s in plan['segments'])+'\n', encoding='utf-8')
    metadata = [';FFMETADATA1', 'title='+metadata_escape(plan['title']), 'language='+metadata_escape(plan['language'])]
    for c in plan['chapters']:
        title=metadata_escape(c['title'])
        metadata += ['[CHAPTER]', 'TIMEBASE=1/1000', f"START={round(c['start']*1000)}", f"END={round((c['start']+c['duration'])*1000)}", 'title='+title]
    (work/'chapters.txt').write_text('\n'.join(metadata)+'\n',encoding='utf-8')
    run_ffmpeg([str(ffmpeg),'-y','-hide_banner','-f','concat','-safe','0','-i',str(listing),
                '-f','concat','-safe','0','-i',str(audio_listing),'-i',str(work/'chapters.txt'),
                '-map','0:v','-map','1:a','-map_metadata','2','-map_chapters','2','-c:v','copy',
                '-c:a','aac','-b:a',aac_bitrate,'-ac','1','-ar','24000','-t',str(plan['duration']),
                '-movflags','+faststart',str(work/'lesson.mp4')],work/'concat.log')
    shutil.copyfile(work/'screens'/f"{plan['segments'][0]['id']}.png",work/'poster.png')
    if input_hashes(work, plan) != checked_inputs:
        raise ValueError('Render inputs changed during rendering; render again')
    write_json(work/'render-manifest.json', {'formatVersion': 1, 'inputs': checked_inputs,
               'outputs': output_hashes(work, plan), 'renderFingerprint': render_fingerprint,
               'audioProcessing': audio_processing, 'audioSafetyChecks': audio_safety_checks,
               'audioEncoding': {'codec': 'aac', 'bitrate': aac_bitrate, 'sampleRate': 24000, 'channels': 1}})
    print('VIDEO_RENDERED',work/'lesson.mp4',flush=True)


def metadata_escape(value):
    return str(value).replace("\\", "\\\\").replace("\n", " ").replace("=", r"\=").replace(";", r"\;").replace("#", r"\#")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['plan','render'])
    p.add_argument('--script',type=Path)
    p.add_argument('--narration',type=Path)
    p.add_argument('--work',type=Path,required=True)
    p.add_argument('--source-root',type=Path)
    p.add_argument('--allow-source',action='append',default=[])
    p.add_argument('--recordings',type=Path)
    p.add_argument('--font-family',default='DejaVu Sans')
    p.add_argument('--font',type=Path)
    p.add_argument('--ffmpeg',type=Path)
    p.add_argument('--workers',type=int,default=3)
    a=p.parse_args();a.work=a.work.resolve();a.work.mkdir(parents=True,exist_ok=True)
    if a.action=='plan':
        if not a.script or not a.narration: p.error('plan requires --script and --narration')
        make_plan(a.script.resolve(),a.narration.resolve(),a.work,source_root=a.source_root,allowed_sources=a.allow_source,recordings=a.recordings,font_family=a.font_family)
    else:
        if not a.ffmpeg or not a.font: p.error('render requires --ffmpeg and --font')
        render(a.work,a.ffmpeg.resolve(),a.workers,a.font.resolve())


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    sys.stderr.reconfigure(encoding='utf-8',errors='replace')
    main()
