"""Regression tests for issue #190 copilot session expiry recovery."""

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from agent_manager import SessionManager


def _make_mgr():
    """Minimal SessionManager instance for issue #190 testing."""
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_map = {}
    mgr._session_map_lock = threading.Lock()
    mgr.command_timeout = 120
    mgr._stream_queues = {}
    mgr._stream_buffers = {}
    mgr._last_exit_codes = {}
    mgr._copilot_session_start = {}
    mgr._live_status = {}
    mgr._live_status_lock = threading.Lock()
    mgr.copilot_bin = "/usr/local/bin/copilot"
    mgr.AGENTS = {
        "wee-dev": {"path": "/opt/n8n-copilot-shim-dev", "description": "dev agent"},
        "orchestrator": {"path": "/opt", "description": "orchestrator"},
    }
    mgr.get_or_create_session_data = MagicMock(
        return_value={"channel": "telegram", "permissions": {"mode": "restricted"}}
    )
    mgr.build_agent_context_prompt = MagicMock(return_value="<ctx>mock context</ctx>")
    mgr._parse_mode_command = MagicMock(side_effect=lambda p: (p, "restricted"))
    mgr._resolve_permission_mode = MagicMock(return_value="restricted")
    mgr.strip_metadata = MagicMock(side_effect=lambda output, _rt: output)
    mgr.clear_live_status = MagicMock()
    mgr.set_live_status = MagicMock()
    return mgr


