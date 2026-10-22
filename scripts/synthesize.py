#!/usr/bin/env python3
"""Optional Edge speech adapter: resumable narration and service-aligned captions.

Sends narration text only to Microsoft's online Edge speech service. Install
edge-tts==7.2.8 separately if this provider is selected. Supply --ffmpeg or install
imageio-ffmpeg==0.6.0 separately. No dependency installation occurs in this script.
Voice and rate are explicit script fields or command arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import math
import re
import subprocess
import textwrap
from pathlib import Path
from typing import Any


FORMAT_VERSION = 3
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def safe_id(value: Any) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value) or ".." in value:
        raise ValueError(f"Invalid segment/chapter id: {value!r}")
    return value


def segments_from_script(script: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if "chapters" in script:
        chapters = script["chapters"]
        if not isinstance(chapters, list):
            raise ValueError("chapters must be a list")
        groups = [(safe_id(c["id"]), c["segments"]) for c in chapters]
    else:
        groups = [("", script.get("segments", []))]
    chapter_ids = [chapter_id.casefold() for chapter_id, _ in groups]
    if len(set(chapter_ids)) != len(chapter_ids):
        raise ValueError('Chapter IDs must be unique ignoring case')
    for chapter_id, segments in groups:
        if not isinstance(segments, list):
            raise ValueError("segments must be a list")
        for segment in segments:
            local_id = safe_id(segment["id"])
            segment_id = f"{chapter_id}--{local_id}" if chapter_id else local_id
            narration = segment.get("narration", segment.get("text"))
            if not isinstance(narration, str) or not narration.strip():
                raise ValueError(f"Empty narration for {segment_id}")
            result.append({"id": segment_id, "chapterId": chapter_id, "text": narration.strip()})
    if not result or len({s["id"].casefold() for s in result}) != len(result):
        raise ValueError("Need at least one segment; flattened IDs must be unique ignoring case")
    return result


def timestamp(seconds: float, separator: str = ",") -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    whole_seconds, milliseconds = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02}{separator}{milliseconds:03}"


def restore_punctuation(words: list[dict[str, Any]], narration: str) -> None:
    """Recover original punctuation without changing service-provided word timing.

    Edge word metadata drops punctuation. Character alignment tolerates splits at
    apostrophes and hyphens, but refuses to silently drop or invent spoken words.
    """
    positions = [(index, folded) for index, char in enumerate(narration) if char.isalnum()
                 for folded in char.casefold()]
    normalized = "".join(char for _, char in positions)
    normalized_words = ["".join(char.casefold() for char in word["text"] if char.isalnum()) for word in words]
    if "".join(normalized_words) != normalized or any(not word for word in normalized_words):
        raise ValueError("Narration does not align with speech word metadata; spell out numbers and symbols")
    cursor = 0
    raw_start = 0
    for word, normalized_word in zip(words, normalized_words):
        cursor += len(normalized_word)
        raw_end = positions[cursor][0] if cursor < len(positions) else len(narration)
        word["text"] = narration[raw_start:raw_end].strip()
        raw_start = raw_end


def complete_word_timings(words: list[dict[str, Any]], duration: float) -> None:
    """Complete a service zero-duration word from the following real word offset.

    Ukrainian clitics such as «б» can have a valid offset but zero duration in
    Edge metadata. Keep that source duration in provenance and use the next
    word's measured start as the interval end. No offsets are shifted and no
    missing, negative, backwards, final, or ambiguous boundaries are guessed.
    """
    for index, word in enumerate(words):
        start, end = word["start"], word["end"]
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            raise ValueError("Speech service returned invalid word timestamps")
        if index and start < words[index - 1]["start"]:
            raise ValueError("Speech service returned backwards word offsets")
        if end == start:
            if index + 1 >= len(words) or words[index + 1]["start"] <= start:
                raise ValueError("Zero-duration speech word lacks a later measured boundary")
            word["serviceDuration"] = 0
            word["endSource"] = "next-word-start"
            word["end"] = words[index + 1]["start"]
        if word["end"] > duration + 0.25:
            raise ValueError("Speech timestamps extend beyond decoded audio")


def captions_from_words(words: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    """Group service word timings into readable, non-overlapping short captions."""
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if not current:
            return
        groups.append(current.copy())
        current.clear()

    for word in words:
        text_with_word = " ".join([w["text"] for w in current] + [word["text"]])
        wrapped = textwrap.wrap(text_with_word, width=42, break_long_words=False, break_on_hyphens=False)
        if current and (len(text_with_word) > 84 or len(wrapped) > 2 or word["end"] - current[0]["start"] > 6):
            flush()
        current.append(word)
        if re.search(r"[.!?…][\"»”']?$", word["text"]):
            flush()
    flush()
    # Avoid flashing a final one-word fragment for a fraction of a second.
    for index in range(1, len(groups)):
        previous, group = groups[index - 1], groups[index]
        if re.search(r"[.!?…][\"»”']?$", previous[-1]["text"]):
            continue
        while len(previous) > 2 and (len(group) < 3 or group[-1]["end"] - group[0]["start"] < 1.2):
            candidate = [previous[-1], *group]
            lines = textwrap.wrap(" ".join(w["text"] for w in candidate), width=42,
                                  break_long_words=False, break_on_hyphens=False)
            if len(lines) > 2 or candidate[-1]["end"] - candidate[0]["start"] > 6:
                break
            group.insert(0, previous.pop())
    captions: list[dict[str, Any]] = []
    for group in groups:
        start = group[0]["start"]
        end = min(duration, group[-1]["end"])
        if end <= start:
            raise ValueError("Speech service returned an invalid caption boundary")
        text = "\n".join(textwrap.wrap(" ".join(w["text"] for w in group), width=42,
                                      break_long_words=False, break_on_hyphens=False))
        captions.append({"start": round(start, 6), "end": round(end, 6), "text": text})
    for index, caption in enumerate(captions[:-1]):
        # Metadata can overlap by a few milliseconds around punctuation.
        caption["end"] = min(caption["end"], captions[index + 1]["start"])
        if caption["end"] <= caption["start"]:
            raise ValueError("Speech service returned overlapping zero-length captions")
    return captions


def srt_text(captions: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"{index}\n{timestamp(cue['start'])} --> {timestamp(cue['end'])}\n{cue['text']}"
        for index, cue in enumerate(captions, 1)
    ) + "\n"


def audio_duration(ffmpeg: str, path: Path) -> float:
    # Decode instead of relying on bitrate estimates from an MP3 without a VBR header.
    completed = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "s16le", "-ac", "1", "-ar", "24000", "pipe:1"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    duration = len(completed.stdout) / 48_000
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"No decodable audio in {path.name}")
    return duration


def cache_entry(directory: Path, segment_id: str, request_hash: str) -> dict[str, Any] | None:
    try:
        metadata = json.loads((directory / f"{segment_id}.json").read_text(encoding="utf-8"))
        if metadata.get("requestSha256") != request_hash:
            return None
        for kind, extension in (("audio", "mp3"), ("subtitles", "srt")):
            if digest((directory / f"{segment_id}.{extension}").read_bytes()) != metadata[f"{kind}Sha256"]:
                return None
        if not metadata.get("wordBoundaries") or not metadata.get("captions") or metadata.get("duration", 0) <= 0:
            return None
        return metadata
    except (OSError, ValueError, TypeError, KeyError):
        return None


async def synthesize_segment(segment: dict[str, str], directory: Path, *, voice: str, rate: str,
                             ffmpeg: str, engine_version: str, attempts: int) -> dict[str, Any]:
    import edge_tts

    segment_id = segment["id"]
    request = {"formatVersion": FORMAT_VERSION, "text": segment["text"], "voice": voice,
               "rate": rate, "engineVersion": engine_version, "boundary": "WordBoundary"}
    request_hash = digest(json_bytes(request))
    cached = cache_entry(directory, segment_id, request_hash)
    if cached is not None:
        print(f"Cached {segment_id}: {cached['duration']:.2f}s", flush=True)
        return cached
    temporary_audio = directory / f"{segment_id}.mp3.partial"
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            words: list[dict[str, Any]] = []
            communicate = edge_tts.Communicate(segment["text"], voice=voice, rate=rate,
                                               boundary="WordBoundary", receive_timeout=90)
            with temporary_audio.open("wb") as output:
                async for event in communicate.stream():
                    if event["type"] == "audio":
                        output.write(event["data"])
                    elif event["type"] == "WordBoundary":
                        start = event["offset"] / 10_000_000
                        end = (event["offset"] + event["duration"]) / 10_000_000
                        words.append({"start": start, "end": end, "text": html.unescape(event["text"])})
            if not words:
                raise ValueError("Speech service returned no word timestamps")
            restore_punctuation(words, segment["text"])
            duration = await asyncio.to_thread(audio_duration, ffmpeg, temporary_audio)
            complete_word_timings(words, duration)
            captions = captions_from_words(words, duration)
            audio_bytes = temporary_audio.read_bytes()
            subtitles = srt_text(captions).encode("utf-8")
            metadata = {"id": segment_id, "chapterId": segment["chapterId"], "voice": voice, "rate": rate,
                        "engine": "edge-tts", "engineVersion": engine_version, "duration": duration,
                        "durationSeconds": duration,
                        "audio": f"{segment_id}.mp3", "subtitles": f"{segment_id}.srt",
                        "srt": f"{segment_id}.srt",
                        "requestSha256": request_hash, "textSha256": digest(segment["text"].encode("utf-8")),
                        "audioSha256": digest(audio_bytes), "subtitlesSha256": digest(subtitles),
                        "wordBoundaries": words, "captions": captions}
            temporary_audio.replace(directory / metadata["audio"])
            atomic_write(directory / metadata["subtitles"], subtitles)
            atomic_write(directory / f"{segment_id}.json", json_bytes(metadata))
            print(f"Synthesized {segment_id}: {duration:.2f}s, {len(words)} words, {len(captions)} captions", flush=True)
            return metadata
        except Exception as error:
            last_error = error
            if temporary_audio.exists():
                temporary_audio.unlink()
            if isinstance(error, ValueError):
                # Invalid script/timing cannot be repaired by retrying the same request.
                break
            if attempt + 1 < attempts:
                wait = min(30, 2 ** (attempt + 1))
                # Do not print remote headers, URLs or payloads in diagnostic logs.
                print(f"Retry {segment_id}: {type(error).__name__}; waiting {wait}s", flush=True)
                await asyncio.sleep(wait)
    raise RuntimeError(f"Narration failed for {segment_id} after {attempt + 1} attempts ({type(last_error).__name__})") from last_error


async def run(args: argparse.Namespace) -> dict[str, Any]:
    import edge_tts

    source_bytes = args.script.read_bytes()
    script = json.loads(source_bytes)
    segments = segments_from_script(script)
    voice = args.voice or script.get("voice")
    rate = args.rate or script.get("rate", "+0%")
    if not isinstance(voice, str) or not voice.strip():
        raise ValueError("Choose an explicit voice appropriate to the requested language")
    if not re.fullmatch(r"[+-]\d+%", rate):
        raise ValueError("rate must be a signed percentage, for example -8%")
    args.output.mkdir(parents=True, exist_ok=True)
    if args.ffmpeg:
        ffmpeg = str(args.ffmpeg)
    else:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    semaphore = asyncio.Semaphore(args.concurrency)

    async def limited(segment: dict[str, str]) -> dict[str, Any]:
        async with semaphore:
            return await synthesize_segment(segment, args.output, voice=voice, rate=rate, ffmpeg=ffmpeg,
                                            engine_version=edge_tts.__version__, attempts=args.attempts)

    results = await asyncio.gather(*(limited(segment) for segment in segments))
    manifest = {"formatVersion": FORMAT_VERSION, "voice": voice, "rate": rate, "sampleRate": 24000,
                "sourceSha256": digest(source_bytes), "engine": "edge-tts",
                "engineVersion": edge_tts.__version__, "totalNarrationDuration": sum(r["duration"] for r in results),
                "segments": results}
    atomic_write(args.output / "manifest.json", json_bytes(manifest))
    print(f"Complete: {len(results)} segments, {manifest['totalNarrationDuration']:.2f}s narration", flush=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--voice")
    parser.add_argument("--rate")
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--concurrency", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--attempts", type=int, choices=range(1, 6), default=4)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
