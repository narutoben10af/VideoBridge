import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import hls_publication as pub
import relay


def manifest(count=1, complete=False, target=4):
    lines = ['#EXTM3U', '#EXT-X-VERSION:7', f'#EXT-X-TARGETDURATION:{target}',
             '#EXT-X-MEDIA-SEQUENCE:0', '#EXT-X-PLAYLIST-TYPE:EVENT', '#EXT-X-MAP:URI="init.mp4"']
    for index in range(count): lines += ['#EXTINF:4.000000,', f'segment{index:06d}.m4s']
    if complete: lines += ['#EXT-X-ENDLIST']
    return ('\n'.join(lines) + '\n').encode()


class PublicationTests(unittest.TestCase):
    def test_dependency_order_progression_and_unchanged_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'init.mp4').write_bytes(b'init')
            for i in range(2): (root/f'segment{i:06d}.m4s').write_bytes(b'media')
            (root/'sub0.vtt').write_text('WEBVTT\n\n00:03.000 --> 00:05.000\nCrossing\n')
            p = pub.HLSPublisher(root, [root/'sub0.vtt'])
            self.assertFalse(p.publish())
            private = root/'_video.m3u8'
            private.write_bytes(manifest())
            writes = []
            original = pub.atomic_write
            def record(path, data):
                name = Path(path).name
                if name == 'sub0.m3u8':
                    for line in data.decode().splitlines():
                        if line.endswith('.vtt'): self.assertTrue((root/line).exists())
                if name == 'video.m3u8': self.assertTrue((root/'sub0.m3u8').exists())
                writes.append(name); original(path, data)
            with patch.object(pub, 'atomic_write', side_effect=record):
                self.assertTrue(p.publish())
                self.assertEqual(writes, ['sub0-000000.vtt','sub0.m3u8','video.m3u8'])
                writes.clear(); self.assertFalse(p.publish()); self.assertEqual(writes, [])
                first = (root/'sub0-000000.vtt').read_bytes()
                private.write_bytes(manifest(2, True)); self.assertTrue(p.publish())
                self.assertEqual(writes, ['sub0-000001.vtt','sub0.m3u8','video.m3u8'])
                self.assertEqual(first, (root/'sub0-000000.vtt').read_bytes())
            self.assertTrue(p.snapshot.complete)
            self.assertIn(b'Crossing', (root/'sub0-000001.vtt').read_bytes())
            self.assertIn(b'#EXT-X-ENDLIST', (root/'sub0.m3u8').read_bytes())

    def test_changed_metadata_or_previous_segments_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'init.mp4').write_bytes(b'init'); (root/'segment000000.m4s').write_bytes(b'media')
            source = root/'_video.m3u8'; source.write_bytes(manifest())
            p = pub.HLSPublisher(root, []); p.publish()
            for bad in [manifest(target=5), manifest().replace(b'4.000000', b'3.000000')]:
                source.write_bytes(bad)
                with self.assertRaises(ValueError): p.publish()
                self.assertEqual((root/'video.m3u8').read_bytes(), manifest())

    def test_manifest_parser_and_unavailable_media_fail_closed(self):
        for bad in [b'bad', manifest().replace(b'segment000000.m4s', b'https://example.com/a'),
                    manifest().replace(b'EVENT', b'VOD'), manifest().replace(b':0\n', b':1\n'),
                    manifest().replace(b'#EXTINF:4.000000,', b'#EXT-X-DISCONTINUITY\n#EXTINF:4.000000,')]:
            with self.subTest(bad=bad), self.assertRaises(ValueError): pub.parse_video_manifest(bad)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'_video.m3u8').write_bytes(manifest())
            with self.assertRaises(ValueError): pub.HLSPublisher(root, []).publish()
            self.assertFalse((root/'video.m3u8').exists())

    def test_atomic_publication_checks_temporary_space_before_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'sub0.m3u8'; path.write_bytes(b'old')
            budget = type('Budget', (), {'free': pub.EARLY_FLOOR + 3})()
            with patch.object(pub.shutil, 'disk_usage', return_value=budget):
                with self.assertRaisesRegex(ValueError, 'storage safety'):
                    pub.atomic_write(path, b'new')
            self.assertEqual(path.read_bytes(), b'old')
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_atomic_replacement_during_response_keeps_length_and_body_consistent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root/'video.m3u8'; old = b'old complete snapshot'; new = b'new much longer complete snapshot'
            path.write_bytes(old)
            server = relay.MediaServer(root, 'test', '127.0.0.1')
            original = relay.MediaHandler.end_headers
            def replace_then_headers(handler):
                pub.atomic_write(path, new)
                original(handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
            try:
                with patch.object(relay.MediaHandler, 'end_headers', replace_then_headers):
                    with urlopen(f'http://127.0.0.1:{server.server_port}/test/video.m3u8') as response:
                        self.assertEqual(int(response.headers['Content-Length']), len(old))
                        self.assertEqual(response.read(), old)
                self.assertEqual(path.read_bytes(), new)
            finally: server.shutdown(); server.server_close(); worker.join()


if __name__ == '__main__': unittest.main()
