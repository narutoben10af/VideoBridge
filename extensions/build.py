#!/usr/bin/env python3
"""Package shared sources as unpacked browser extensions; no dependencies installed."""
import argparse
import shutil
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('browser', choices=['firefox', 'chrome'])
parser.add_argument('--output', type=Path)
args = parser.parse_args()
print(subprocess.check_output(['df', '-Pk', '/'], text=True), end='')
output = args.output or root.parent / 'work' / 'extensions' / args.browser
ancestor = output.resolve()
while not ancestor.exists(): ancestor = ancestor.parent
if shutil.disk_usage(ancestor).free - 1024 * 1024 <= 15_728_640 * 1024:
    raise SystemExit('Stopped: need 1 MiB above the 15 GiB free-space boundary.')
output.mkdir(parents=True, exist_ok=True)
for source in (root / 'shared').iterdir():
    if source.is_file(): shutil.copyfile(source, output / source.name)
shutil.copyfile(root / args.browser / 'manifest.json', output / 'manifest.json')
print(output.resolve())
