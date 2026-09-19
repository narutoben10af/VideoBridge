"""Session-only HTTPS media broker. Resolves/pins public IPs and rewrites HLS URLs.
No cookies, authentication headers, general proxy endpoint, or persistent URL logs.
"""
import http.client
import http.server
import ipaddress
import os
import re
import secrets
import socket
import ssl
import threading
from urllib.parse import urlsplit, urlunsplit, urljoin

MAX_MANIFEST = 2 * 1024 * 1024


def validate_url(value):
    if not isinstance(value, str) or len(value) > 16000 or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid remote media URL.')
    parts = urlsplit(value)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError('Browser media must use HTTPS on the standard port without embedded credentials.')
    if parts.fragment:
        parts = parts._replace(fragment='')
    return urlunsplit(parts)


def public_addresses(host, resolver=socket.getaddrinfo):
    results = resolver(host, 443, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(row[4][0] for row in results))
    if not addresses or any(not ipaddress.ip_address(x.split('%')[0]).is_global for x in addresses):
        raise ValueError('Remote media cannot access local, private or reserved network addresses.')
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        # Use the OS CA bundle when the selected Python has no configured trust roots.
        context = ssl.create_default_context(cafile='/etc/ssl/cert.pem' if os.path.isfile('/etc/ssl/cert.pem') else None)
        super().__init__(host, 443, timeout=15, context=context)
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def fetch_public(url, method='GET', range_header=None, referer='', redirects=5):
    for _ in range(redirects + 1):
        url = validate_url(url)
        parts = urlsplit(url)
        address = public_addresses(parts.hostname)[0]
        connection = PinnedHTTPS(parts.hostname, address)
        headers = {'User-Agent': 'Mozilla/5.0 VideoBridge/0.2', 'Accept-Encoding': 'identity'}
        if range_header:
            if not re.fullmatch(r'bytes=(?:\d+-\d*|-\d+)', range_header):
                raise ValueError('Invalid byte range.')
            headers['Range'] = range_header
        if referer:
            r = urlsplit(validate_url(referer))
            # Cross-origin requests receive only the page origin, without query tokens.
            headers['Referer'] = urlunsplit(r._replace(fragment='')) if r.netloc == parts.netloc else f'{r.scheme}://{r.netloc}/'
        path = urlunsplit(('', '', parts.path or '/', parts.query, ''))
        try:
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
        except BaseException:
            connection.close()
            raise
        if response.status in (301, 302, 303, 307, 308):
            location = response.getheader('Location')
            response.close(); connection.close()
            if not location: raise ValueError('Media redirect has no target.')
            url = validate_url(urljoin(url, location))
            continue
        return connection, response, url
    raise ValueError('Too many media redirects.')


# Allow only playlist syntax whose reference semantics we rewrite explicitly.
# Unknown EXT tags fail closed rather than introducing a new FFmpeg URL sink.
ATTRIBUTE_TAGS = {
    '#EXT-X-KEY', '#EXT-X-SESSION-KEY', '#EXT-X-MAP', '#EXT-X-MEDIA',
    '#EXT-X-I-FRAME-STREAM-INF', '#EXT-X-SESSION-DATA', '#EXT-X-STREAM-INF',
    '#EXT-X-START', '#EXT-X-DATERANGE', '#EXT-X-SERVER-CONTROL',
    '#EXT-X-PART-INF', '#EXT-X-PART', '#EXT-X-PRELOAD-HINT',
    '#EXT-X-RENDITION-REPORT', '#EXT-X-SKIP',
}
SCALAR_TAGS = {
    '#EXTM3U', '#EXT-X-VERSION', '#EXT-X-TARGETDURATION', '#EXT-X-MEDIA-SEQUENCE',
    '#EXT-X-DISCONTINUITY-SEQUENCE', '#EXT-X-ENDLIST', '#EXT-X-PLAYLIST-TYPE',
    '#EXT-X-I-FRAMES-ONLY', '#EXT-X-INDEPENDENT-SEGMENTS', '#EXT-X-DISCONTINUITY',
    '#EXTINF', '#EXT-X-BYTERANGE', '#EXT-X-PROGRAM-DATE-TIME', '#EXT-X-BITRATE', '#EXT-X-GAP',
    '#EXT-X-ALLOW-CACHE',  # Legacy non-network scalar accepted by FFmpeg.
}
URI_TAGS = {
    '#EXT-X-KEY', '#EXT-X-SESSION-KEY', '#EXT-X-MAP', '#EXT-X-MEDIA',
    '#EXT-X-I-FRAME-STREAM-INF', '#EXT-X-SESSION-DATA', '#EXT-X-PART',
    '#EXT-X-PRELOAD-HINT', '#EXT-X-RENDITION-REPORT',
}


