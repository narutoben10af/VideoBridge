import sys, unittest
import http.server, io, shutil, subprocess, threading
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from remote_media import validate_url, public_addresses, rewrite_hls, RemoteMediaBroker
from urllib.request import urlopen
from urllib.error import HTTPError

class RemoteMediaTests(unittest.TestCase):
    def test_url_policy(self):
        self.assertEqual(validate_url('https://cdn.example/video?q=a#fragment'), 'https://cdn.example/video?q=a')
        for url in ['file:///etc/passwd','http://cdn.example/a','https://user:pass@cdn.example/a','https://cdn.example:8443/a','https://cdn.example/\r\nCookie:x']:
            with self.subTest(url=url),self.assertRaises(ValueError): validate_url(url)

    def test_dns_public_only(self):
        def resolver(addresses): return lambda *args,**kw:[(None,None,None,None,(ip,443)) for ip in addresses]
        self.assertEqual(public_addresses('cdn.example',resolver(['8.8.8.8'])), ['8.8.8.8'])
        for addresses in [['127.0.0.1'], ['10.0.0.1'], ['169.254.169.254'], ['::1'], ['8.8.8.8','192.168.1.1']]:
            with self.subTest(addresses=addresses),self.assertRaises(ValueError): public_addresses('x',resolver(addresses))

    def test_all_hls_references_are_rewritten(self):
        seen=[]
        def mapper(url): validate_url(url); seen.append(url); return 'http://127.0.0.1/session/'+str(len(seen))
        source='#EXTM3U\n#EXT-X-MEDIA:TYPE=SUBTITLES,URI="subs/en.m3u8"\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXT-X-MAP:URI="init.mp4"\nsegment.m4s\n'
        result=rewrite_hls(source,'https://cdn.example/base/master.m3u8',mapper)
        self.assertEqual(len(seen),4)
        self.assertEqual(seen[0],'https://cdn.example/base/subs/en.m3u8')
        self.assertNotIn('https://cdn.example',result)
        with self.assertRaises(ValueError): rewrite_hls('#EXTM3U\nfile:///etc/passwd','https://cdn.example/a',mapper)
        with self.assertRaises(ValueError): rewrite_hls('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="x"','https://cdn.example/a',mapper)

    def test_hls_attribute_parser_rejects_equivalent_bypasses(self):
        cases = [
            '#EXT-X-KEY:METHOD=AES-128,URI=http://127.0.0.1/key',
            '#EXT-X-KEY:METHOD=AES-128,URI=\'https://media.example/key\'',
            '#EXT-X-KEY:METHOD=AES-128, URI="https://media.example/key"',
            '#EXT-X-KEY:METHOD=AES-128,URI="good.key",URI="bad.key"',
            '#EXT-X-KEY:METHOD=AES-128,URI="good.key",',
            '#EXT-X-KEY:METHOD=AES-128,URI="unterminated',
            '#EXT-X-KEY:METHOD=AES-128,URI="good.key"BAD=1',
            '#EXT-X-KEY:METHOD=AES-128-OTHER,URI="good.key"',
            '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="good.key",NAME="METHOD=AES-128"',
            '#EXT-X-KEY:METHOD=AES-128,URI="good.key",KEYFORMAT="other",NAME="KEYFORMAT=identity"',
            '#EXT-X-KEY:METHOD=NONE,URI="bad.key"',
            '#EXT-X-MAP:URI=https://media.example/init.mp4',
            '#EXT-X-MEDIA:TYPE=SUBTITLES,URI=https://media.example/subs.m3u8',
            '#EXT-X-SESSION-KEY:METHOD=AES-128,URI=https://media.example/key',
            '#EXT-X-CONTENT-STEERING:SERVER-URI="https://media.example/steering"',
            '#EXT-X-DEFINE:NAME="url",VALUE="https://media.example/key"',
            '#EXT-X-DATERANGE:ID="x",X-ASSET-URI="https://media.example/other"',
        ]
        for line in cases:
            with self.subTest(line=line), self.assertRaises(ValueError):
                rewrite_hls('#EXTM3U\n' + line + '\n', 'https://media.example/master.m3u8', lambda url: 'http://127.0.0.1/mapped')

    def test_valid_hls_attribute_values_preserved(self):
        source = ('#EXTM3U\n#EXT-X-ALLOW-CACHE:YES\n#EXT-X-STREAM-INF:BANDWIDTH=1000,CODECS="avc1.64001f,mp4a.40.2"\nmedia.m3u8\n'
                  '#EXT-X-MEDIA:TYPE=SUBTITLES,NAME="English, CC",URI="en.m3u8"\n'
                  '#EXT-X-KEY:METHOD=AES-128,URI="key",KEYFORMAT="identity"\n#EXT-X-KEY:METHOD=NONE\n')
        result = rewrite_hls(source, 'https://media.example/master.m3u8', lambda url: 'http://127.0.0.1/mapped')
        self.assertIn('#EXT-X-ALLOW-CACHE:YES', result)
        self.assertIn('CODECS="avc1.64001f,mp4a.40.2"', result)
        self.assertIn('NAME="English, CC"', result)
        self.assertIn('#EXT-X-KEY:METHOD=NONE', result)
        self.assertEqual(result.count('http://127.0.0.1/mapped'), 3)

    def test_real_ffprobe_reads_legitimate_brokered_hls(self):
        def binary(name):
            return next((path for path in ['/opt/homebrew/bin/' + name, '/usr/local/bin/' + name, shutil.which(name)] if path and Path(path).is_file()), None)
        ffmpeg, ffprobe = binary('ffmpeg'), binary('ffprobe')
        self.assertTrue(ffmpeg and ffprobe, 'FFmpeg tools are required for the compatibility regression.')
        media = subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                                '-t', '0.25', '-c:a', 'aac', '-f', 'mpegts', 'pipe:1'],
                               capture_output=True, check=True, timeout=10).stdout
        playlist = b'#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:0.25,\nsegment.ts\n#EXT-X-ENDLIST\n'
        fetched = []
        class Response(io.BytesIO):
            status = 200
            def __init__(self, body, mime): super().__init__(body); self.mime = mime
            def getheader(self, name, default=None): return self.mime if name == 'Content-Type' else default
        class Connection:
            def close(self): pass
        def fixture_fetch(url, *args):
            fetched.append(url)
            return Connection(), Response(playlist if url.endswith('.m3u8') else media,
                                          'application/vnd.apple.mpegurl' if url.endswith('.m3u8') else 'video/mp2t'), url
        broker = RemoteMediaBroker()
        try:
            with patch('remote_media.fetch_public', side_effect=fixture_fetch):
                result = subprocess.run([ffprobe, '-v', 'error', '-protocol_whitelist', 'http,https,tcp,tls,crypto',
                                         '-show_entries', 'stream=codec_name', '-of', 'json', '-i',
                                         broker.map_url('https://media.example/master.m3u8')],
                                        capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertIn(b'aac', result.stdout)
            self.assertIn('https://media.example/segment.ts', fetched)
            self.assertIsNone(broker.last_error)
        finally: broker.close()

    def test_unquoted_key_cannot_escape_broker_in_real_ffprobe(self):
        executable = next((path for path in ['/opt/homebrew/bin/ffprobe', '/usr/local/bin/ffprobe', shutil.which('ffprobe')] if path and Path(path).is_file()), None)
        self.assertIsNotNone(executable, 'FFprobe is required for the network-boundary regression.')
        hits = []
        class Trap(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                hits.append(self.path)
                body = b'0123456789abcdef'
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        trap = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Trap)
        thread = threading.Thread(target=trap.serve_forever, daemon=True); thread.start()
        broker = RemoteMediaBroker()
        class Response(io.BytesIO):
            status = 200
            def getheader(self, name, default=None):
                return 'application/vnd.apple.mpegurl' if name == 'Content-Type' else default
        class Connection:
            def close(self): pass
        source = ('#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-KEY:METHOD=AES-128,URI=http://127.0.0.1:'
                  + str(trap.server_port) + '/private.key\n#EXTINF:4,\nhttps://media.example/segment.ts\n#EXT-X-ENDLIST\n')
        def fixture_fetch(url, *args):
            return Connection(), Response(source.encode() if url.endswith('.m3u8') else b'x' * 188), url
        try:
            with patch('remote_media.fetch_public', side_effect=fixture_fetch):
                result = subprocess.run([executable, '-v', 'error', '-rw_timeout', '2000000',
                                         '-protocol_whitelist', 'http,https,tcp,tls,crypto', '-i',
                                         broker.map_url('https://media.example/master.m3u8')],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(hits, [], 'FFprobe directly fetched an unbrokered local URL.')
            self.assertIn('attribute', broker.last_error.lower())
        finally:
            broker.close(); trap.shutdown(); trap.server_close(); thread.join(timeout=2)

    def test_broker_does_not_accept_arbitrary_proxy_urls(self):
        broker=RemoteMediaBroker()
        try:
            generated=broker.map_url('https://cdn.example/video')
            self.assertEqual(generated,broker.map_url('https://cdn.example/video'))
            with self.assertRaises(HTTPError) as error: urlopen(f'http://127.0.0.1:{broker.server_port}/https://example.com')
            self.assertEqual(error.exception.code,404);error.exception.close()
        finally: broker.close()

if __name__=='__main__': unittest.main()
