"""Bounded subprocess execution for media inspection and subtitle extraction."""
import os
import selectors
import signal
import subprocess
import time

MAX_OUTPUT_BYTES = 2 * 1024 * 1024
POLL_SECONDS = 0.1


def _signal_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def _stop_group(process):
    _signal_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
    # Include descendants that kept running after the direct child exited.
    _signal_group(process, signal.SIGKILL)
    process.wait()


def run_cancellable(command, stopping, parent_pid, timeout, capture_output=True):
    """Return CompletedProcess with bytes; cancel via InterruptedError.

    Timeout raises subprocess.TimeoutExpired. Captured stdout and stderr share a
    2 MiB limit; overflow raises ValueError. Every child runs in its own process
    group so cancellation also stops its descendants. No output is persisted.
    """
    def check_cancelled():
        if stopping.is_set() or os.getppid() != parent_pid:
            raise InterruptedError('Media preparation cancelled.')

    check_cancelled()
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
                               stderr=subprocess.PIPE if capture_output else subprocess.DEVNULL,
                               start_new_session=True)
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    total = 0
    selector = selectors.DefaultSelector()
    try:
        if capture_output:
            for name in buffers:
                stream = getattr(process, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
        while True:
            check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            if selector.get_map():
                for key, _ in selector.select(min(POLL_SECONDS, remaining)):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                    else:
                        total += len(data)
                        if total > MAX_OUTPUT_BYTES:
                            raise ValueError('Media inspection output exceeds the safety limit.')
                        buffers[key.data].extend(data)
            elif process.poll() is None:
                stopping.wait(min(POLL_SECONDS, remaining))
            if process.poll() is not None and not selector.get_map():
                check_cancelled()
                return subprocess.CompletedProcess(command, process.returncode,
                    bytes(buffers['stdout']) if capture_output else None,
                    bytes(buffers['stderr']) if capture_output else None)
    finally:
        # Also remove descendants after normal completion of the direct child.
        _stop_group(process)
        selector.close()
        for stream in (process.stdout, process.stderr):
            if stream:
                stream.close()