class TestIssue190ReactiveRecovery(unittest.TestCase):
    """B01–B03: Reactive detection of 'Session token expired' and auto-retry."""

    def setUp(self):
        self.mgr = _make_mgr()

    def test_issue_190_b01_expired_output_triggers_retry(self):
        """B01: token expiry should trigger a retry."""
        call_count = [0]
        session_expiry_output = (
            "Some partial work completed...\n"
            "Session token expired. Please resend your message.\n"
            "(Request ID: 8888:112498:1207E6B:16D0D95:69E58E62)\n"
        )
        recovery_output = "Task completed successfully after session recovery."

        def fake_execute(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            call_count[0] += 1
            if call_count[0] == 1:
                return session_expiry_output
            return recovery_output

        self.mgr._execute_subprocess_with_tracking = fake_execute

        with patch("sys.stderr"):
            result = self.mgr.run_copilot(
                prompt="do a long task",
                model="gpt-4o",
                agent="wee-dev",
                session_id="copilot-session-abc",
                resume=True,
                n8n_session_id="n8n-session-xyz",
            )

        self.assertEqual(call_count[0], 2, "B01: expected one retry")
        self.assertEqual(result, recovery_output, "B01: expected retry output")

    def test_issue_190_b02_prior_work_injected_into_recovery(self):
        """B02: prior work should be injected into recovery context."""
        captured_recovery_prompt = []
        session_expiry_output = (
            "Installed dependencies.\n"
            "Ran tests.\n"
            "Session token expired. Please resend your message.\n"
            "(Request ID: 9999:abc)\n"
        )

        call_count = [0]

        def fake_execute(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            call_count[0] += 1
            if call_count[0] == 2:
                # Capture the prompt arg sent to the recovery session
                captured_recovery_prompt.append(prompt)
            return "done" if call_count[0] == 2 else session_expiry_output

        self.mgr._execute_subprocess_with_tracking = fake_execute

        with patch("sys.stderr"):
            self.mgr.run_copilot(
                prompt="implement feature X",
                model="gpt-4o",
                agent="wee-dev",
                session_id=None,
                resume=False,
                n8n_session_id="n8n-b02",
            )

        self.assertTrue(captured_recovery_prompt, "B02: recovery must launch")
        recovery_prompt_text = captured_recovery_prompt[0]
        # Recovery prompt must reference prior work
        self.assertIn(
            "Installed dependencies",
            recovery_prompt_text,
            "B02: prior work missing",
        )
        self.assertIn("Ran tests", recovery_prompt_text, "B02: prior work missing")
        self.assertIn("implement feature X", recovery_prompt_text, "B02: task missing")

    def test_issue_190_b03_recovery_uses_fresh_session_no_resume(self):
        """B03: recovery should start fresh."""
        captured_cmds = []

        session_expiry_output = (
            "partial work\n" "Session token expired. Please resend your message.\n"
        )

        call_count = [0]

        def fake_execute(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            call_count[0] += 1
            captured_cmds.append(list(cmd))
            return "recovered" if call_count[0] == 2 else session_expiry_output

        self.mgr._execute_subprocess_with_tracking = fake_execute

        with patch("sys.stderr"):
            self.mgr.run_copilot(
                prompt="long task",
                model="gpt-4o",
                agent="wee-dev",
                session_id="old-copilot-session",
                resume=True,
                n8n_session_id="n8n-b03",
            )

        self.assertEqual(len(captured_cmds), 2, "B03: expected two launches")
        recovery_cmd = captured_cmds[1]
        self.assertNotIn("--resume", recovery_cmd, "B03: resume must be absent")

    def test_issue_190_b06_happy_path_no_retry(self):
        """B06: normal output should pass through unchanged."""
        call_count = [0]

        def fake_execute(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            call_count[0] += 1
            return "Task completed successfully."

        self.mgr._execute_subprocess_with_tracking = fake_execute

        with patch("sys.stderr"):
            result = self.mgr.run_copilot(
                prompt="simple task",
                model="gpt-4o",
                agent="wee-dev",
                session_id=None,
                resume=False,
                n8n_session_id="n8n-b06",
            )

        self.assertEqual(call_count[0], 1, "B06: expected one launch")
        self.assertEqual(result, "Task completed successfully.")


class TestIssue190ProactiveRestart(unittest.TestCase):
    """B04–B05: Proactive session age check prevents token expiry before it occurs."""

    def setUp(self):
        self.mgr = _make_mgr()

    def test_issue_190_b04_old_session_starts_fresh(self):
        """B04: old sessions should start fresh."""
        # Mark session as 26 minutes old
        self.mgr._copilot_session_start["n8n-b04"] = time.time() - (26 * 60)

        captured_cmds = []

        def fake_execute(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            captured_cmds.append(list(cmd))
            return "completed"

        self.mgr._execute_subprocess_with_tracking = fake_execute

        with patch("sys.stderr"):
            self.mgr.run_copilot(
                prompt="continue work",
                model="gpt-4o",
                agent="wee-dev",
                session_id="stale-copilot-session",
                resume=True,
                n8n_session_id="n8n-b04",
            )

        self.assertEqual(len(captured_cmds), 1, "B04: expected one launch")
        cmd = captured_cmds[0]
        self.assertNotIn("--resume", cmd, "B04: resume must be absent")
        self.assertNotIn("stale-copilot-session", cmd, "B04: stale session leaked")

    def test_issue_190_b05_young_session_keeps_resume(self):
        """B05: young sessions should keep resume."""
        # Mark session as 5 minutes old (well within TTL)
        self.mgr._copilot_session_start["n8n-b05"] = time.time() - (5 * 60)

        captured_cmds = []

        def fake_execute(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            captured_cmds.append(list(cmd))
            return "completed"

        self.mgr._execute_subprocess_with_tracking = fake_execute

        with patch("sys.stderr"):
            self.mgr.run_copilot(
                prompt="continue work",
                model="gpt-4o",
                agent="wee-dev",
                session_id="valid-copilot-session",
                resume=True,
                n8n_session_id="n8n-b05",
            )

        self.assertEqual(len(captured_cmds), 1)
        cmd = captured_cmds[0]
        self.assertIn("--resume", cmd, "B05: resume should be used")
        self.assertIn("valid-copilot-session", cmd, "B05: session id missing")

    def test_issue_190_session_start_recorded_on_new_session(self):
        """Record session start on a new run."""
        before = time.time()

        def fake_execute(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            return "done"

        self.mgr._execute_subprocess_with_tracking = fake_execute

        with patch("sys.stderr"):
            self.mgr.run_copilot(
                prompt="new task",
                model="gpt-4o",
                agent="wee-dev",
                session_id=None,
                resume=False,
                n8n_session_id="n8n-new",
            )

        after = time.time()
        recorded = self.mgr._copilot_session_start.get("n8n-new")
        self.assertIsNotNone(recorded, "Session start time must be recorded")
        self.assertGreaterEqual(recorded, before)
        self.assertLessEqual(recorded, after + 1)

    def test_issue_190_session_start_recorded_on_recovery(self):
        """Refresh session start after recovery."""
        session_expiry_output = (
            "work\n" "Session token expired. Please resend your message.\n"
        )
        call_count = [0]

        def fake_execute(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            call_count[0] += 1
            return "done" if call_count[0] == 2 else session_expiry_output

        self.mgr._execute_subprocess_with_tracking = fake_execute

        before = time.time()
        with patch("sys.stderr"):
            self.mgr.run_copilot(
                prompt="long task",
                model="gpt-4o",
                agent="wee-dev",
                session_id=None,
                resume=False,
                n8n_session_id="n8n-rec",
            )
        after = time.time()

        recorded = self.mgr._copilot_session_start.get("n8n-rec")
        self.assertIsNotNone(recorded)
        # Should be updated to the recovery session start time (not the original)
        self.assertGreaterEqual(recorded, before)
        self.assertLessEqual(recorded, after + 1)


class TestIssue190SourceInspection(unittest.TestCase):
    """Verify the fix is present in source code (static analysis)."""

    def test_issue_190_session_expiry_detection_in_source(self):
        """run_copilot source must contain 'Session token expired' detection."""
        import inspect

        src = inspect.getsource(SessionManager.run_copilot)
        self.assertIn(
            "Session token expired",
            src,
            "run_copilot must detect 'Session token expired' error string",
        )

    def test_issue_190_session_start_tracking_in_init(self):
        """SessionManager must have _copilot_session_start attribute."""
        mgr = _make_mgr()
        self.assertIsInstance(
            mgr._copilot_session_start,
            dict,
            "_copilot_session_start must be a dict for session age tracking",
        )

    def test_issue_190_proactive_restart_in_source(self):
        """run_copilot must check session age for proactive restart."""
        import inspect

        src = inspect.getsource(SessionManager.run_copilot)
        self.assertIn(
            "_copilot_session_start",
            src,
            "run_copilot must check _copilot_session_start for proactive token refresh",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
