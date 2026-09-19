#!/usr/bin/env python3
"""Generate small, original media for physical two-language AirPlay verification."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path

FLOOR = 15_728_640 * 1024
BUDGET = 10 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'work' / 'm0-fixture')
    args = parser.parse_args()
    output = args.output.resolve()
    print(subprocess.check_output(['df', '-Pk', '/'], text=True), end='')
    ancestor = output
    while not ancestor.exists():
        ancestor = ancestor.parent
    if shutil.disk_usage(ancestor).free - BUDGET <= FLOOR:
        raise SystemExit('Stopped: estimated 10 MiB peak would cross the 15 GiB boundary.')
    ffmpeg = next((p for p in ['/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg', shutil.which('ffmpeg')]
                   if p and Path(p).is_file()), None)
    if not ffmpeg:
        raise SystemExit('An existing FFmpeg installation is required; nothing was installed.')
    names = ['video.mp4', 'English.en.srt', 'Chinese.zh.srt', 'manifest.json']
    if any((output / name).exists() for name in names):
        raise SystemExit('Fixture files already exist; choose a fresh --output directory.')
    output.mkdir(parents=True, exist_ok=True)
    before = sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
    fonts = ['/System/Library/Fonts/Supplemental/Arial.ttf', '/Library/Fonts/Arial.ttf']
    font = next((p for p in fonts if Path(p).is_file()), None)
    filters = subprocess.check_output([ffmpeg, '-hide_banner', '-filters'], stderr=subprocess.DEVNULL, text=True)
    drawtext = font is not None and 'drawtext' in filters
    # Test source has a moving marker even on systems without the optional text filter.
    video_filter = (f"drawtext=fontfile='{font}':text='VideoBridge M0 - seconds %{{eif\\:t\\:d}}':"
                    'fontcolor=white:fontsize=28:box=1:boxcolor=black@0.8:x=20:y=20') if drawtext else 'null'
    cmd = [ffmpeg, '-nostdin', '-hide_banner', '-v', 'error', '-f', 'lavfi', '-i',
           'testsrc2=size=640x360:rate=25', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
           '-t', '30', '-vf', video_filter, '-c:v', 'libx264', '-preset', 'veryfast',
           '-b:v', '600k', '-maxrate', '700k', '-bufsize', '1400k', '-g', '50', '-bf', '0',
           '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '64k', '-ac', '2',
           '-movflags', '+faststart', '-fs', str(BUDGET - 65536), '-n', str(output / names[0])]
    subprocess.run(cmd, check=True, timeout=90)
    tracks = []
    for lang, label in [('en', 'English'), ('zh', '中文')]:
        cues = []
        blocks = []
        for index in range(6):
            start, end = index * 5 + 1, index * 5 + 5
            text = (f'ENGLISH cue {index + 1} | {start:02d}-{end:02d} seconds' if lang == 'en'
                    else f'中文字幕 {index + 1} | 第{start:02d}至{end:02d}秒')
            cues.append(dict(start_seconds=start, end_seconds=end, text=text))
            blocks.append(f'{index + 1}\n00:00:{start:02d},000 --> 00:00:{end:02d},000\n{text}\n')
        filename = 'English.en.srt' if lang == 'en' else 'Chinese.zh.srt'
        (output / filename).write_text('\n'.join(blocks), encoding='utf-8')
        tracks.append(dict(language=lang, label=label, filename=filename, cues=cues))
    manifest = dict(schema_version=1, synthetic=True, duration_seconds=30, width=640, height=360,
                    video=names[0], video_codec='h264', audio_codec='aac',
                    visible_timing='whole seconds counter' if drawtext else 'testsrc2 moving pattern; use player clock',
                    subtitles=tracks,
                    acceptance=['Select English, Chinese, then Off on Apple TV.',
                                'Match cue text and timing to this manifest, including seek and segment boundaries.',
                                'Switch Mac apps while TV playback continues; do not use screen mirroring.'])
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    size = sum((output / name).stat().st_size for name in names)
    if size > BUDGET:
        raise SystemExit(f'Fixture exceeds budget: {size} bytes; inspect retained output.')
    print(json.dumps(dict(output=str(output), created_bytes=size, directory_before_bytes=before,
                          directory_after_bytes=sum(p.stat().st_size for p in output.rglob('*') if p.is_file()),
                          visible_seconds=drawtext), indent=2))


if __name__ == '__main__':
    main()
