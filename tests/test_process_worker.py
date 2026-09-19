import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('process_worker', Path(__file__).parents[1] / 'app/process_worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class ProcessWorkerTests(unittest.TestCase):
    def run_child(self, code, event=None, timeout=3):
        return worker.run_cancellable([sys.executable, '-c', code], event or threading.Event(), os.getppid(), timeout)

    def test_success(self):
        result = self.run_child('import sys; print("video"); print("notice", file=sys.stderr)')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'video\n', b'notice\n'))

    def test_running_child_cancelled_and_reaped(self):
        event = threading.Event()
        real_popen = subprocess.Popen
        children = []
        def launch(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child
        timer = threading.Timer(0.2, event.set)
        started = time.monotonic()
        timer.start()
        try:
            with patch.object(worker.subprocess, 'Popen', side_effect=launch):
                with self.assertRaises(InterruptedError):
                    self.run_child('import time; time.sleep(30)', event)
            self.assertLess(time.monotonic() - started, 2)
            self.assertIsNotNone(children[0].returncode)
            with self.assertRaises(ProcessLookupError):
                os.kill(children[0].pid, 0)
        finally:
            timer.cancel()

    def test_cancellation_stops_descendant_process(self):
        event = threading.Event()
        with tempfile.TemporaryDirectory(prefix='videobridge-worker-test-') as directory:
            pid_path = Path(directory) / 'child.pid'
            code = (
                'import subprocess, sys, signal, time; '
                'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]); '
                'signal.signal(signal.SIGTERM, lambda *_: (child.wait(), sys.exit(0))); '
                f'open({str(pid_path)!r}, "w").write(str(child.pid)); '
                'time.sleep(30)'
            )
            def cancel_after_child_starts():
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and not pid_path.exists():
                    time.sleep(0.01)
                event.set()
            thread = threading.Thread(target=cancel_after_child_starts)
            thread.start()
            try:
                with self.assertRaises(InterruptedError):
                    self.run_child(code, event)
                descendant = int(pid_path.read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(descendant, 0)
            finally:
                event.set()
                thread.join(timeout=3)

    def test_timeout(self):
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_child('import time; time.sleep(30)', timeout=0.2)
        self.assertLess(time.monotonic() - started, 2)

    def test_parent_loss_during_execution(self):
        parent = os.getppid()
        with patch.object(worker.os, 'getppid', side_effect=[parent, parent, parent + 1]):
            with self.assertRaises(InterruptedError):
                worker.run_cancellable([sys.executable, '-c', 'import time; time.sleep(30)'], threading.Event(), parent, 3)

    def test_output_bound(self):
        with patch.object(worker, 'MAX_OUTPUT_BYTES', 1024):
            with self.assertRaisesRegex(ValueError, 'safety limit'):
                self.run_child('import sys; sys.stdout.write("x" * 100000)')

    def test_already_cancelled_does_not_spawn(self):
        event = threading.Event()
        event.set()
        with patch.object(worker.subprocess, 'Popen') as launch:
            with self.assertRaises(InterruptedError):
                self.run_child('pass', event)
            launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
