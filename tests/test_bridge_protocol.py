import copy
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('bridge_protocol', Path(__file__).parents[1] / 'app/bridge_protocol.py')
protocol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol)


def offer():
    return {'protocolVersion': 1, 'requestId': 'bc47cdd5-e533-46a8-8b0d-55d691d3b9a1', 'type': 'offerMedia',
            'payload': {'url': 'https://media.example.org/video.mp4', 'title': 'Synthetic fixture',
                        'referer': 'https://example.org/watch', 'currentTime': 12.5,
                        'subtitles': [{'url': 'https://media.example.org/en.vtt', 'label': 'English', 'language': 'en'},
                                      {'url': 'https://media.example.org/zh.vtt', 'label': '中文', 'language': 'zh'}]}}


class ProtocolTests(unittest.TestCase):
    def test_valid_media_and_capabilities(self):
        value = offer()
        self.assertEqual(protocol.parse_message(json.dumps(value).encode()), value)
        value['type'], value['payload'] = 'getCapabilities', {}
        self.assertEqual(protocol.validate_message(value), value)

    def test_duplicate_fields_at_both_levels_rejected(self):
        raw = json.dumps(offer()).encode()
        for invalid in (raw.replace(b'"protocolVersion": 1', b'"protocolVersion": 1,"protocolVersion": 1'),
                        raw.replace(b'"title": "Synthetic fixture"', b'"title":"a","title":"b"')):
            with self.subTest(raw=invalid):
                with self.assertRaises(protocol.ProtocolError):
                    protocol.parse_message(invalid)

    def test_invalid_urls_rejected(self):
        urls = ['http://media.example.org/video.mp4', 'https://media.example.org:8443/video.mp4', 'file:///tmp/video.mp4', '/tmp/video.mp4', 'blob:https://example.org/a',
                'https://user:pass@example.org/video', 'https://example.org/a\r\nHeader:x',
                'https://example.org/a b', 'https://example.org\\@localhost/a',
                'https://localhost/v', 'https://foo.local/v', 'http://127.0.0.1/v',
                'http://169.254.169.254/v', 'https://[::1]/v', 'http://10.0.0.1/v',
                'https://example.org:99999/v']
        for url in urls:
            with self.subTest(url=url):
                value = offer()
                value['payload']['url'] = url
                with self.assertRaises(protocol.ProtocolError):
                    protocol.validate_message(value)

    def test_wrong_versions_fields_types_and_ids(self):
        mutations = [lambda v: v.update(protocolVersion=True), lambda v: v.update(protocolVersion=2),
                     lambda v: v.update(type='runCommand'), lambda v: v.update(requestId='../escape'),
                     lambda v: v.update(requestId=v['requestId'].upper()),
                     lambda v: v['payload'].update(filePath='/tmp/video'),
                     lambda v: v['payload'].update(headers={'Cookie': 'secret'}),
                     lambda v: v['payload'].update(title=None),
                     lambda v: v['payload']['subtitles'][0].update(language=None)]
        for mutate in mutations:
            value = offer()
            mutate(value)
            with self.assertRaises(protocol.ProtocolError):
                protocol.validate_message(value)

    def test_nonfinite_and_bool_positions_rejected(self):
        for position in [float('nan'), float('inf'), -1, 86401, 10 ** 1000, True, None]:
            value = offer()
            value['payload']['currentTime'] = position
            with self.assertRaises(protocol.ProtocolError):
                protocol.validate_message(value)

    def test_bounds(self):
        value = offer()
        value['payload']['subtitles'] *= 9
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_message(value)
        value = offer()
        value['payload']['title'] = 'x' * 201
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_message(value)
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_message(b' ' * 65537)

    def test_invalid_encoding_and_deep_json(self):
        for raw in [b'\xff', b'[' * 2000 + b']' * 2000, b'{"a": NaN}']:
            with self.assertRaises(protocol.ProtocolError):
                protocol.parse_message(raw)

    def test_shared_valid_fixture(self):
        path = Path(__file__).parents[1] / 'protocol/valid-offer.json'
        protocol.parse_message(path.read_bytes())


if __name__ == '__main__':
    unittest.main()
