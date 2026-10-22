"""Offline regression tests for the fragile timing/provenance boundaries."""
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from unittest.mock import patch

from build_video import (chapter_timing, make_plan, source_lines, write_ass,
                         scene_audio_filter, verify_preserved_audio, final_audio_bitrate)
from publish_video import check_verified, publish, safe_output
from synthesize import complete_word_timings, captions_from_words, restore_punctuation, segments_from_script
from verify_video import ASSETS, verify
from render_integrity import input_hashes, output_hashes, check_render
from import_narration import convert as import_narration


class TimingTests(unittest.TestCase):
    def test_random_chapters_keep_all_speech_and_exact_frames(self):
        rng = random.Random(9041)
        for fps in (10, 24, 25, 30):
            for _ in range(100):
                durations = [rng.uniform(2, 75) for _ in range(rng.randint(1, 10))]
                target = (round((sum(durations)+len(durations)*3)*fps)+rng.randint(0, 600))/fps
                speed, lengths = chapter_timing(durations, target, fps=fps)
                self.assertEqual(sum(round(n*fps) for n in lengths), round(target*fps))
                for speech, length in zip(durations, lengths):
                    self.assertGreaterEqual(length+1e-7, speech/speed+.9)

    def test_overfull_or_fractional_frame_budget_fails(self):
        for durations, target in [([100, 100], 120), ([1], 4.01)]:
            with self.assertRaises(ValueError):
                chapter_timing(durations, target)

    def test_service_zero_word_uses_next_actual_offset(self):
        words = [{'start': 1, 'end': 1, 'text': 'a'}, {'start': 1.2, 'end': 1.6, 'text': 'word'}]
        complete_word_timings(words, 2)
        self.assertEqual(words[0]['end'], 1.2)
        self.assertEqual(words[0]['serviceDuration'], 0)
        for invalid in [[{'start': 1, 'end': 1}], [{'start': 1, 'end': .5}]]:
            with self.assertRaises(ValueError):
                complete_word_timings(invalid, 2)

    def test_grouped_captions_do_not_overlap_at_punctuation(self):
        words = [{'start': 0, 'end': 1.05, 'text': 'First.'}, {'start': 1, 'end': 2, 'text': 'Second.'}]
        cues = captions_from_words(words, 2)
        self.assertEqual(cues[0]['end'], cues[1]['start'])

    def test_punctuation_alignment_handles_casefold_expansion(self):
        words = [{'text': 'Straße'}, {'text': 'ist'}, {'text': 'hier'}]
        restore_punctuation(words, 'Straße ist hier.')
        self.assertEqual([word['text'] for word in words], ['Straße', 'ist', 'hier.'])

    def test_ass_minute_rollover(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'test.ass'
            write_ass(path, [{'start': 59.999, 'end': 61.22, 'text': '{text}\nnext'}])
            value = path.read_text(encoding='utf-8-sig')
            self.assertIn('0:01:00.00,0:01:01.22', value)
            self.assertIn('(text)\\Nnext', value)


class SourceTests(unittest.TestCase):
    def test_import_rejects_audio_replaced_after_alignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = {'voice': 'firered-test-a', 'rate': '+0%',
                      'segments': [{'id': 'intro', 'text': 'Test fixture.'}]}
            (root/'script.json').write_text(json.dumps(script), encoding='utf-8')
            audio = root/'voice.wav'
            original = b'original aligned audio fixture'
            audio.write_bytes(b'replacement with different voice or timing')
            for hash_field in ('audio_sha256', 'audioSha256'):
                with self.subTest(hash_field=hash_field):
                    supplied = {'provider': 'fixture', 'voice': script['voice'], 'rate': '+0%',
                        'alignmentMethod': 'fixture-test', 'segments': [{'id': 'intro',
                        'audio': audio.name, hash_field: hashlib.sha256(original).hexdigest(),
                        'captions': [{'start': 0, 'end': 1, 'text': 'Test fixture.'}]}]}
                    (root/'takes.json').write_text(json.dumps(supplied), encoding='utf-8')
                    destination = root/hash_field
                    with patch('import_narration.audio_duration') as decoder:
                        with self.assertRaisesRegex(ValueError, 'Audio changed after generation/alignment'):
                            import_narration(root/'script.json', root/'takes.json', destination, Path('unused-ffmpeg'))
                        decoder.assert_not_called()
                    self.assertFalse((destination/'manifest.json').exists())

    def test_case_colliding_output_ids_are_rejected_before_writing(self):
        scene = {'id': 'scene', 'narration': 'Test fixture.'}
        for script in [
            {'chapters': [{'id': 'intro', 'segments': [scene]},
                          {'id': 'INTRO', 'segments': [scene]}]},
            {'chapters': [{'id': 'intro', 'segments': [scene, dict(scene, id='SCENE')]}]},
        ]:
            with self.assertRaisesRegex(ValueError, 'ignoring case'):
                segments_from_script(script)

    def test_illustrative_or_unclassified_run_never_becomes_recorded(self):
        screen = {'kind': 'recording', 'runId': 'example', 'frameIndex': 0}
        run = {'title': 'Example', 'frames': [{'kind': 'output', 'text': 'Example output'}]}
        for evidence in (None, 'illustrative'):
            value = dict(run)
            if evidence is not None:
                value['evidence'] = evidence
            _, label, _ = source_lines(screen, {'example': value}, Path('.'), (), {})
            self.assertIn('ILLUSTRATIVE', label)
            self.assertNotIn('ACTUAL', label)
        _, label, _ = source_lines(screen, {'example': dict(run, evidence='recorded')}, Path('.'), (), {})
        self.assertIn('ACTUAL RECORDING', label)

    def test_sources_require_explicit_allowlist_and_in_range_excerpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root/'example.txt').write_text('one\ntwo\n', encoding='utf-8')
            screen = {'kind': 'code', 'path': 'example.txt'}
            with self.assertRaises(ValueError):
                source_lines(screen, {}, root, (), {})
            rows, _, _ = source_lines(screen, {}, root, ['example.txt'], {})
            self.assertEqual([row['text'] for row in rows], ['one', 'two'])
            with self.assertRaises(ValueError):
                source_lines({**screen, 'lineEnd': 99}, {}, root, ['example.txt'], {})
            with self.assertRaises(ValueError):
                source_lines({**screen, 'path': '../private.txt'}, {}, root, ['..'], {})

    def test_stale_script_is_rejected_before_planning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'script.json').write_text('{}', encoding='utf-8')
            (root/'manifest.json').write_text(json.dumps({'segments': [], 'sourceSha256': 'old'}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'current script'):
                make_plan(root/'script.json', root, root)


class PreserveVoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def tone(self, amplitude=.2):
        samples = b''.join(struct.pack('<h', round(amplitude * 32767 * math.sin(2 * math.pi * 440 * i / 24000)))
                           for i in range(24000))
        source = self.root/'tone.wav'
        with wave.open(str(source), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(samples)
        return source, samples

    def script_fixture(self, **options):
        source, _ = self.tone()
        script = {'title': 'Preserve voice fixture', 'voice': 'fixture-tone', 'rate': '+0%',
                  'language': 'en', 'disclosure': 'A test tone, not spoken narration.',
                  'durationSeconds': 4, 'audioProcessing': 'preserve-voice',
                  'maxSpeechSpeed': 1,
                  'chapters': [{'id': 'intro', 'title': 'Fixture', 'targetSeconds': 4,
                                'segments': [{'id': 'tone', 'title': 'Fixture',
                                              'narration': 'Test fixture.', 'screen': {'kind': 'text'}}]}]}
        script.update(options)
        if script.get('maxSpeechSpeed') is None:
            script.pop('maxSpeechSpeed')
        script_path = self.root/'script.json'
        script_path.write_text(json.dumps(script), encoding='utf-8')
        subtitle = self.root/'tone.srt'
        subtitle.write_text('Fixture subtitle', encoding='utf-8')
        take = {'id': 'intro--tone', 'voice': 'fixture-tone', 'rate': '+0%', 'audio': source.name,
                'subtitles': subtitle.name, 'durationSeconds': 1,
                'audioSha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'subtitlesSha256': hashlib.sha256(subtitle.read_bytes()).hexdigest(),
                'textSha256': hashlib.sha256(b'Test fixture.').hexdigest(),
                'captions': [{'start': 0, 'end': 1, 'text': 'Test fixture.'}]}
        (self.root/'manifest.json').write_text(json.dumps({'voice': 'fixture-tone', 'rate': '+0%',
                'sourceSha256': hashlib.sha256(script_path.read_bytes()).hexdigest(), 'segments': [take]}), encoding='utf-8')
        return script_path

    def ffmpeg(self):
        executable = os.environ.get('WORKSHOP_TEST_FFMPEG') or shutil.which('ffmpeg')
        if not executable:
            self.skipTest('Set WORKSHOP_TEST_FFMPEG to run real audio preservation checks')
        return Path(executable)

    def test_preserve_plan_requires_explicit_unaccelerated_budget(self):
        for maximum in (None, 1.12, True):
            script = self.script_fixture(maxSpeechSpeed=maximum)
            with self.assertRaisesRegex(ValueError, 'requires explicit maxSpeechSpeed=1'):
                make_plan(script, self.root, self.root/'work')
        script = self.script_fixture()
        make_plan(script, self.root, self.root/'work')
        plan = json.loads((self.root/'work/timeline.json').read_text(encoding='utf-8'))
        self.assertEqual(plan['audioProcessing'], 'preserve-voice')
        self.assertEqual(plan['maxSpeechSpeed'], 1)
        self.assertEqual(plan['segments'][0]['speechSpeed'], 1)
        with self.assertRaisesRegex(ValueError, 'cannot change'):
            scene_audio_filter({'speechSpeed': 1.01}, 'preserve-voice')

    def test_legacy_filter_keeps_previous_default_behavior(self):
        self.assertEqual(scene_audio_filter({'speechSpeed': 1.12}, 'standard'),
                         'atempo=1.12000000,adelay=500,apad,alimiter=limit=0.95')
        self.assertEqual(final_audio_bitrate('standard'), '48k')
        self.assertEqual(final_audio_bitrate('preserve-voice'), '128k')

    def test_real_preserve_filter_keeps_every_pcm_sample_and_duration(self):
        ffmpeg = self.ffmpeg()
        source, original = self.tone()
        safety = verify_preserved_audio(ffmpeg, source)
        self.assertLess(safety['decodedTruePeakDbtp'], -10)
        destination = self.root/'preserved.wav'
        subprocess.run([str(ffmpeg), '-v', 'error', '-y', '-i', str(source),
                        '-af', scene_audio_filter({'speechSpeed': 1}, 'preserve-voice'),
                        '-t', '2', '-c:a', 'pcm_s16le', '-ar', '24000', '-ac', '1', str(destination)], check=True)
        with wave.open(str(destination), 'rb') as output:
            self.assertEqual(output.getnframes(), 48000)
            samples = output.readframes(output.getnframes())
        self.assertEqual(samples[:24000], bytes(24000))
        self.assertEqual(samples[24000:72000], original)
        self.assertEqual(samples[72000:], bytes(24000))

    def test_real_peak_guard_rejects_hot_audio_instead_of_limiting(self):
        ffmpeg = self.ffmpeg()
        source, _ = self.tone(amplitude=.98)
        with self.assertRaisesRegex(ValueError, 'requires decoded true peak'):
            verify_preserved_audio(ffmpeg, source)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = {'duration': 12, 'fps': 10, 'segments': []}
        (self.root/'timeline.json').write_text(json.dumps(self.plan), encoding='utf-8')
        self.report = {'durationSeconds': 12, 'expectedDurationSeconds': 12, 'frames': 120,
                       'fullDecodePass': True, 'timelineSha256': self.hash('timeline.json'), 'assets': {}}
        for name in ASSETS:
            (self.root/name).write_bytes(b'test fixture')
            self.report['assets'][name] = self.hash(name)
        for name in ('screens.html', 'screen-validation.json'):
            (self.root/name).write_bytes(b'test fixture')
        self.receipt = {'formatVersion': 1, 'inputs': input_hashes(self.root, self.plan),
                        'outputs': output_hashes(self.root, self.plan)}
        (self.root/'render-manifest.json').write_text(json.dumps(self.receipt), encoding='utf-8')
        self.report['renderManifestSha256'] = self.hash('render-manifest.json')

    def hash(self, name):
        return hashlib.sha256((self.root/name).read_bytes()).hexdigest()

    def save(self):
        (self.root/'verification.json').write_text(json.dumps(self.report), encoding='utf-8')

    def test_valid_hash_binding_accepts_any_duration(self):
        self.save()
        self.assertEqual(check_verified(self.root)[0]['duration'], 12)

    def test_changed_assets_or_timeline_block_publication(self):
        self.save()
        (self.root/'lesson.mp4').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'stale'):
            publish(self.root, self.root/'public')
        self.assertFalse((self.root/'public').exists())
        self.report['assets']['lesson.mp4'] = self.hash('lesson.mp4')
        self.save()
        (self.root/'timeline.json').write_text(json.dumps({**self.plan, 'duration': 14}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'stale'):
            publish(self.root, self.root/'public')

    def test_failed_full_decode_blocks_publication(self):
        self.report['fullDecodePass'] = False
        self.save()
        with self.assertRaisesRegex(ValueError, 'stale'):
            publish(self.root, self.root/'public')

    def test_replan_cannot_certify_previous_video(self):
        self.save()
        (self.root/'timeline.json').write_text(json.dumps({**self.plan, 'title': 'Revised lesson'}), encoding='utf-8')
        with patch('verify_video.subprocess.run') as decoder:
            with self.assertRaisesRegex(ValueError, 'stale'):
                verify(self.root, Path('unused-ffmpeg'))
            decoder.assert_not_called()

    def test_missing_completed_render_blocks_verification(self):
        (self.root/'render-manifest.json').unlink()
        with patch('verify_video.subprocess.run') as decoder:
            with self.assertRaisesRegex(ValueError, 'missing'):
                verify(self.root, Path('unused-ffmpeg'))
            decoder.assert_not_called()

    def test_changed_capture_invalidates_completed_render(self):
        (self.root/'screens.html').write_bytes(b'changed screen')
        with self.assertRaisesRegex(ValueError, 'stale'):
            check_render(self.root, self.plan)

    def test_failed_render_invalidates_previous_receipts(self):
        from build_video import render
        self.save()
        # This validation file is deliberately invalid JSON, so rendering fails early.
        with self.assertRaises(ValueError):
            render(self.root, Path('unused-ffmpeg'), 1, Path('unused-font'))
        self.assertFalse((self.root/'render-manifest.json').exists())
        self.assertFalse((self.root/'verification.json').exists())

    def test_unrelated_page_cannot_be_overwritten_even_with_replace(self):
        destination = self.root/'public'
        destination.mkdir()
        page = destination/'lesson.html'
        page.write_text('Existing handwritten page', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            safe_output(destination, 'lesson', False)
        with self.assertRaisesRegex(ValueError, 'not owned'):
            safe_output(destination, 'lesson', True)
        self.assertEqual(page.read_text(encoding='utf-8'), 'Existing handwritten page')

    def test_symlink_output_is_rejected(self):
        destination = self.root/'public'
        destination.mkdir()
        elsewhere = self.root/'outside'
        elsewhere.mkdir()
        try:
            (destination/'lesson-media').symlink_to(elsewhere, target_is_directory=True)
        except OSError:
            self.skipTest('Host does not permit symlink creation')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            safe_output(destination, 'lesson', True)
        self.assertEqual(list(elsewhere.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