def parse_hls_attributes(value):
    """RFC 8216 attribute list, without tolerant FFmpeg reinterpretation."""
    attributes = {}
    offset = 0
    while offset < len(value):
        match = re.match(r'([A-Z0-9-]+)=("[^"\r\n]*"|[^,\s"]+)(,|$)', value[offset:])
        if not match:
            raise ValueError('Malformed HLS attribute list.')
        name, raw, separator = match.groups()
        if name in attributes:
            raise ValueError('Duplicate HLS attribute.')
        quoted = raw.startswith('"')
        attributes[name] = (raw[1:-1] if quoted else raw, quoted)
        offset += match.end()
        if separator and offset == len(value):
            raise ValueError('Malformed HLS attribute list.')
    if not attributes:
        raise ValueError('Missing HLS attribute list.')
    return attributes


def rewrite_hls(text, base, map_url):
    # Normalize only legal line endings; do not give FFmpeg an alternate parse.
    if any((ord(c) < 32 and c not in '\r\n') or c in '\x85\u2028\u2029' for c in text):
        raise ValueError('Unsupported HLS control character.')
    text = text.removeprefix('\ufeff')
    if not text.startswith('#EXTM3U\n') and not text.startswith('#EXTM3U\r\n'):
        raise ValueError('Not a supported HLS playlist.')
    lines = []
    for line in text.splitlines():
        if not line:
            lines.append(line)
            continue
        if not line.startswith('#'):
            lines.append(map_url(urljoin(base, line.strip())))
            continue
        tag, separator, value = line.partition(':')
        if tag in ATTRIBUTE_TAGS:
            attributes = parse_hls_attributes(value)
            if tag in {'#EXT-X-KEY', '#EXT-X-SESSION-KEY'}:
                method = attributes.get('METHOD')
                if method not in {('NONE', False), ('AES-128', False)}:
                    raise ValueError('Protected media is not supported.')
                if 'KEYFORMAT' in attributes and attributes['KEYFORMAT'] != ('identity', True):
                    raise ValueError('Protected media is not supported.')
                if method == ('NONE', False) and set(attributes) != {'METHOD'}:
                    raise ValueError('Malformed HLS encryption attributes.')
                if method == ('AES-128', False) and 'URI' not in attributes:
                    raise ValueError('Missing HLS key URI attribute.')
            if tag in {'#EXT-X-MAP', '#EXT-X-I-FRAME-STREAM-INF', '#EXT-X-PART',
                       '#EXT-X-PRELOAD-HINT', '#EXT-X-RENDITION-REPORT'} and 'URI' not in attributes:
                raise ValueError('Missing HLS URI attribute.')
            for name, (content, quoted) in list(attributes.items()):
                if name == 'URI':
                    if tag not in URI_TAGS or not quoted or not content:
                        raise ValueError('Malformed or unsupported HLS URI attribute.')
                    attributes[name] = (map_url(urljoin(base, content)), True)
                elif name.endswith('-URI') or name.endswith('-URL'):
                    raise ValueError('Unsupported HLS external-reference attribute.')
            line = tag + ':' + ','.join(name + '=' + ('"' + content + '"' if quoted else content)
                                      for name, (content, quoted) in attributes.items())
        elif tag.startswith('#EXT') and tag not in SCALAR_TAGS:
            raise ValueError('Unsupported HLS extension tag.')
        lines.append(line)
    return '\n'.join(lines) + '\n'


def read_remote(response, size):
    try:
        return response.read(size)
    except (OSError, http.client.IncompleteRead):
        # Upstream failure must not be confused with a cancelled local consumer.
        raise ValueError('The media download was interrupted. Send the video again.') from None


class BrokerHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_HEAD(self): self.serve('HEAD')
    def do_GET(self): self.serve('GET')
    def serve(self, method):
        connection = response = None
        started = False
        try:
            source = self.server.sources.get(self.path)
            if not source:
                self.send_error(404); return
            try:
                connection, response, final_url = fetch_public(source, method, self.headers.get('Range'), self.server.referer)
            except OSError:
                raise ValueError('The media connection was interrupted. Send the video again.') from None
            if response.status not in (200, 206):
                self.server.last_error = f'The media server refused this request (HTTP {response.status}). Refresh the video page and send it again.'
                self.send_error(502, 'Media server did not provide the requested content'); return
            content_type = response.getheader('Content-Type', '').split(';')[0].lower()
            # GET prefix inspection prevents HTML/DASH from being fed to a demuxer.
            prefix = read_remote(response, 4096) if method == 'GET' else b''
            stripped = prefix.lstrip(b'\xef\xbb\xbf\r\n\t ')
            hls = stripped.startswith(b'#EXTM3U') or content_type in {'application/vnd.apple.mpegurl', 'application/x-mpegurl', 'audio/mpegurl'}
            if content_type in {'application/dash+xml', 'text/html'} or stripped.startswith((b'<', b'<!')):
                raise ValueError('This media response is not a supported video stream.')
            expected = response.getheader('Content-Length')
            expected = int(expected) if expected and expected.isdecimal() else None
            received = len(prefix)
            body = None
            if hls and method == 'GET':
                body = prefix + read_remote(response, MAX_MANIFEST + 1)
                if len(body) > MAX_MANIFEST: raise ValueError('HLS playlist is too large.')
                if expected is not None and len(body) != expected:
                    raise ValueError('The media playlist download was incomplete. Send the video again.')
                body = rewrite_hls(body.decode('utf-8-sig'), final_url, self.server.map_url).encode('utf-8')
            self.send_response(response.status)
            self.send_header('Content-Type', 'application/vnd.apple.mpegurl' if hls else content_type or 'application/octet-stream')
            self.send_header('Cache-Control', 'no-store')
            if body is not None:
                self.send_header('Content-Length', str(len(body)))
            else:
                for name in ('Content-Length', 'Content-Range', 'Accept-Ranges'):
                    value = response.getheader(name)
                    if value: self.send_header(name, value)
            self.end_headers(); started = True
            if method == 'GET':
                if body is not None: self.wfile.write(body)
                else:
                    self.wfile.write(prefix)
                    while not self.server.stopping.is_set():
                        chunk = read_remote(response, 128 * 1024)
                        if not chunk: break
                        received += len(chunk)
                        self.wfile.write(chunk)
                    if not self.server.stopping.is_set() and expected is not None and received != expected:
                        raise ValueError('The media download was incomplete. Send the video again.')
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception as error:
            self.server.last_error = str(error) if isinstance(error, ValueError) else 'The media server connection failed. Refresh the video page and send it again.'
            if not started: self.send_error(502, 'Media source is unavailable or unsupported')
        finally:
            if response: response.close()
            if connection: connection.close()


class RemoteMediaBroker(http.server.ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, referer=''):
        self.sources = {}; self.reverse = {}; self.lock = threading.Lock(); self.last_error = None
        self.token = secrets.token_urlsafe(24); self.stopping = threading.Event()
        self.referer = validate_url(referer) if referer else ''
        super().__init__(('127.0.0.1', 0), BrokerHandler)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def map_url(self, url):
        url = validate_url(url)
        with self.lock:
            path = self.reverse.get(url)
            if not path:
                if len(self.sources) >= 10000: raise ValueError('This media playlist exceeds the session reference limit.')
                suffix = urlsplit(url).path.rsplit('.', 1)[-1].lower()
                suffix = suffix if suffix in {'m3u8', 'mp4', 'm4s', 'ts', 'key', 'vtt', 'srt'} else 'ts'
                path = '/' + self.token + '/' + secrets.token_urlsafe(12) + '.' + suffix
                self.sources[path] = url; self.reverse[url] = path
        return f'http://127.0.0.1:{self.server_port}{path}'

    def close(self):
        self.stopping.set(); self.shutdown(); self.server_close(); self.thread.join(timeout=2)
        self.sources.clear(); self.reverse.clear()
