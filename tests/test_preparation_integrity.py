"""Synthetic HLS failures must not become a successful, incomplete preparation."""
import http.client
import io
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import relay
import remote_media


class Response(io.BytesIO):
    def __init__(self, data, mime, status=200, expected=None):
        super().__init__(data)
        self.status = status
        self.headers = {'Content-Type': mime, 'Content-Length': str(len(data) if expected is None else expected)}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


class Connection:
    def close(self):
        pass


class PreparationIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.disk_usage('/').free <= relay.EARLY_FLOOR + 16 * 1024**2:
            raise RuntimeError('Need 16 MiB above the storage floor for synthetic HLS tests.')
        cls.fixture = tempfile.TemporaryDirectory(prefix='videobridge-integrity-fixture-')
        cls.root = Path(cls.fixture.name)
        try:
            subprocess.run([relay.executable('ffmpeg'), '-nostdin', '-v', 'error',
                            '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=5', '-t', '3',
                            '-c:v', 'libx264', '-g', '5', '-sc_threshold', '0',
                            '-f', 'hls', '-hls_time', '1', '-hls_list_size', '0',
                            str(cls.root / 'video.m3u8')], check=True, timeout=15)
            cls.media = {p.name: p.read_bytes() for p in cls.root.iterdir()}
        except BaseException:
            cls.fixture.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def exercise(self, fault=None):
        # Inspection succeeds. The chosen middle segment fails only when the real
        # FFmpeg preparation process starts, exercising the active relay loop.
        encoding = threading.Event()
        events, brokers, children, directories, fault_hits = [], [], [], [], []
        signal_handlers = {}
        real_popen = subprocess.Popen
        real_temp = tempfile.TemporaryDirectory
        real_broker = remote_media.RemoteMediaBroker

        def launch(command, *args, **kwargs):
            if '-hls_segment_filename' in command:
                encoding.set()
            child = real_popen(command, *args, **kwargs)
            children.append(child)
            return child

        def directory(*args, **kwargs):
            result = real_temp(*args, **kwargs)
            directories.append(Path(result.name))
            return result

        def broker(*args, **kwargs):
            result = real_broker(*args, **kwargs)
            brokers.append(result)
            return result

        def fetch(url, method='GET', range_header=None, referer='', redirects=5):
            name = Path(urlsplit(url).path).name
            data = self.media[name]
            mime = 'application/vnd.apple.mpegurl' if name.endswith('.m3u8') else 'video/mp2t'
            if encoding.is_set() and name == 'video1.ts' and fault:
                fault_hits.append(name)
                if fault == 'missing':
                    return Connection(), Response(b'', mime, status=404), url
                if fault == 'truncated':
                    return Connection(), Response(data[:len(data)//2], mime, expected=len(data)), url
            return Connection(), Response(data, mime), url

        def emit(kind, **fields):
            events.append(dict(event=kind, **fields))
            if kind == 'complete':
                # Stop normally after observing completion, including server cleanup.
                signal_handlers[relay.signal.SIGTERM](None, None)

        caught = None
        with patch('remote_media.fetch_public', side_effect=fetch), \
             patch.object(relay, 'RemoteMediaBroker', side_effect=broker), \
             patch.object(relay.subprocess, 'Popen', side_effect=launch), \
             patch.object(relay.tempfile, 'TemporaryDirectory', side_effect=directory), \
             patch.object(relay.signal, 'signal', side_effect=lambda sig, fn: signal_handlers.__setitem__(sig, fn)), \
             patch.object(relay, 'emit', side_effect=emit):
            try:
                relay.run({'source': 'https://media.example/video.m3u8', 'address': '127.0.0.1', 'subtitles': []})
            except ValueError as error:
                caught = str(error)
        self.assertTrue(children)
        self.assertTrue(all(child.poll() is not None for child in children), 'A media worker survived relay cleanup.')
        self.assertTrue(directories)
        self.assertTrue(all(not path.exists() for path in directories), 'Preparation cache survived relay cleanup.')
        self.assertTrue(brokers)
        self.assertTrue(all(not item.thread.is_alive() and item.fileno() == -1 for item in brokers))
        for event in events:
            if event['event'] == 'ready':
                with self.assertRaises(Exception):
                    urlopen(event['url'], timeout=1)
        kinds = [event['event'] for event in events]
        if fault:
            self.assertTrue(fault_hits, 'Failure was not injected into encoding.')
            self.assertIsNotNone(caught)
            self.assertNotIn('complete', kinds, 'Incomplete media was reported complete.')
            self.assertNotIn('https://', caught, 'Error disclosed a source URL.')
            self.assertIsNotNone(brokers[0].last_error)
        else:
            self.assertIsNone(caught)
            self.assertIn('ready', kinds)
            self.assertEqual(kinds.count('complete'), 1)
            self.assertIsNone(brokers[0].last_error)

    def test_healthy_hls_completes_and_stop_cleans_workers_cache_and_urls(self):
        self.exercise()

    def test_missing_middle_segment_never_reports_complete(self):
        self.exercise('missing')

    def test_truncated_middle_segment_never_reports_complete(self):
        self.exercise('truncated')

    def test_upstream_broken_pipe_before_headers_latches_safe_failure(self):
        broker = remote_media.RemoteMediaBroker()
        try:
            with patch('remote_media.fetch_public', side_effect=BrokenPipeError('private token')):
                with self.assertRaises(Exception) as rejected:
                    urlopen(broker.map_url('https://media.example/video.ts'), timeout=2)
                if hasattr(rejected.exception, 'close'):
                    rejected.exception.close()
            self.assertIsNotNone(broker.last_error, 'Upstream header reset was silently ignored.')
            self.assertNotIn('private token', broker.last_error)
        finally:
            broker.close()

    def test_upstream_reset_and_incomplete_read_have_safe_errors(self):
        class Broken:
            def __init__(self, error): self.error = error
            def read(self, size): raise self.error
        for error in [ConnectionResetError('private token'), BrokenPipeError('private token'), TimeoutError('private token'), http.client.IncompleteRead(b'private token')]:
            with self.subTest(error=type(error).__name__), self.assertRaisesRegex(ValueError, '^The media download was interrupted'):
                remote_media.read_remote(Broken(error), 10)


if __name__ == '__main__':
    unittest.main()
