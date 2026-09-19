"""Publish generated video and subtitle EVENT snapshots in dependency order."""
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from subtitle_hls import MAX_INPUT_BYTES, MAX_SEGMENTS, package_webvtt

MAX_MANIFEST_BYTES = 2 * 1024 * 1024
EARLY_FLOOR = 15_728_640 * 1024


@dataclass(frozen=True)
class VideoSnapshot:
    target_duration: int
    durations: tuple[float, ...]
    names: tuple[str, ...]
    complete: bool


def parse_video_manifest(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError('Generated video playlist exceeds its size limit.')
    try:
        lines = raw.decode('ascii').splitlines()
    except UnicodeError:
        raise ValueError('Generated video playlist has invalid encoding.') from None
    if not lines or lines[0] != '#EXTM3U':
        raise ValueError('Generated video playlist has no HLS header.')
    required = {'#EXT-X-VERSION', '#EXT-X-TARGETDURATION', '#EXT-X-MEDIA-SEQUENCE', '#EXT-X-PLAYLIST-TYPE', '#EXT-X-MAP'}
    fields, durations, names = {}, [], []
    pending = None
    complete = False
    for line in lines[1:]:
        if not line:
            continue
        if complete:
            raise ValueError('Generated video playlist has data after completion.')
        tag, _, value = line.partition(':')
        if tag in required:
            if tag in fields or names or pending is not None:
                raise ValueError('Generated video playlist has duplicate or misplaced metadata.')
            fields[tag] = value
        elif line.startswith('#EXTINF:'):
            if pending is not None or not re.fullmatch(r'\d+(?:\.\d+)?,', value):
                raise ValueError('Generated video playlist has invalid segment duration.')
            pending = float(value[:-1])
            if not math.isfinite(pending) or not 0 < pending <= 86400:
                raise ValueError('Generated video playlist has invalid segment duration.')
        elif line == '#EXT-X-ENDLIST':
            if pending is not None:
                raise ValueError('Generated video playlist ends before its segment URI.')
            complete = True
        elif re.fullmatch(r'segment\d{6}\.m4s', line):
            if pending is None or line != f'segment{len(names):06d}.m4s':
                raise ValueError('Generated video segment sequence is invalid.')
            names.append(line); durations.append(pending); pending = None
            if len(names) > MAX_SEGMENTS:
                raise ValueError('Generated video playlist has too many segments.')
        else:
            raise ValueError('Generated video playlist contains unsupported metadata or paths.')
    if set(fields) != required or pending is not None or not names:
        raise ValueError('Generated video playlist is incomplete.')
    if fields['#EXT-X-VERSION'] != '7' or fields['#EXT-X-MEDIA-SEQUENCE'] != '0' or fields['#EXT-X-PLAYLIST-TYPE'] != 'EVENT' or fields['#EXT-X-MAP'] != 'URI="init.mp4"':
        raise ValueError('Generated video playlist has unsupported timeline metadata.')
    target = fields['#EXT-X-TARGETDURATION']
    if not target.isdecimal() or len(target) > 5 or not 1 <= int(target) <= 86400:
        raise ValueError('Generated video target duration is invalid.')
    if sum(durations) > 86400 or any(math.floor(value + 0.5) > int(target) for value in durations):
        raise ValueError('Generated video duration exceeds its supported bounds.')
    return VideoSnapshot(int(target), tuple(durations), tuple(names), complete)


def atomic_write(path, data):
    """Replace one owned output; callers order dependencies before manifests."""
    path = Path(path)
    if shutil.disk_usage(path.parent).free - len(data) <= EARLY_FLOOR:
        raise ValueError('Subtitle publication would cross the storage safety boundary.')
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class HLSPublisher:
    def __init__(self, root, subtitle_paths):
        self.root = Path(root)
        self.subtitle_texts = []
        for path in subtitle_paths:
            with Path(path).open('rb') as stream:
                raw = stream.read(MAX_INPUT_BYTES + 1)
            if len(raw) > MAX_INPUT_BYTES:
                raise ValueError('Prepared subtitle input exceeds its size limit.')
            self.subtitle_texts.append(raw.decode('utf-8-sig'))
        self.snapshot = None
        self.raw = None

    def publish(self):
        private = self.root / '_video.m3u8'
        try:
            with private.open('rb') as stream:
                raw = stream.read(MAX_MANIFEST_BYTES + 1)
        except FileNotFoundError:
            return False
        if raw == self.raw:
            return False
        current = parse_video_manifest(raw)
        old = self.snapshot
        if old and (old.complete or current.target_duration != old.target_duration or
                    current.names[:len(old.names)] != old.names or
                    current.durations[:len(old.durations)] != old.durations):
            raise ValueError('Generated EVENT playlist changed previously published media.')
        for name in ('init.mp4', *current.names):
            path = self.root / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
                raise ValueError('Generated video refers to an unavailable media file.')
        playlists = []
        for index, text in enumerate(self.subtitle_texts):
            package = package_webvtt(text, list(current.durations), current.target_duration,
                                     current.complete, prefix=f'sub{index}')
            for name, data in package.segments.items():
                path = self.root / name
                if path.exists():
                    if path.read_bytes() != data:
                        raise ValueError('A published subtitle segment changed unexpectedly.')
                else:
                    atomic_write(path, data)
            playlists.append((package.playlist_name, package.playlist))
        # All referenced segment files exist before any rendition advertises them.
        for name, data in playlists:
            atomic_write(self.root / name, data)
        atomic_write(self.root / 'video.m3u8', raw)
        self.snapshot, self.raw = current, raw
        return True
