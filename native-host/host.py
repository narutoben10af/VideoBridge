#!/usr/bin/env python3
"""Native messaging host. Browser identity is checked before reading descriptors.

Uses a private per-user inbox and an opaque URL token; never puts media in argv.
This protects against web-origin callers, not malicious programs running as the user.
"""
import fcntl
import json
import math
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import time

# Installed copies place bridge_protocol.py beside this file. Repo tests load app/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from bridge_protocol import (MAX_ENVELOPE_BYTES, REQUEST_TTL_SECONDS, ProtocolError,
                             capabilities, canonical_request_id, decode_json, parse_message, response, validate_message)

FIREFOX_ID = 'videobridge@local.app'
HOST_NAME = 'app.videobridge.bridge'
MAX_PENDING = 32


def checked_directory(path, create=False):
    """Walk from the filesystem root without following any symlink component."""
    path = Path(path).absolute()
    parts = path.parts
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(parts[1:]):
            last = index == len(parts) - 2
            try:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=fd)
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            if last:
                info = os.fstat(fd)
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise ProtocolError('INBOX_UNSAFE', 'The browser inbox has unsafe permissions.')
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_private(fd, name):
    item = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        info = os.fstat(item)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise ProtocolError('INBOX_UNSAFE', 'The browser inbox contains an unsafe request.')
        with os.fdopen(item, 'rb', closefd=False) as stream:
            raw = stream.read(MAX_ENVELOPE_BYTES + 1)
        return decode_json(raw)
    finally:
        os.close(item)


class Inbox:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock

    @staticmethod
    def prune_expired(fd, names, now):
        for name in names:
            try:
                identifier = canonical_request_id(name[:-5])
                before = os.stat(name, dir_fd=fd, follow_symlinks=False)
                record = read_private(fd, name)
                if not isinstance(record, dict):
                    continue
                created = record.pop('createdAt', None)
                validate_message(record)
                if record['type'] != 'offerMedia' or record['requestId'] != identifier:
                    continue
                if type(created) not in (int, float) or not math.isfinite(created) or not created < now - REQUEST_TTL_SECONDS:
                    continue
                after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    continue
                os.unlink(name, dir_fd=fd)
            except (OSError, ValueError, OverflowError, TypeError, KeyError):
                # Unknown or concurrently consumed files are never cleanup targets.
                continue

    def put(self, envelope):
        validate_message(envelope)
        if envelope['type'] != 'offerMedia':
            raise ProtocolError('INVALID_REQUEST', 'Only media offers can enter the inbox.')
        now = self.clock()
        record = dict(envelope, createdAt=now)
        raw = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(raw) > MAX_ENVELOPE_BYTES:
            raise ProtocolError('REQUEST_TOO_LARGE', 'Browser request exceeds the inbox size limit.')
        parent_fd = checked_directory(self.path.parent, create=True)
        os.close(parent_fd)
        fd = checked_directory(self.path, create=True)
        try:
            # flock serializes duplicate detection and the pending-count bound.
            fcntl.flock(fd, fcntl.LOCK_EX)
            names = [name for name in os.listdir(fd) if name.endswith('.json')]
            name = envelope['requestId'] + '.json'
            if name in names:
                old = read_private(fd, name)
                if not isinstance(old, dict):
                    raise ProtocolError('INBOX_UNSAFE', 'The browser inbox contains an invalid request.')
                created = old.pop('createdAt', None)
                if old != envelope:
                    raise ProtocolError('DUPLICATE_REQUEST', 'Request ID was already used for different media.')
                if type(created) not in (int, float) or not 0 <= now - created <= REQUEST_TTL_SECONDS:
                    raise ProtocolError('REQUEST_EXPIRED', 'Browser request has expired. Send it again.')
                return envelope['requestId'], False
            self.prune_expired(fd, names, now)
            names = [entry for entry in os.listdir(fd) if entry.endswith('.json')]
            if len(names) >= MAX_PENDING:
                raise ProtocolError('INBOX_FULL', 'Too many browser requests are pending. Open VideoBridge to review them.')
            item = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                os.fchmod(item, 0o600)
                with os.fdopen(item, 'wb', closefd=False) as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                os.unlink(name, dir_fd=fd)
                raise
            finally:
                os.close(item)
            return envelope['requestId'], True
        finally:
            os.close(fd)


def verify_identity(config, arguments):
    chrome_ids = config.get('chromeExtensionIds', [])
    if len(arguments) == 1 and arguments[0] in ['chrome-extension://' + ident + '/' for ident in chrome_ids]:
        return 'chrome'
    if len(arguments) == 2 and arguments[0] == config.get('firefoxManifestPath') and arguments[1] == FIREFOX_ID:
        return 'firefox'
    raise ProtocolError('UNAUTHORIZED_EXTENSION', 'This browser extension is not authorized.')


def launch_app(app_path, token):
    result = subprocess.run(['/usr/bin/open', '-a', app_path, 'videobridge://request/' + token],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=10)
    if result.returncode:
        raise ProtocolError('APP_UNAVAILABLE', 'VideoBridge could not be opened.')


def exact_read(stream, size):
    chunks = bytearray()
    while len(chunks) < size:
        data = stream.read(size - len(chunks))
        if not data:
            raise ProtocolError('INVALID_FRAME', 'Incomplete native message.')
        chunks.extend(data)
    return bytes(chunks)


def read_frame(stream):
    first = stream.read(1)
    if not first:
        return None
    prefix = first + exact_read(stream, 3)
    size = struct.unpack('=I', prefix)[0]
    if size == 0 or size > MAX_ENVELOPE_BYTES:
        raise ProtocolError('REQUEST_TOO_LARGE', 'Native message exceeds the size limit.')
    return exact_read(stream, size)


def write_frame(stream, value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    stream.write(struct.pack('=I', len(raw)) + raw)
    stream.flush()


def serve(input_stream, output_stream, config, arguments, inbox, launcher=launch_app):
    try:
        verify_identity(config, arguments)
    except ProtocolError as error:
        write_frame(output_stream, response(None, 'failed', {'code': error.code, 'message': str(error)}))
        return 1
    while True:
        request_id = None
        try:
            raw = read_frame(input_stream)
            if raw is None:
                return 0
            envelope = parse_message(raw)
            request_id = envelope['requestId']
            if envelope['type'] == 'getCapabilities':
                answer = capabilities(request_id)
            else:
                token, _ = inbox.put(envelope)
                launcher(config['appPath'], token)
                answer = response(request_id, 'accepted', {'token': token})
            write_frame(output_stream, answer)
        except ProtocolError as error:
            write_frame(output_stream, response(request_id, 'failed', {'code': error.code, 'message': str(error)}))
            # Bad framing or requests close the host; never attempt stream resync.
            return 1
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            write_frame(output_stream, response(request_id, 'failed', {
                'code': 'HOST_UNAVAILABLE', 'message': 'VideoBridge could not receive this request.'}))
            return 1


def main():
    try:
        location = Path(__file__).resolve().parent
        fd = checked_directory(location)
        try:
            config = read_private(fd, 'config.json')
        finally:
            os.close(fd)
        inbox = Inbox(Path.home() / 'Library/Application Support/VideoBridge/Inbox')
        return serve(sys.stdin.buffer, sys.stdout.buffer, config, sys.argv[1:], inbox)
    except (OSError, ValueError):
        return 1


if __name__ == '__main__':
    sys.exit(main())
