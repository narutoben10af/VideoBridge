#!/usr/bin/env python3
"""Task-local HLS preparation. stdin: one JSON request. stdout: JSON status events.
Only generated media is served, behind an unguessable per-session URL.
"""
import functools, http.server, json, math, os, re, secrets, shutil, signal, socket
import subprocess, sys, tempfile, threading, time
from pathlib import Path
from urllib.parse import urlsplit
from process_worker import run_cancellable
from remote_media import RemoteMediaBroker

EARLY_FLOOR = 15_728_640 * 1024
MAX_SESSION = 8 * 1024**3
TEXT_CODECS = {'subrip', 'srt', 'webvtt', 'ass', 'ssa', 'mov_text', 'text'}

def emit(kind, **fields):
    print(json.dumps(dict(event=kind, **fields)), flush=True)

def executable(name):
    choices = [f'/opt/homebrew/bin/{name}', f'/usr/local/bin/{name}', shutil.which(name)]
    for p in choices:
        if p and os.access(p, os.X_OK): return p
    raise ValueError(f'{name} is required. Install FFmpeg before preparing video.')

def source_args(source, referer=''):
    if source.startswith(('http://', 'https://')):
        p = urlsplit(source)
        if not p.hostname or p.username or p.password: raise ValueError('Invalid media URL.')
        args = ['-rw_timeout', '15000000', '-protocol_whitelist', 'http,https,tcp,tls,crypto']
        if referer:
            if '\r' in referer or '\n' in referer or not referer.startswith(('http://', 'https://')):
                raise ValueError('Invalid referring page.')
            args += ['-headers', f'Referer: {referer}\r\n']
        return args + ['-i', source]
    p = Path(source)
    if not p.is_absolute() or not p.is_file(): raise ValueError('Choose an existing local file or an HTTP(S) media URL.')
    return ['-i', str(p)]

def probe(source, referer='', stopping=None, parent_pid=None):
    stopping = stopping or threading.Event()
    parent_pid = parent_pid if parent_pid is not None else os.getppid()
    cmd = [executable('ffprobe'), '-v', 'error', *source_args(source, referer),
           '-show_streams', '-show_format', '-of', 'json']
    try:
        result = run_cancellable(cmd, stopping, parent_pid, timeout=35)
    except subprocess.TimeoutExpired:
        raise ValueError('Media inspection timed out. The server may require a browser session.')
    if result.returncode: raise ValueError('Cannot open this media source. It may have expired, require login, or be protected.')
    return json.loads(result.stdout)

def quote_attr(s):
    return str(s).replace('"', "'").replace('\r', ' ').replace('\n', ' ')[:100]

def subtitle_playlist(duration, file):
    return f'#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:{math.ceil(duration)}\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:{duration:.3f},\n{file}\n#EXT-X-ENDLIST\n'

def master_playlist(tracks):
    text = '#EXTM3U\n#EXT-X-VERSION:7\n'
    used = set()
    for i, track in enumerate(tracks):
        base = quote_attr(track['name']) or 'Subtitles'
        name = base; suffix = 2
        while name in used:
            name = base[:90] + f' ({suffix})'; suffix += 1
        used.add(name)
        text += (f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="{name}",'
                 f'LANGUAGE="{quote_attr(track["language"])}",AUTOSELECT=YES,DEFAULT={"YES" if i == 0 else "NO"},'
                 f'URI="sub{i}.m3u8"\n')
    text += '#EXT-X-STREAM-INF:BANDWIDTH=12000000' + (',SUBTITLES="subs"' if tracks else '') + '\nvideo.m3u8\n'
    return text

class MediaHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # Do not persist media tokens, paths or browsing history.
    def do_HEAD(self): self.serve(False)
    def do_GET(self): self.serve(True)
    def serve(self, body):
        path = urlsplit(self.path).path
        prefix = '/' + self.server.token + '/'
        if not path.startswith(prefix): self.send_error(404); return
        name = path[len(prefix):]
        if not re.fullmatch(r'(master|video|sub\d+)\.m3u8|sub\d+\.vtt|init\.mp4|segment\d+\.m4s', name):
            self.send_error(404); return
        file = self.server.root / name
        try: size = file.stat().st_size
        except OSError: self.send_error(404); return
        start, end = 0, size - 1
        header = self.headers.get('Range')
        if header:
            m = re.fullmatch(r'bytes=(\d+)-(\d*)', header)
            if not m: self.send_error(416); return
            start, end = int(m[1]), min(int(m[2]) if m[2] else end, end)
            if start > end: self.send_error(416); return
        self.send_response(206 if header else 200)
        self.send_header('Content-Type', {'.m3u8':'application/vnd.apple.mpegurl','.vtt':'text/vtt','.mp4':'video/mp4','.m4s':'video/iso.segment'}[file.suffix])
        self.send_header('Content-Length', str(max(0, end-start+1)))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-store' if file.suffix == '.m3u8' else 'private, max-age=60')
        if header: self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if body:
            try:
                with file.open('rb') as f:
                    f.seek(start); remaining = end-start+1
                    while remaining > 0:
                        data = f.read(min(128*1024, remaining))
                        if not data: break
                        self.wfile.write(data); remaining -= len(data)
            except (BrokenPipeError, ConnectionResetError): pass

class MediaServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, root, token, host='0.0.0.0'):
        self.root, self.token = Path(root), token
        super().__init__((host, 0), MediaHandler)

def lan_address():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(('192.0.2.1', 9))  # Route lookup; sends no packet.
        return s.getsockname()[0]

def validate_video_for_prototype(video):
    # M0 proved this path only. Do not silently tone-map or change video quality.
    hdr_side_data = any(x.get('side_data_type') in {'Mastering display metadata', 'Content light level metadata', 'DOVI configuration record'} for x in video.get('side_data_list', []))
    if video.get('color_transfer') in {'smpte2084', 'arib-std-b67'} or hdr_side_data:
        raise ValueError('HDR video is not supported by this prototype; no conversion was started.')
    if video.get('codec_name') != 'h264' or video.get('pix_fmt') not in {'yuv420p', 'yuvj420p'}:
        raise ValueError('This prototype supports 8-bit H.264 SDR video only. Other codecs need a validated conversion path; no conversion was started.')

def prepare(request, root, stopping, parent_pid):
    source = request.get('source', '')
    if not isinstance(source, str) or len(source) > 16000: raise ValueError('Invalid source.')
    referer = request.get('referer', '')
    ffmpeg = executable('ffmpeg')
    emit('status', message='Inspecting video and subtitle tracks…')
    info = probe(source, referer, stopping, parent_pid)
    streams = info.get('streams', [])
    video = next((x for x in streams if x.get('codec_type') == 'video' and not x.get('disposition', {}).get('attached_pic')), None)
    if video is None: raise ValueError('This source has no video track.')
    validate_video_for_prototype(video)
    duration = float(info.get('format', {}).get('duration', 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('This prototype requires a finite-duration video. Live streams are not supported yet.')
    bitrate = max(float(info.get('format', {}).get('bit_rate', 0)), 8_000_000)
    estimate = int(duration * bitrate / 8 * 1.25)
    if estimate > MAX_SESSION: raise ValueError('Estimated preparation exceeds the 8 GiB session limit.')
    if shutil.disk_usage(root).free - estimate <= EARLY_FLOOR:
        raise ValueError('Preparing this video could cross the 15 GiB free-space boundary.')
    tracks = []
    embedded = [x for x in streams if x.get('codec_type') == 'subtitle']
    supplied = request.get('subtitles', [])
    if not isinstance(supplied, list) or len(supplied) > 16: raise ValueError('Too many subtitle tracks (maximum 16).')
    if len(embedded) + len(supplied) > 16: raise ValueError('Too many subtitle tracks (maximum 16).')
    for stream in embedded:
        codec = stream.get('codec_name')
        if codec not in TEXT_CODECS:
            raise ValueError(f'Embedded {codec} subtitles need an image-subtitle/burn-in path, which is not implemented. Subtitles were not discarded.')
        if codec in {'ass', 'ssa'}:
            emit('warning', message='ASS/SSA subtitles retain text and timing; complex styling and positioning are simplified to WebVTT.')
        tags = stream.get('tags', {})
        tracks.append(dict(name=tags.get('title', tags.get('language', 'Subtitles')), language=tags.get('language', 'und'),
                           input=source_args(source, referer), map=f'0:{stream["index"]}'))
    for track in supplied:
        if not isinstance(track, dict): raise ValueError('Invalid subtitle track.')
        subsource = track.get('url', '')
        if not isinstance(subsource, str): raise ValueError('Invalid subtitle source.')
        if subsource.lower().split('?')[0].endswith(('.ass', '.ssa')):
            emit('warning', message='ASS/SSA subtitles retain text and timing with simplified styling.')
        tracks.append(dict(name=track.get('label', 'Subtitles'), language=track.get('language') or 'und',
                           input=source_args(subsource, referer if subsource.startswith('http') else ''), map='0:s:0'))
    for i, track in enumerate(tracks):
        if stopping.is_set(): raise InterruptedError()
        emit('status', message=f'Preparing selectable subtitles {i+1}/{len(tracks)}…')
        try:
            result = run_cancellable([ffmpeg, '-nostdin', '-v', 'error', *track['input'], '-map', track['map'],
                '-c:s', 'webvtt', '-y', str(root / f'sub{i}.vtt')], stopping, parent_pid, timeout=90, capture_output=False)
        except subprocess.TimeoutExpired:
            raise ValueError('Subtitle preparation timed out. No silent fallback to video without subtitles.')
        if result.returncode: raise ValueError('A subtitle track could not be prepared. No silent fallback to video without subtitles.')
        (root / f'sub{i}.m3u8').write_text(subtitle_playlist(duration, f'sub{i}.vtt'))
    (root / 'master.m3u8').write_text(master_playlist(tracks))
    video_args = ['-c:v', 'copy']
    cmd = [ffmpeg, '-nostdin', '-v', 'error', *source_args(source, referer), '-map', f'0:{video["index"]}',
           '-map', '0:a:0?', *video_args, '-c:a', 'aac', '-b:a', '192k', '-ac', '2', '-sn',
           '-f', 'hls', '-hls_time', '4', '-hls_playlist_type', 'event', '-hls_segment_type', 'fmp4',
           '-hls_flags', 'temp_file', '-hls_fmp4_init_filename', 'init.mp4',
           '-hls_segment_filename', str(root / 'segment%06d.m4s'), str(root / 'video.m3u8')]
    if stopping.is_set() or os.getppid() != parent_pid: raise InterruptedError()
    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return process, duration, tracks

def run(request):
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    process = server = broker = None
    parent = os.getppid()
    # Session owns this directory exclusively; cleanup never touches source media.
    with tempfile.TemporaryDirectory(prefix='videobridge-') as directory:
        root = Path(directory)
        try:
            request = dict(request)
            if request.get('source', '').startswith(('http://', 'https://')) or any(x.get('url', '').startswith(('http://', 'https://')) for x in request.get('subtitles', [])):
                broker = RemoteMediaBroker(request.get('referer', ''))
                if request.get('source', '').startswith(('http://', 'https://')):
                    request['source'] = broker.map_url(request['source'])
                request['subtitles'] = [dict(x, url=broker.map_url(x['url'])) if x.get('url', '').startswith(('http://', 'https://')) else dict(x) for x in request.get('subtitles', [])]
                request['referer'] = ''
            try:
                process, duration, tracks = prepare(request, root, stopping, parent)
            except ValueError:
                if broker and broker.last_error: raise ValueError(broker.last_error)
                raise
            ready = False; started = time.monotonic(); complete = False
            while not stopping.wait(0.5):
                if os.getppid() != parent: break
                size = sum(p.stat().st_size for p in root.iterdir() if p.is_file())
                if size > MAX_SESSION or shutil.disk_usage(root).free <= EARLY_FLOOR:
                    raise ValueError('Preparation stopped at the storage safety boundary.')
                code = process.poll()
                if code not in (None, 0): raise ValueError('Video preparation failed. The stream may have expired or use an unsupported codec.')
                if not ready and (root / 'video.m3u8').exists():
                    host = request.get('address') or lan_address()
                    socket.inet_aton(host)
                    server = MediaServer(root, secrets.token_urlsafe(24), host)
                    threading.Thread(target=server.serve_forever, daemon=True).start()
                    emit('ready', url=f'http://{host}:{server.server_port}/{server.token}/master.m3u8', duration=duration,
                         subtitles=[dict(label=t['name'], language=t['language']) for t in tracks])
                    ready = True
                if code == 0 and not complete:
                    if not ready: raise ValueError('No playable media was generated.')
                    emit('complete', message='Video is fully prepared; seeking is available throughout.')
                    complete = True
                if not ready and time.monotonic()-started > 120: raise ValueError('No playable segment after two minutes.')
        finally:
            if process and process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
            if server: server.shutdown(); server.server_close()
            if broker: broker.close()

if __name__ == '__main__':
    try:
        raw = sys.stdin.buffer.readline(64*1024+1)
        if len(raw) > 64*1024: raise ValueError('Request is too large.')
        run(json.loads(raw))
    except InterruptedError: pass
    except Exception as e:
        emit('error', message=str(e) if isinstance(e, ValueError) else 'Preparation failed. Check the media source and local network.')
        sys.exit(1)
