import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import bridge_protocol

spec = importlib.util.spec_from_file_location('native_host', ROOT / 'native-host/host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
spec = importlib.util.spec_from_file_location('native_host_installer', ROOT / 'scripts/install_native_host.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def offer():
    return json.loads((ROOT / 'protocol/valid-offer.json').read_text())


def frame(value):
    raw = json.dumps(value).encode()
    return struct.pack('=I', len(raw)) + raw


def decode_output(stream):
    stream.seek(0)
    return json.loads(host.read_frame(stream))


class NativeHostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='videobridge-host-test-')
        self.root = Path(self.temporary.name).resolve()
        self.inbox = host.Inbox(self.root / 'VideoBridge/Inbox')
        self.config = {'appPath': '/Applications/VideoBridge.app', 'chromeExtensionIds': ['a' * 32],
                       'firefoxManifestPath': '/test/native-manifest.json'}
        self.arguments = ['/test/native-manifest.json', host.FIREFOX_ID]

    def tearDown(self):
        self.temporary.cleanup()

    def test_identity_allowlist_and_browser_arguments(self):
        self.assertEqual(host.verify_identity(self.config, self.arguments), 'firefox')
        self.assertEqual(host.verify_identity(self.config, ['chrome-extension://' + 'a' * 32 + '/']), 'chrome')
        for args in [[], ['https://example.org'], ['/test/native-manifest.json', 'other@example.org'],
                     ['/wrong-manifest.json', host.FIREFOX_ID], ['chrome-extension://' + 'b' * 32 + '/']]:
            with self.assertRaises(bridge_protocol.ProtocolError):
                host.verify_identity(self.config, args)

    def test_offer_private_inbox_and_opaque_launch(self):
        output, launcher = io.BytesIO(), Mock()
        value = offer()
        result = host.serve(io.BytesIO(frame(value)), output, self.config, self.arguments, self.inbox, launcher)
        self.assertEqual(result, 0)
        token = value['requestId']
        launcher.assert_called_once_with(self.config['appPath'], token)
        path = self.inbox.path / (token + '.json')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        record = json.loads(path.read_text())
        self.assertLess(abs(record.pop('createdAt') - time.time()), 3)
        self.assertEqual(record, value)
        self.assertEqual(decode_output(output)['type'], 'accepted')

    def test_capabilities_do_not_create_inbox_or_launch(self):
        value = offer()
        value['type'], value['payload'] = 'getCapabilities', {}
        output, launcher = io.BytesIO(), Mock()
        host.serve(io.BytesIO(frame(value)), output, self.config, self.arguments, self.inbox, launcher)
        self.assertEqual(decode_output(output)['type'], 'capabilities')
        launcher.assert_not_called()
        self.assertFalse(self.inbox.path.exists())

    def test_unauthorized_or_invalid_request_has_no_writes(self):
        for arguments, raw in [([], frame(offer())), (self.arguments, b'\xff\xff\xff\x7f'),
                               (self.arguments, b'\x04\x00'), (self.arguments, struct.pack('=I', 8) + b'{}')]:
            with self.subTest(arguments=arguments, raw=raw):
                output, launcher = io.BytesIO(), Mock()
                self.assertEqual(host.serve(io.BytesIO(raw), output, self.config, arguments, self.inbox, launcher), 1)
                self.assertEqual(decode_output(output)['type'], 'failed')
                launcher.assert_not_called()
                self.assertFalse(self.inbox.path.exists())

    def test_duplicate_outstanding_request_and_mutation(self):
        value = offer()
        self.assertEqual(self.inbox.put(value), (value['requestId'], True))
        self.assertEqual(self.inbox.put(value), (value['requestId'], False))
        value['payload']['title'] = 'Different'
        with self.assertRaisesRegex(bridge_protocol.ProtocolError, 'different media'):
            self.inbox.put(value)
        self.assertEqual(len(list(self.inbox.path.iterdir())), 1)

    def test_expired_duplicate_rejected(self):
        value = offer()
        self.inbox.clock = lambda: 1000
        self.inbox.put(value)
        self.inbox.clock = lambda: 1061
        with self.assertRaisesRegex(bridge_protocol.ProtocolError, 'expired'):
            self.inbox.put(value)

    def test_symlink_and_unsafe_mode_rejected(self):
        parent = self.inbox.path.parent
        parent.mkdir(mode=0o700)
        self.inbox.path.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            self.inbox.put(offer())
        self.inbox.path.unlink()
        self.inbox.path.mkdir(mode=0o755)
        with self.assertRaises(bridge_protocol.ProtocolError):
            self.inbox.put(offer())

    def test_symlink_file_and_hardlink_rejected(self):
        value = offer()
        self.inbox.put(value)
        path = self.inbox.path / (value['requestId'] + '.json')
        os.link(path, self.root / 'alias.json')
        with self.assertRaises(bridge_protocol.ProtocolError):
            self.inbox.put(value)
        (self.root / 'alias.json').unlink()
        path.unlink()
        path.symlink_to(self.root / 'missing.json')
        with self.assertRaises(OSError):
            self.inbox.put(value)

    def test_expired_owned_requests_pruned_before_capacity_check(self):
        self.inbox.clock = lambda: 1000
        for index in range(32):
            value = offer()
            value['requestId'] = f'bc47cdd5-e533-46a8-8b0d-{index:012x}'
            self.inbox.put(value)
        self.inbox.clock = lambda: 1061
        self.inbox.put(offer())
        self.assertEqual(len(list(self.inbox.path.glob('*.json'))), 1)

    def test_expiry_cleanup_preserves_unknown_unsafe_and_unexpired(self):
        self.inbox.clock = lambda: 1000
        value = offer()
        self.inbox.put(value)
        original = self.inbox.path / (value['requestId'] + '.json')
        old_record = json.loads(original.read_text())
        foreign = self.inbox.path / 'personal.json'
        foreign.write_text(json.dumps(old_record))
        bad_id = 'bc47cdd5-e533-46a8-8b0d-000000000001'
        unsafe = self.inbox.path / (bad_id + '.json')
        old_record['requestId'] = bad_id
        unsafe.write_text(json.dumps(old_record)); unsafe.chmod(0o644)
        linked_id = 'bc47cdd5-e533-46a8-8b0d-000000000002'
        linked = self.inbox.path / (linked_id + '.json')
        linked.symlink_to(original)
        hard_id = 'bc47cdd5-e533-46a8-8b0d-000000000005'
        hard = self.inbox.path / (hard_id + '.json')
        old_record['requestId'] = hard_id
        hard.write_text(json.dumps(old_record)); hard.chmod(0o600)
        os.link(hard, self.root / 'retain-hardlink')
        malformed = self.inbox.path / 'bc47cdd5-e533-46a8-8b0d-000000000006.json'
        malformed.write_text('null'); malformed.chmod(0o600)
        self.inbox.clock = lambda: 1030
        new = offer(); new['requestId'] = 'bc47cdd5-e533-46a8-8b0d-000000000003'
        self.inbox.put(new)
        self.assertTrue(original.exists())
        self.inbox.clock = lambda: 1061
        next_request = offer(); next_request['requestId'] = 'bc47cdd5-e533-46a8-8b0d-000000000004'
        self.inbox.put(next_request)
        self.assertFalse(original.exists())
        self.assertTrue(foreign.exists()); self.assertTrue(unsafe.exists()); self.assertTrue(linked.is_symlink())
        self.assertTrue(hard.exists()); self.assertTrue(malformed.exists())
        self.assertTrue((self.inbox.path / (new['requestId'] + '.json')).exists())

    def test_pending_request_limit(self):
        self.inbox.put(offer())
        other = offer()
        other['requestId'] = 'dda1cdd5-e533-46a8-8b0d-55d691d3b9a2'
        with patch.object(host, 'MAX_PENDING', 1):
            with self.assertRaisesRegex(bridge_protocol.ProtocolError, 'Too many'):
                self.inbox.put(other)

    def test_launch_failure_preserves_retryable_private_request(self):
        output = io.BytesIO()
        launcher = Mock(side_effect=OSError('private URL must not leak'))
        host.serve(io.BytesIO(frame(offer())), output, self.config, self.arguments, self.inbox, launcher)
        response = decode_output(output)
        self.assertEqual(response['payload']['code'], 'HOST_UNAVAILABLE')
        self.assertNotIn('private URL', json.dumps(response))
        self.assertEqual(len(list(self.inbox.path.iterdir())), 1)

    def test_launch_argv_contains_only_token(self):
        with patch.object(host.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            host.launch_app('/Applications/VideoBridge.app', offer()['requestId'])
        self.assertEqual(run.call_args.args[0], ['/usr/bin/open', '-a', '/Applications/VideoBridge.app',
                                             'videobridge://request/' + offer()['requestId']])

    def test_installer_plan_and_explicit_install_in_fake_home(self):
        app = self.root / 'VideoBridge.app'
        app.mkdir()
        files = installer.plan(app, ['a' * 32], home=self.root / 'fake-home')
        self.assertFalse((self.root / 'fake-home').exists())
        installer.install(files)
        for path, data, mode, _ in files:
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
        installer.install(files)  # Identical install is idempotent.
        files[0][0].write_text('not the installed host')
        with self.assertRaisesRegex(ValueError, 'differs'):
            installer.install(files)

    def test_installed_host_actual_process_capability_roundtrip(self):
        app = self.root / 'VideoBridge.app'
        app.mkdir()
        files = installer.plan(app, [], home=self.root / 'isolated-home')
        installer.install(files)
        script = next(path for path, _, _, name in files if name == 'launch')
        manifest = next(path for path, _, _, name in files if name == 'firefox-manifest.json')
        value = offer()
        value['type'], value['payload'] = 'getCapabilities', {}
        result = subprocess.run([str(script), str(manifest), host.FIREFOX_ID], input=frame(value),
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        reply = decode_output(io.BytesIO(result.stdout))
        self.assertEqual(reply['requestId'], value['requestId'])
        self.assertEqual(reply['type'], 'capabilities')
        self.assertEqual(result.stderr, b'')

    def test_installer_cli_defaults_to_staging(self):
        app, stage = self.root / 'VideoBridge.app', self.root / 'stage'
        app.mkdir()
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/install_native_host.py'),
                                 '--app-path', str(app), '--stage-dir', str(stage)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b'Dry run', result.stdout)
        self.assertTrue((stage / 'firefox-manifest.json').is_file())
        self.assertFalse((stage / 'chrome-manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
