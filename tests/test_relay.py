import importlib.util, json, os, select, subprocess, sys, tempfile, threading, unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen, Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
spec = importlib.util.spec_from_file_location('relay', ROOT / 'app' / 'relay.py')
relay = importlib.util.module_from_spec(spec); spec.loader.exec_module(relay)

class RelayTests(unittest.TestCase):
    def test_reject_browser_blob_and_header_injection(self):
        with self.assertRaises(ValueError): relay.source_args('blob:https://example.com/a')
        with self.assertRaises(ValueError): relay.source_args('https://example.com/video', 'https://example.com/\r\nCookie: secret')
        with self.assertRaises(ValueError): relay.source_args('https://user:password@example.com/video')

    def test_tracks_are_soft_and_names_cannot_inject_playlist_lines(self):
        playlist = relay.master_playlist([dict(name='English"\n#BAD', language='en'), dict(name='中文', language='zh')])
        self.assertEqual(playlist.count('#EXT-X-MEDIA:'), 2)
        self.assertIn('TYPE=SUBTITLES', playlist)
        self.assertNotIn('\n#BAD', playlist)
        self.assertIn('DEFAULT=NO', playlist)
        self.assertIn('中文', playlist)

    def test_duplicate_rendition_labels_remain_unique(self):
        playlist = relay.master_playlist([dict(name='English', language='en'), dict(name='English', language='en')])
        self.assertIn('NAME="English"', playlist)
        self.assertIn('NAME="English (2)"', playlist)

    def test_video_policy_rejects_lossy_unvalidated_paths(self):
        relay.validate_video_for_prototype(dict(codec_name='h264', pix_fmt='yuv420p'))
        for metadata in [dict(codec_name='hevc', pix_fmt='yuv420p'),
                         dict(codec_name='h264', pix_fmt='yuv420p10le'),
                         dict(codec_name='h264', pix_fmt='yuv420p', color_transfer='smpte2084'),
                         dict(codec_name='h264', pix_fmt='yuv420p', side_data_list=[dict(side_data_type='DOVI configuration record')])]:
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                relay.validate_video_for_prototype(metadata)

    def test_server_is_scoped_and_supports_ranges(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'init.mp4').write_bytes(b'0123456789')
            server = relay.MediaServer(root, 'test-token', '127.0.0.1')
            worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(Request(base+'/test-token/init.mp4', headers={'Range':'bytes=2-5'})) as response:
                    self.assertEqual(response.status, 206); self.assertEqual(response.read(), b'2345')
                with urlopen(Request(base+'/test-token/init.mp4', method='HEAD')) as response:
                    self.assertEqual(response.headers['Content-Length'], '10'); self.assertEqual(response.read(), b'')
                for path in ['/wrong/init.mp4', '/test-token/../secret', '/test-token/%2e%2e/secret', '/test-token/relay.py']:
                    with self.assertRaises(HTTPError) as error: urlopen(base+path)
                    self.assertEqual(error.exception.code, 404)
                    error.exception.close()
            finally: server.shutdown(); server.server_close(); worker.join()

    def test_real_hls_and_sidecar_subtitle_preparation(self):
        # Synthetic eight-second fixture; no user media or remote content.
        with tempfile.TemporaryDirectory(prefix='videobridge-test-') as temp:
            root = Path(temp); video = root/'video.mp4'; sub = root/'English.srt'
            sub.write_text('1\n00:00:01,000 --> 00:00:03,000\nVideoBridge subtitle test\n\n2\n00:00:05,000 --> 00:00:07,000\n中文 timing test\n', encoding='utf-8')
            subprocess.run([relay.executable('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=25',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '8', '-c:v', 'libx264', '-g', '50', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', str(video)], check=True, timeout=20)
            proc = subprocess.Popen([sys.executable, '-u', str(ROOT/'app'/'relay.py')], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            request = dict(source=str(video), address='127.0.0.1', subtitles=[dict(url=str(sub), label='English + CJK', language='en')])
            proc.stdin.write((json.dumps(request)+'\n').encode()); proc.stdin.close()
            url = None; events=[]; buffer=b''; completed=False
            try:
                import time
                deadline = time.monotonic()+35
                while time.monotonic() < deadline:
                    if not select.select([proc.stdout], [], [], 1)[0]: continue
                    data=os.read(proc.stdout.fileno(), 8192)
                    if not data: break
                    buffer += data
                    while b'\n' in buffer:
                        line, buffer=buffer.split(b'\n', 1)
                        event=json.loads(line); events.append(event['event'])
                        if event['event']=='error': self.fail(event['message'])
                        if event['event']=='ready': url=event['url']
                        if event['event']=='complete': completed=True
                    if completed: break
                self.assertIsNotNone(url, events)
                self.assertTrue(completed, events)
                with urlopen(url) as response: master=response.read().decode()
                self.assertIn('TYPE=SUBTITLES', master)
                self.assertIn('SUBTITLES="subs"', master)
                with urlopen(url.replace('master.m3u8','sub0.vtt')) as response: vtt=response.read().decode()
                self.assertIn('WEBVTT', vtt); self.assertIn('00:01.000 --> 00:03.000', vtt); self.assertIn('中文', vtt)
                # Decode actual generated audio and video; manifest presence alone is insufficient.
                decode=subprocess.run([relay.executable('ffmpeg'), '-v', 'error', '-i', url.replace('master.m3u8','video.m3u8'),
                    '-t','2','-f','null','-'], capture_output=True, timeout=15)
                self.assertEqual(decode.returncode,0,decode.stderr.decode()[:400])
            finally:
                proc.terminate()
                try: proc.wait(timeout=7)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(); self.fail('Relay did not stop within 7 seconds')
                proc.stdout.close(); proc.stderr.close()
            with self.assertRaises(Exception): urlopen(url, timeout=2)

if __name__ == '__main__': unittest.main(verbosity=2)
