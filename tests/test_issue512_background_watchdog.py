"""Regression coverage for silent background-child timeout handling (Issue #512)."""

import subprocess
import sys
import time

from agent_manager import _start_background_timeout_watchdog


def test_silent_child_is_terminated_without_stdout_activity():
    """The watchdog must fire even when stdout iteration would block forever."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    finished, timed_out, watchdog = _start_background_timeout_watchdog(proc, 0.15)
    try:
        proc.wait(timeout=3)
        assert timed_out.is_set()
        assert proc.returncode is not None
    finally:
        finished.set()
        if proc.poll() is None:
            proc.kill()
        watchdog.join(timeout=1)
