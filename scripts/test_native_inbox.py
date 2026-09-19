#!/usr/bin/env python3
"""Exercise actual Swift inbox code against a task-owned synthetic home."""
from pathlib import Path
import subprocess
import tempfile
import shutil
ROOT = Path(__file__).resolve().parents[1]
if shutil.disk_usage('/').free < 15728640 * 1024 + 256 * 1024**2:
    raise SystemExit('Need 256 MiB test allowance above the storage floor.')
source = (ROOT / 'app/VideoBridge.swift').read_text()
reader = source[:source.index('@MainActor final class Playback')]
reader = reader.replace('FileManager.default.homeDirectoryForCurrentUser.path', 'testHome.path')
start = source.index('    nonisolated static func webURL')
end = source.index('    func loadSubtitleTest', start)
helper = source[start:end].replace('nonisolated ', '')
with tempfile.TemporaryDirectory(prefix='videobridge-inbox-tests-') as folder:
    folder = Path(folder)
    text = reader + '\nlet testHome = URL(fileURLWithPath: "' + str(folder / 'home') + '")\n'
    text += 'struct Playback {\n' + helper + '}\n'
    text += (ROOT / 'tests/native/InboxTests.swift').read_text()
    test = folder / 'InboxTests.swift'
    test.write_text(text)
    subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-module-cache-path', str(ROOT / 'work/swift-cache'), str(test), '-o', str(folder / 'tests')], check=True)
    subprocess.run([str(folder / 'tests')], check=True)
