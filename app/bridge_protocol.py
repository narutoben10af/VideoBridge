"""Strict, bounded browser-to-app protocol. Source URLs remain untrusted data."""
import ipaddress
import json
import math
import uuid
from urllib.parse import urlsplit

MAX_ENVELOPE_BYTES = 65536
MAX_SUBTITLE_TRACKS = 16
REQUEST_TTL_SECONDS = 60


class ProtocolError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def reject(code='INVALID_REQUEST', message='Invalid browser request.'):
    raise ProtocolError(code, message)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            reject(message='Duplicate JSON fields are not allowed.')
        result[key] = value
    return result


def decode_json(data):
    if not isinstance(data, bytes) or len(data) > MAX_ENVELOPE_BYTES:
        reject('REQUEST_TOO_LARGE', 'Browser request exceeds the size limit.')
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=_pairs,
                          parse_constant=lambda _: reject())
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        reject()


def fields(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        reject()


def text(value, limit, empty=True):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value):
        reject()
    if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        reject()
    return value


def canonical_request_id(value):
    text(value, 36, empty=False)
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        reject()
    if str(parsed) != value or parsed.version != 4:
        reject()
    return value


def remote_url(value, allow_empty=False):
    text(value, 16000, empty=allow_empty)
    if not value and allow_empty:
        return value
    if '\\' in value or any(c.isspace() for c in value):
        reject()
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        if parsed.scheme != 'https' or not host or parsed.username is not None or parsed.password is not None:
            reject()
        if port not in (None, 443):
            reject()
        hostname = host.rstrip('.').lower()
        if hostname == 'localhost' or hostname.endswith(('.localhost', '.local')) or '.' not in hostname and ':' not in hostname:
            reject('SOURCE_POLICY_REJECTED', 'Local network media sources are not accepted from browsers.')
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            # DNS and redirects must also be checked by the remote-source broker.
            hostname.encode('idna')
        else:
            if not address.is_global:
                reject('SOURCE_POLICY_REJECTED', 'Local network media sources are not accepted from browsers.')
    except (ValueError, UnicodeError) as error:
        if isinstance(error, ProtocolError):
            raise
        reject()
    return value


def validate_message(value):
    fields(value, ('protocolVersion', 'requestId', 'type', 'payload'))
    if type(value['protocolVersion']) is not int or value['protocolVersion'] != 1:
        reject('VERSION_MISMATCH', 'Unsupported browser protocol version.')
    canonical_request_id(value['requestId'])
    if value['type'] == 'getCapabilities':
        fields(value['payload'], ())
        return value
    if value['type'] != 'offerMedia':
        reject('UNSUPPORTED_MESSAGE', 'Unsupported browser message.')
    payload = value['payload']
    fields(payload, ('url', 'title', 'referer', 'currentTime', 'subtitles'))
    remote_url(payload['url'])
    remote_url(payload['referer'], allow_empty=True)
    text(payload['title'], 200)
    position = payload['currentTime']
    if type(position) not in (float, int) or not 0 <= position <= 86400 or not math.isfinite(position):
        reject()
    tracks = payload['subtitles']
    if not isinstance(tracks, list) or len(tracks) > MAX_SUBTITLE_TRACKS:
        reject()
    for track in tracks:
        fields(track, ('url', 'label', 'language'))
        remote_url(track['url'])
        text(track['label'], 100)
        text(track['language'], 35, empty=False)
    # Validation also applies to callers constructing Python objects directly.
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (ValueError, UnicodeError, TypeError, RecursionError):
        reject()
    if len(encoded) > MAX_ENVELOPE_BYTES:
        reject('REQUEST_TOO_LARGE', 'Browser request exceeds the size limit.')
    return value


def parse_message(data):
    return validate_message(decode_json(data))


def response(request_id, kind, payload):
    return {'protocolVersion': 1, 'requestId': request_id, 'type': kind, 'payload': payload}


def capabilities(request_id):
    return response(request_id, 'capabilities', {
        'supportedProtocolVersions': [1], 'messageTypes': ['getCapabilities', 'offerMedia'],
        'maxEnvelopeBytes': MAX_ENVELOPE_BYTES, 'maxSubtitleTracks': MAX_SUBTITLE_TRACKS,
        'browserPlaybackVerified': False})
