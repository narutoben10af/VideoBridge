"""Small synthetic regressions for the production TS packaging timeline."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import relay
from hls_publication import HLSPublisher
from subtitle_hls import package_webvtt


class TimestampTests(unittest.TestCase):
    def test_nonzero_map_is_consistent_across_progressive_segments(self):
        text = 'WEBVTT\n\n00:03.000 --> 00:09.000\nAcross original gap\n'
        first = package_webvtt(text, [8.004], 9, False, mpegts_origin=133508)
        final = package_webvtt(text, [8.004, 4.004], 9, True, mpegts_origin=133508)
        self.assertEqual(first.segments['sub0-000000.vtt'], final.segments['sub0-000000.vtt'])
        for data in final.segments.values():
            self.assertIn(b'X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:133508', data)
            self.assertIn(b'00:03.000 --> 00:09.000', data)

    def test_small_initial_offset_assigns_boundary_cues_without_changing_timing(self):
        text = 'WEBVTT\n\n00:04.005 --> 00:04.015\nFirst segment tail\n'
        package = package_webvtt(text, [4, 4], 4, True, mpegts_origin=126000, video_start_offset=0.021)
        self.assertIn(b'00:04.005 --> 00:04.015', package.segments['sub0-000000.vtt'])
        self.assertNotIn(b'First segment tail', package.segments['sub0-000001.vtt'])
        with self.assertRaises(ValueError):
            package_webvtt(text, [4], 4, True, video_start_offset=0.101)

    def test_wrap_and_invalid_epochs_fail_closed(self):
        for origin in [-1, True, 2**33, 2**33 - 90000, 1.5]:
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                package_webvtt('WEBVTT\n\n', [4], 4, True, mpegts_origin=origin)

    def test_unknown_or_wrapped_source_epochs_rejected_before_media_job(self):
        for epoch in [-1, float('nan'), 2**33/90000 - 1, None]:
            metadata = {'streams': [{'codec_type':'video', 'codec_name':'h264', 'pix_fmt':'yuv420p',
                                    'index':0, 'start_pts':0, 'time_base':'1/90000'}],
                        'format': {'duration':'4'}}
            if epoch is not None: metadata['format']['start_time'] = str(epoch)
            with self.subTest(epoch=epoch), tempfile.TemporaryDirectory() as folder:
                with patch.object(relay, 'probe', return_value=metadata), patch.object(relay.subprocess, 'Popen') as launch:
                    with self.assertRaises(ValueError):
                        relay.prepare({'source':'/unused'}, Path(folder), threading.Event(), os.getppid())
                    launch.assert_not_called()

    def test_video_measurement_uses_presentation_order_not_decode_order(self):
        data = {'streams': [{'time_base': '1/90000'}], 'packets': [{'pts': 140000}, {'pts': 133508}]}
        response = subprocess.CompletedProcess([], 0, json.dumps(data).encode(), b'')
        with patch.object(relay, 'run_cancellable', return_value=response):
            self.assertEqual(relay.video_presentation_origin(Path('/owned/segment000000.ts'), threading.Event(), os.getppid()), 133508)
        for points in [[], [{'pts': -1}], [{'pts': 2**33}], [{}]]:
            response.stdout = json.dumps(dict(data, packets=points)).encode()
            with patch.object(relay, 'run_cancellable', return_value=response), self.assertRaises(ValueError):
                relay.video_presentation_origin(Path('/owned/segment000000.ts'), threading.Event(), os.getppid())


@unittest.skipUnless(all(any(Path(prefix, tool).is_file() for prefix in ['/opt/homebrew/bin', '/usr/local/bin', '/usr/bin']) or shutil.which(tool) for tool in ['ffmpeg', 'ffprobe']), 'FFmpeg required')
class TransportTimelineTests(unittest.TestCase):
    def command(self, args):
        return subprocess.check_output(args, stderr=subprocess.PIPE, timeout=35)

    def frames(self, path):
        data = self.command([relay.executable('ffmpeg'), '-v', 'error', '-copyts', '-i', str(path),
                             '-map', '0:v:0', '-an', '-fps_mode', 'passthrough', '-pix_fmt', 'yuv420p', '-f', 'framemd5', '-']).decode()
        hashes = [line.split(',')[-1].strip() for line in data.splitlines() if line and not line.startswith('#')]
        frames = json.loads(self.command([relay.executable('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
                                         '-show_frames', '-show_entries', 'frame=pts_time', '-of', 'json', str(path)]))['frames']
        return hashes, [float(frame['pts_time']) for frame in frames]

    def test_staggered_video_is_rejected_before_preparation(self):
        if shutil.disk_usage('/').free < relay.EARLY_FLOOR + 30 * 1024**2:
            self.skipTest('Need 30 MiB above storage floor')
        with tempfile.TemporaryDirectory(prefix='videobridge-staggered-') as folder:
            root = Path(folder); source = root/'delayed.mkv'; captions = root/'cue.srt'
            captions.write_text('1\n00:00:02,000 --> 00:00:03,000\nTwo seconds on shared timeline\n')
            self.command([relay.executable('ffmpeg'), '-v', 'error', '-itsoffset', '2', '-f', 'lavfi',
                '-i', 'testsrc2=size=64x64:rate=10:duration=2', '-f', 'lavfi', '-i',
                'anullsrc=r=48000:cl=stereo:d=4', '-i', str(captions), '-map', '0:v', '-map', '1:a', '-map', '2:s',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-bf', '0', '-fps_mode', 'passthrough',
                '-c:a', 'aac', '-c:s', 'srt', '-output_ts_offset', '3600', str(source)])
            output = root/'output'; output.mkdir(); stop = threading.Event()
            metadata = relay.probe(str(source))
            with patch.object(relay, 'probe', return_value=metadata), patch.object(relay.subprocess, 'Popen') as launch:
                with self.assertRaisesRegex(ValueError, 'Initial audio/video offset exceeds 100 ms'):
                    relay.prepare(dict(source=str(source), subtitles=[dict(url=str(captions), label='Sidecar', language='en')]),
                                  output, stop, os.getppid())
                launch.assert_not_called()
            self.assertEqual(list(output.iterdir()), [])

    def test_production_preserves_frames_and_four_and_twelve_second_gaps(self):
        if shutil.disk_usage('/').free < relay.EARLY_FLOOR + 30 * 1024**2:
            self.skipTest('Need 30 MiB above storage floor')
        with tempfile.TemporaryDirectory(prefix='videobridge-timeline-') as folder:
            root = Path(folder); continuous = root/'continuous.ts'
            self.command([relay.executable('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                          'testsrc2=size=160x90:rate=24:duration=8', '-f', 'lavfi', '-i',
                          'sine=frequency=440:sample_rate=48000:duration=8', '-c:v', 'libx264',
                          '-preset', 'ultrafast', '-g', '96', '-bf', '2', '-c:a', 'aac', '-f', 'mpegts', str(continuous)])
            for gap, epoch in [(4, 0), (12, 3600)]:
                with self.subTest(gap=gap, epoch=epoch):
                    source = root/f'gap{gap}.ts'; output = root/f'output{gap}'; output.mkdir()
                    self.command([relay.executable('ffmpeg'), '-v', 'error', '-i', str(continuous), '-c', 'copy',
                                  '-bsf:v', f'setts=pts=PTS+gte(N\\,96)*{gap}/TB:dts=DTS+gte(N\\,96)*{gap}/TB',
                                  '-bsf:a', f'setts=pts=PTS+gte(N\\,188)*{gap}/TB:dts=DTS+gte(N\\,188)*{gap}/TB',
                                  '-output_ts_offset', str(epoch), '-f', 'mpegts', str(source)])
                    captions = root/'captions.vtt'
                    captions.write_text('WEBVTT\n\n00:03.000 --> 00:23.000\nUnchanged gap-spanning cue\n')
                    stop = threading.Event()
                    process, _, _, source_video_offset = relay.prepare(dict(source=str(source), subtitles=[dict(url=str(captions), label='English', language='en')]), output, stop, os.getppid())
                    try: self.assertEqual(process.wait(timeout=30), 0)
                    finally:
                        if process.poll() is None: process.terminate(); process.wait(timeout=5)
                    publisher = HLSPublisher(output, [output/'sub0.vtt'],
                                             timestamp_probe=lambda path: relay.video_presentation_origin(path, stop, os.getppid()) - source_video_offset,
                                             video_start_offset=source_video_offset/90000)
                    self.assertTrue(publisher.publish()); self.assertTrue(publisher.snapshot.complete)
                    original_hashes, original_pts = self.frames(source)
                    hashes, pts = self.frames(output/'video.m3u8')
                    self.assertEqual(len(hashes), 192); self.assertEqual(hashes, original_hashes)
                    shifts = [new-old for old,new in zip(original_pts, pts)]
                    self.assertLess(max(shifts)-min(shifts), 2/90000)
                    self.assertAlmostEqual(pts[96]-pts[95], gap+1/24, places=4)
                    # Audio is still disclosed stereo AAC conversion; preserve its
                    # timestamp gap instead of treating re-encoded packets as copies.
                    audio_gaps = []
                    for media in [source, output/'video.m3u8']:
                        audio = json.loads(self.command([relay.executable('ffprobe'), '-v', 'error',
                            '-select_streams', 'a:0', '-show_packets', '-show_entries', 'packet=pts_time,duration_time',
                            '-of', 'json', str(media)]))['packets']
                        times = [float(packet['pts_time']) for packet in audio]
                        audio_gaps.append(max(b-a for a,b in zip(times, times[1:])))
                        self.assertLess(max(float(packet['duration_time']) for packet in audio), 0.03)
                    self.assertAlmostEqual(audio_gaps[0], audio_gaps[1], places=4)
                    # Measured presentation epoch, not a fixed 1.4-second TS assumption.
                    self.assertAlmostEqual((publisher.mpegts_origin + source_video_offset)/90000, pts[0], places=4)
                    for path in output.glob('sub0-*.vtt'):
                        data = path.read_text()
                        self.assertIn(f'MPEGTS:{publisher.mpegts_origin}', data)
                        self.assertIn('00:03.000 --> 00:23.000', data)
            self.assertLess(sum(p.stat().st_size for p in root.rglob('*') if p.is_file()), 30*1024**2)


if __name__ == '__main__': unittest.main()
