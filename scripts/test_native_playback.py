#!/usr/bin/env python3
"""Run the production Playback controller against isolated synthetic local HLS.

No UI, installed app, AirPlay receiver or user media is opened. This is local
AVFoundation integration evidence, not receiver subtitle/rendering acceptance.
"""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import shlex
import sys
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--progressive', action='store_true', help='Pace only synthetic HLS preparation at 2x input speed.')
parser.add_argument('--progressive-playing', action='store_true', help='Use paced preparation and verify playing-state finalization with selected subtitles.')
args = parser.parse_args()
progressive = args.progressive or args.progressive_playing
ROOT = Path(__file__).resolve().parents[1]
FLOOR = 15_728_640 * 1024
print(subprocess.check_output(['df', '-Pk', '/'], text=True), end='')
if shutil.disk_usage('/').free <= FLOOR + 512 * 1024**2:
    raise SystemExit('Need a 512 MiB allowance above the storage floor.')
env = dict(os.environ, DEVELOPER_DIR='/Library/Developer/CommandLineTools')
env['VIDEOBRIDGE_TEST_PROGRESSIVE'] = '1' if progressive else '0'
env['VIDEOBRIDGE_TEST_PROGRESSIVE_PLAYING'] = '1' if args.progressive_playing else '0'
ffmpeg = next((p for p in ['/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg', shutil.which('ffmpeg')]
               if p and Path(p).is_file()), None)
if not ffmpeg:
    raise SystemExit('FFmpeg is required; nothing was installed.')
source = (ROOT / 'app/VideoBridge.swift').read_text()
# Extract the complete production types and controller verbatim. Do not replace
# methods, inject state setters, or duplicate Playback in the test.
controller = source[:source.index('struct NativeVideo: NSViewRepresentable')]
with tempfile.TemporaryDirectory(prefix='videobridge-native-playback-') as directory:
    root = Path(directory)
    app = root / 'PlaybackTests.app'
    resources = app / 'Contents/Resources'
    binary = app / 'Contents/MacOS/PlaybackTests'
    resources.mkdir(parents=True)
    binary.parent.mkdir(parents=True)
    for name in ['relay.py', 'process_worker.py', 'remote_media.py', 'subtitle_hls.py', 'hls_publication.py']:
        shutil.copyfile(ROOT / 'app' / name, resources / name)
    if progressive:
        # Production relay is copied unchanged. Only this isolated test launcher
        # substitutes a wrapper that paces video preparation; probe and subtitle
        # conversion retain their real production commands.
        (resources / 'relay.py').rename(resources / 'relay_impl.py')
        wrapper_code = root / 'paced_ffmpeg.py'
        wrapper_code.write_text("import os,sys\nargs=sys.argv[1:]\nif '-hls_segment_filename' in args:\n    index=args.index('-i'); args[index:index]=['-readrate','2']\nos.execv(" + repr(ffmpeg) + ", [" + repr(ffmpeg) + ", *args])\n")
        wrapper = root / 'paced_ffmpeg'
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(wrapper_code)) + ' "$@"\n')
        wrapper.chmod(0o700)
        (resources / 'relay.py').write_text(
            "import sys,json\nimport relay_impl as relay\noriginal=relay.executable\n"
            "relay.executable=lambda name: " + repr(str(wrapper)) + " if name=='ffmpeg' else original(name)\n"
            "try:\n    raw=sys.stdin.buffer.readline(65537)\n    if len(raw)>65536: raise ValueError('Request is too large.')\n"
            "    relay.run(json.loads(raw))\nexcept InterruptedError: pass\nexcept Exception:\n"
            "    relay.emit('error',message='Synthetic progressive preparation failed.'); sys.exit(1)\n")
    with (app / 'Contents/Info.plist').open('wb') as stream:
        plistlib.dump({'CFBundleExecutable':'PlaybackTests', 'CFBundleIdentifier':'test.videobridge.playback',
                      'CFBundlePackageType':'APPL', 'NSAppTransportSecurity': {
                          'NSAllowsLocalNetworking':True, 'NSAllowsArbitraryLoadsForMedia':True}}, stream)
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=160x90:rate=10', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                    '-t', '30' if progressive else '12', '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '20', '-bf', '0',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '32k', str(resources / 'video.mp4')],
                   check=True, timeout=30)
    for name, text in [('en.srt','English synthetic cue'), ('zh.srt','中文测试字幕')]:
        (resources / name).write_text('1\n00:00:01,000 --> 00:00:' + ('29' if progressive else '11') + ',000\n' + text + '\n', encoding='utf-8')
    extracted = root / 'PlaybackTests.swift'
    extracted.write_text(controller + '\n' + (ROOT / 'tests/native/PlaybackTests.swift').read_text())
    subprocess.run(['xcrun', 'swiftc', '-swift-version', '5', '-parse-as-library',
                    '-module-cache-path', str(ROOT / 'work/swift-cache'), str(extracted), '-o', str(binary),
                    '-framework', 'SwiftUI', '-framework', 'AppKit', '-framework', 'AVKit', '-framework', 'AVFoundation'],
                   env=env, check=True, timeout=90)
    subprocess.run([str(binary)], env=env, check=True, timeout=100)
