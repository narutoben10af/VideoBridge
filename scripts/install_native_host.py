#!/usr/bin/env python3
"""Stage the native messaging host by default; --install writes per-user registration.

Does not install a browser extension or replace an existing registration. Review the
staged files first. Existing identical files are accepted; changed files fail closed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
HOST_NAME = 'app.videobridge.bridge'
FIREFOX_ID = 'videobridge@local.app'
EARLY_FLOOR = 15_728_640 * 1024


def ensure_directory(path, private=False):
    path = Path(path).absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Installation paths must not contain symlinks or non-directories.')
    info = path.lstat()
    if info.st_uid != os.getuid() or (private and stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('Installation directory ownership or permissions are unsafe.')


def plan(app_path, chrome_ids, home=None):
    home = Path.home() if home is None else Path(home)
    app_path = Path(app_path).absolute()
    if not app_path.is_dir() or app_path.suffix != '.app':
        raise ValueError('Choose an installed VideoBridge.app directory.')
    if any(not re.fullmatch('[a-p]{32}', value) for value in chrome_ids):
        raise ValueError('Chrome IDs must contain 32 lowercase letters a through p.')
    base = home / 'Library/Application Support/VideoBridge'
    install = base / 'NativeHost'
    firefox_manifest = home / 'Library/Application Support/Mozilla/NativeMessagingHosts' / (HOST_NAME + '.json')
    chrome_manifest = home / 'Library/Application Support/Google/Chrome/NativeMessagingHosts' / (HOST_NAME + '.json')
    wrapper = '#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(install / 'host.py')) + ' "$@"\n'
    config = {'hostName': HOST_NAME, 'appPath': str(app_path), 'firefoxManifestPath': str(firefox_manifest),
              'chromeExtensionIds': sorted(set(chrome_ids))}
    manifest = {'name': HOST_NAME, 'description': 'VideoBridge browser handoff',
                'path': str(install / 'launch'), 'type': 'stdio'}
    def encoded(value):
        return (json.dumps(value, indent=2) + '\n').encode('utf-8')
    files = [
        (install / 'host.py', (ROOT / 'native-host/host.py').read_bytes(), 0o600, 'host.py'),
        (install / 'bridge_protocol.py', (ROOT / 'app/bridge_protocol.py').read_bytes(), 0o600, 'bridge_protocol.py'),
        (install / 'launch', wrapper.encode('utf-8'), 0o700, 'launch'),
        (install / 'config.json', encoded(config), 0o600, 'config.json'),
        (firefox_manifest, encoded(dict(manifest, allowed_extensions=[FIREFOX_ID])), 0o600, 'firefox-manifest.json')]
    if chrome_ids:
        files.append((chrome_manifest, encoded(dict(manifest, allowed_origins=[
            'chrome-extension://' + ident + '/' for ident in sorted(set(chrome_ids))])), 0o600, 'chrome-manifest.json'))
    return files


def write_exclusive(path, data, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def install(files):
    if shutil.disk_usage('/').free - 1024 * 1024 <= EARLY_FLOOR:
        raise ValueError('Installation would cross the free-space safety boundary.')
    # Preflight every existing target before any file mutation.
    for path, data, mode, _ in files:
        if path.is_symlink():
            raise ValueError('Refusing a symlink registration target.')
        if path.exists():
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != mode or path.read_bytes() != data:
                raise ValueError('An existing native-host file differs. Review it before replacement.')
    created = []
    try:
        for path, data, mode, _ in files:
            ensure_directory(path.parent, private=path.parent.name == 'NativeHost')
            if path.exists():
                continue
            write_exclusive(path, data, mode)
            created.append(path)
    except BaseException:
        for path in reversed(created):
            path.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-path', required=True, type=Path)
    parser.add_argument('--chrome-extension-id', action='append', default=[])
    parser.add_argument('--stage-dir', type=Path, default=ROOT / 'work/native-host-stage')
    parser.add_argument('--install', action='store_true', help='Install the reviewed host and per-user manifests.')
    args = parser.parse_args()
    try:
        files = plan(args.app_path, args.chrome_extension_id)
        if args.install:
            install(files)
            print('Installed the native host and requested per-user browser registrations.')
        else:
            ensure_directory(args.stage_dir, private=True)
            for _, data, mode, name in files:
                path = args.stage_dir / name
                if path.exists():
                    if path.is_symlink() or path.read_bytes() != data:
                        raise ValueError('Staging directory already contains different files. Choose a fresh --stage-dir.')
                else:
                    write_exclusive(path, data, mode)
            print('Dry run: staged native-host files; no browser registration changed.')
            print(str(args.stage_dir))
        return 0
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
