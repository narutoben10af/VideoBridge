"""Pure, bounded WebVTT rendition packaging for a zero-based HLS timeline.

Caller supplies trusted video segment durations and publishes returned segments
before atomically replacing the subtitle playlist. No URL or filesystem access.
"""
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import math
import re

MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_CUES = 100_000
MAX_SEGMENTS = 25_000
MAX_REFERENCES = 250_000
TIMESTAMP = r'(?:\d{2,}:)?\d{2}:\d{2}\.\d{3}'
TIMING = re.compile(r'^(' + TIMESTAMP + r')[ \t]+-->[ \t]+(' + TIMESTAMP + r')([ \t]+[^\r\n]*)?$')


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    block: str


@dataclass(frozen=True)
class SubtitlePackage:
    playlist_name: str
    playlist: bytes
    segments: dict[str, bytes]


def timestamp(value):
    parts = value.split(':')
    seconds = float(parts[-1])
    minutes = int(parts[-2])
    if len(parts) == 3 and len(parts[0]) > 6:
        raise ValueError('WebVTT timestamp exceeds the supported finite timeline.')
    hours = int(parts[0]) if len(parts) == 3 else 0
    if hours > 24:
        raise ValueError('WebVTT timestamp exceeds the supported finite timeline.')
    if minutes >= 60 or seconds >= 60:
        raise ValueError('Malformed WebVTT timestamp.')
    total = hours * 3600 + minutes * 60 + seconds
    if total > 86400:
        raise ValueError('WebVTT timestamp exceeds the supported finite timeline.')
    return total


def parse_webvtt(text):
    if not isinstance(text, str) or len(text) > MAX_INPUT_BYTES:
        raise ValueError('WebVTT input exceeds the size limit.')
    try:
        if len(text.encode('utf-8')) > MAX_INPUT_BYTES:
            raise ValueError('WebVTT input exceeds the size limit.')
    except UnicodeError:
        raise ValueError('WebVTT contains invalid Unicode.') from None
    if any(ord(c) < 32 and c not in '\n\r\t' for c in text):
        raise ValueError('WebVTT contains unsupported control characters.')
    text = text.removeprefix('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    blocks = re.split(r'\n[ \t]*\n', text.rstrip('\n'))
    header = blocks.pop(0).split('\n') if blocks else []
    if not header or not re.fullmatch(r'WEBVTT(?:[ \t][^\n]*)?', header[0]) or '-->' in header[0]:
        raise ValueError('Missing WebVTT header.')
    for line in header[1:]:
        if line != 'X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0':
            raise ValueError('WebVTT header requires unsupported timestamp or metadata handling.')
    globals_, cues = [], []
    for block in blocks:
        if not block.strip():
            continue
        lines = block.split('\n')
        if re.match(r'^NOTE(?:[ \t]|$)', lines[0]):
            continue
        if lines[0] in ('STYLE', 'REGION'):
            if cues or '-->' in block:
                raise ValueError('Misplaced WebVTT style or region block.')
            globals_.append(block)
            continue
        index = 0 if '-->' in lines[0] else 1
        if index >= len(lines):
            raise ValueError('WebVTT cue has no timing line.')
        match = TIMING.fullmatch(lines[index])
        if not match:
            raise ValueError('Malformed WebVTT cue timing.')
        start, end = timestamp(match[1]), timestamp(match[2])
        if end <= start or (cues and start < cues[-1].start):
            raise ValueError('WebVTT cues must have increasing start times and positive durations.')
        cues.append(Cue(start, end, block))
        if len(cues) > MAX_CUES:
            raise ValueError('WebVTT cue count exceeds the limit.')
    # An explicit identity map documents the required shared zero-based epoch.
    common = 'WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0\n\n'
    if globals_:
        common += '\n\n'.join(globals_) + '\n\n'
    return common, cues


def package_webvtt(text, segment_durations, target_duration, complete, prefix='sub0', playlist_type='EVENT', mpegts_origin=0, video_start_offset=0):
    if not isinstance(prefix, str) or not re.fullmatch(r'sub\d{1,3}', prefix):
        raise ValueError('Invalid subtitle rendition prefix.')
    if type(target_duration) is not int or not 1 <= target_duration <= 86400:
        raise ValueError('Invalid video target duration.')
    if type(complete) is not bool or playlist_type not in ('EVENT', 'VOD') or (playlist_type == 'VOD' and not complete):
        raise ValueError('Invalid subtitle playlist type or completion state.')
    if not isinstance(segment_durations, (list, tuple)) or not 1 <= len(segment_durations) <= MAX_SEGMENTS:
        raise ValueError('Invalid video segment count.')
    if type(video_start_offset) not in (int, float) or not math.isfinite(video_start_offset) or not 0 <= video_start_offset <= 0.1:
        raise ValueError('Unsupported initial video offset.')
    boundaries = [float(video_start_offset)]
    for duration in segment_durations:
        if type(duration) not in (int, float) or not 0 < duration <= 86400 or not math.isfinite(duration):
            raise ValueError('Invalid video segment duration.')
        if math.floor(duration + 0.5) > target_duration:
            raise ValueError('Video segment exceeds target duration.')
        boundaries.append(boundaries[-1] + duration)
    if boundaries[-1] > 86400:
        raise ValueError('Video exceeds the supported finite timeline.')
    if type(mpegts_origin) is not int or not 0 <= mpegts_origin < 2**33 or mpegts_origin + math.ceil(boundaries[-1] * 90000) >= 2**33:
        raise ValueError('Subtitle timeline crosses an unsupported MPEGTS timestamp wrap.')
    common, cues = parse_webvtt(text)
    common = common.replace('MPEGTS:0\n', f'MPEGTS:{mpegts_origin}\n', 1)
    assignments = [[] for _ in segment_durations]
    references = 0
    for cue in cues:
        first = max(0, bisect_right(boundaries, cue.start) - 1)
        last = min(len(segment_durations), bisect_left(boundaries, cue.end))
        references += max(0, last - first)
        if references > MAX_REFERENCES:
            raise ValueError('Subtitle overlap exceeds the packaging limit.')
        for index in range(first, last):
            assignments[index].append(cue.block)
    playlist = ['#EXTM3U', '#EXT-X-VERSION:3', f'#EXT-X-TARGETDURATION:{target_duration}',
                '#EXT-X-MEDIA-SEQUENCE:0', f'#EXT-X-PLAYLIST-TYPE:{playlist_type}']
    segments, size = {}, 0
    for index, blocks in enumerate(assignments):
        name = f'{prefix}-{index:06d}.vtt'
        content = (common + ''.join(block + '\n\n' for block in blocks)).encode('utf-8')
        size += len(content)
        if size > MAX_OUTPUT_BYTES:
            raise ValueError('Subtitle rendition exceeds the output size limit.')
        segments[name] = content
        playlist += [f'#EXTINF:{segment_durations[index]:.6f},', name]
    if complete:
        playlist.append('#EXT-X-ENDLIST')
    return SubtitlePackage(prefix + '.m3u8', ('\n'.join(playlist) + '\n').encode('ascii'), segments)
