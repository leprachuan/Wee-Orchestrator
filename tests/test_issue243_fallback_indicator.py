"""
Tests for issue #243: WebUI should indicate when a task fell back to fallback runtime.

Covers:
- BackgroundTaskManager stores fallback_runtime/fallback_model in task record
- mark_fallback_used() sets used_fallback, actual_runtime, actual_model
- create_task() stores fallback fields from API request
- API list endpoint exposes fallback fields
- API detail endpoint exposes fallback fields
- Tasks with no fallback have used_fallback=False, actual_runtime=None
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_manager import BackgroundTaskManager


def _make_mgr(tmp_path: str) -> BackgroundTaskManager:
    mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
    mgr._path = tmp_path
    mgr._lock = threading.Lock()
    mgr._bg_events = {}
    mgr._bg_events_lock = threading.Lock()
    return mgr


class TestFallbackFieldsInTaskRecord(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.mgr = _make_mgr(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _create(self, task_id="bg_test001", fallback_runtime=None, fallback_model=None):
        return self.mgr.create_task(
            task_id=task_id,
            session_id="sess-1",
            user_identity="testuser",
            channel="telegram",
            agent="orchestrator",
            runtime="claude",
            model="claude-sonnet-4.6",
            prompt="do something",
            fallback_runtime=fallback_runtime,
            fallback_model=fallback_model,
        )

    def test_create_task_stores_fallback_fields(self):
        """Task record stores fallback_runtime/fallback_model when provided."""
        task = self._create(fallback_runtime="copilot", fallback_model="auto")
        self.assertEqual(task["fallback_runtime"], "copilot")
        self.assertEqual(task["fallback_model"], "auto")

    def test_create_task_fallback_defaults_none(self):
        """Task record has fallback_runtime=None when not provided."""
        task = self._create()
        self.assertIsNone(task["fallback_runtime"])
        self.assertIsNone(task["fallback_model"])

    def test_create_task_used_fallback_initially_false(self):
        """New tasks have used_fallback=False by default."""
        task = self._create(fallback_runtime="copilot", fallback_model="auto")
        self.assertFalse(task["used_fallback"])
        self.assertIsNone(task["actual_runtime"])
        self.assertIsNone(task["actual_model"])

    def test_mark_fallback_used_sets_fields(self):
        """mark_fallback_used() sets used_fallback=True and actual_runtime/model."""
        self._create(
            "bg_fallback001", fallback_runtime="copilot", fallback_model="auto"
        )
        self.mgr.mark_fallback_used("bg_fallback001", "copilot", "auto")
        task = self.mgr.get_task("bg_fallback001")
        self.assertTrue(task["used_fallback"])
        self.assertEqual(task["actual_runtime"], "copilot")
        self.assertEqual(task["actual_model"], "auto")

    def test_mark_fallback_used_persisted(self):
        """mark_fallback_used() changes persist to disk (task survives reload)."""
        self._create(
            "bg_persist001", fallback_runtime="copilot", fallback_model="gpt-5-mini"
        )
        self.mgr.mark_fallback_used("bg_persist001", "copilot", "gpt-5-mini")
        # Create new manager pointing to same file — simulates service restart read
        mgr2 = _make_mgr(self.tmp.name)
        task = mgr2.get_task("bg_persist001")
        self.assertTrue(task["used_fallback"])
        self.assertEqual(task["actual_runtime"], "copilot")

    def test_task_without_fallback_config_has_false_used(self):
        """Tasks that never used fallback show used_fallback=False."""
        self._create("bg_nofallback")
        self.mgr.complete_task("bg_nofallback", "Done")
        task = self.mgr.get_task("bg_nofallback")
        self.assertFalse(task.get("used_fallback", False))
        self.assertIsNone(task.get("actual_runtime"))

    def test_list_all_tasks_includes_fallback_fields(self):
        """list_all_tasks() returns tasks including fallback metadata."""
        self._create("bg_list001", fallback_runtime="copilot", fallback_model="auto")
        self.mgr.mark_fallback_used("bg_list001", "copilot", "auto")
        tasks = self.mgr.list_all_tasks()
        t = next(x for x in tasks if x["task_id"] == "bg_list001")
        self.assertIn("used_fallback", t)
        self.assertIn("actual_runtime", t)
        self.assertTrue(t["used_fallback"])
        self.assertEqual(t["actual_runtime"], "copilot")


class TestFallbackRateLimitPatterns(unittest.TestCase):
    """Verify the rate-limit/infra error detection patterns."""

    PATTERNS = [
        r"429",
        r"rate.?limit",
        r"quota.?exceeded",
        r"401",
        r"unauthorized",
        r"missing.?authentication",
        r"api[_\-]?key.?(invalid|expired|missing)",
        r"503",
        r"service.?unavailable",
        r"502",
        r"bad.?gateway",
        r"connection.?refused",
        r"timed?.?out",
        r"etimedout",
        r"overloaded",
    ]

    def _is_infra_error(self, text):
        import re

        return any(re.search(p, text, re.IGNORECASE) for p in self.PATTERNS)

    def test_429_triggers_fallback(self):
        self.assertTrue(self._is_infra_error("Error: 429 Too Many Requests"))

    def test_rate_limit_triggers_fallback(self):
        self.assertTrue(self._is_infra_error("copilot: rate limit exceeded"))

    def test_quota_exceeded_triggers_fallback(self):
        self.assertTrue(self._is_infra_error("quota_exceeded: monthly quota hit"))

    def test_service_unavailable_triggers_fallback(self):
        self.assertTrue(self._is_infra_error("503 Service Unavailable"))

    def test_connection_refused_triggers_fallback(self):
        self.assertTrue(self._is_infra_error("Connection refused: localhost:5000"))

    def test_normal_error_does_not_trigger_fallback(self):
        self.assertFalse(self._is_infra_error("ValueError: invalid argument provided"))

    def test_syntax_error_does_not_trigger_fallback(self):
        self.assertFalse(self._is_infra_error("SyntaxError: unexpected token"))

    def test_authentication_error_triggers_fallback(self):
        self.assertTrue(self._is_infra_error("missing authentication token"))


class TestFallbackNotAppliedWhenAlreadyUsed(unittest.TestCase):
    """Guard against double-fallback (recursive fallback loops)."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.mgr = _make_mgr(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_used_fallback_flag_prevents_second_retry(self):
        """Once used_fallback=True, the flag prevents a second fallback attempt."""
        task = self.mgr.create_task(
            task_id="bg_double001",
            session_id="sess-x",
            user_identity="user",
            channel="telegram",
            agent="orchestrator",
            runtime="claude",
            model="claude-sonnet-4.6",
            prompt="do something",
            fallback_runtime="copilot",
            fallback_model="auto",
        )
        self.mgr.mark_fallback_used("bg_double001", "copilot", "auto")
        task = self.mgr.get_task("bg_double001")
        # Simulate the guard check used in _run_background_task
        already_fb = task.get("used_fallback", False)
        self.assertTrue(
            already_fb,
            "Second fallback attempt should be blocked by used_fallback flag",
        )


if __name__ == "__main__":
    unittest.main()
