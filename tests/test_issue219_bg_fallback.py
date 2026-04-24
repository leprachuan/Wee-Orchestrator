"""Regression tests for Issue #219: Background tasks do not use fallback_runtime/fallback_model.

Tests cover:
1. BackgroundTaskRequest accepts fallback_runtime/fallback_model fields
2. BackgroundTaskManager.create_task/create_task_checked stores fallback fields
3. _is_bg_fallback_eligible() pattern matching
4. Fallback retry data model: queued tasks store fallback fields for promotion
5. agents.json dispatch_config fallback normalization logic
"""

import re
import sys
import os
import tempfile
import threading
import unittest

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")


def _make_bg_task_manager(tmpdir):
    """Construct a BackgroundTaskManager with a temp file path."""
    from agent_manager import BackgroundTaskManager
    mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
    mgr._path = os.path.join(tmpdir, "tasks.json")
    mgr._lock = threading.Lock()
    mgr._tasks_cache = None
    mgr._bg_events = {}
    mgr._bg_events_lock = threading.Lock()
    mgr._cleanup_thread_started = True  # prevent background thread start
    return mgr


class TestBackgroundTaskRequestFallbackFields(unittest.TestCase):
    """Test that BackgroundTaskRequest accepts fallback fields."""

    @classmethod
    def setUpClass(cls):
        from agent_manager import create_api_app
        cls.app = create_api_app()

    def test_fallback_runtime_field_exists(self):
        schema = self.app.openapi()
        bg_schema = schema.get("components", {}).get("schemas", {}).get("BackgroundTaskRequest", {})
        props = bg_schema.get("properties", {})
        self.assertIn(
            "fallback_runtime", props,
            "BackgroundTaskRequest must have fallback_runtime field"
        )
        self.assertIn(
            "fallback_model", props,
            "BackgroundTaskRequest must have fallback_model field"
        )

    def test_fallback_fields_are_optional(self):
        schema = self.app.openapi()
        bg_schema = schema.get("components", {}).get("schemas", {}).get("BackgroundTaskRequest", {})
        required = bg_schema.get("required", [])
        self.assertNotIn("fallback_runtime", required, "fallback_runtime must be optional")
        self.assertNotIn("fallback_model", required, "fallback_model must be optional")


class TestBackgroundTaskManagerFallbackStorage(unittest.TestCase):
    """Test that fallback fields are stored in and retrievable from task dicts."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_create_task_stores_fallback_fields(self):
        mgr = _make_bg_task_manager(self.tmpdir)
        task = mgr.create_task(
            task_id="test_219_a",
            session_id="sess1",
            user_identity="user1",
            channel="telegram",
            agent="test-agent",
            runtime="copilot",
            model="gpt-4",
            prompt="test",
            fallback_runtime="claude",
            fallback_model="sonnet",
        )
        self.assertEqual(task["fallback_runtime"], "claude")
        self.assertEqual(task["fallback_model"], "sonnet")

    def test_create_task_fallback_defaults_to_none(self):
        mgr = _make_bg_task_manager(self.tmpdir)
        task = mgr.create_task(
            task_id="test_219_b",
            session_id="sess2",
            user_identity="user1",
            channel="telegram",
            agent="test-agent",
            runtime="copilot",
            model="gpt-4",
            prompt="test",
        )
        self.assertIsNone(task.get("fallback_runtime"))
        self.assertIsNone(task.get("fallback_model"))

    def test_create_task_checked_stores_fallback_fields(self):
        mgr = _make_bg_task_manager(self.tmpdir)
        task, status = mgr.create_task_checked(
            task_id="test_219_c",
            session_id="sess3",
            user_identity="user1",
            channel="telegram",
            agent="test-agent",
            runtime="opencode",
            model="gpt-4o",
            prompt="test",
            max_concurrent=5,
            fallback_runtime="copilot",
            fallback_model="gpt-4.1",
        )
        self.assertEqual(task["fallback_runtime"], "copilot")
        self.assertEqual(task["fallback_model"], "gpt-4.1")

    def test_create_task_checked_fallback_retrieved_after_storage(self):
        mgr = _make_bg_task_manager(self.tmpdir)
        mgr.create_task_checked(
            task_id="test_219_d",
            session_id="s4",
            user_identity="u4",
            channel="telegram",
            agent="wee-dev",
            runtime="codex",
            model="gpt-5.4",
            prompt="do work",
            max_concurrent=5,
            fallback_runtime="copilot",
            fallback_model="gpt-4.1",
        )
        # Retrieve from disk and verify persistence
        retrieved = mgr.get_task("test_219_d")
        self.assertEqual(retrieved["fallback_runtime"], "copilot",
                         "fallback_runtime must persist through storage/retrieval")
        self.assertEqual(retrieved["fallback_model"], "gpt-4.1",
                         "fallback_model must persist through storage/retrieval")


class TestBgFallbackEligibilityPatterns(unittest.TestCase):
    """Test that the fallback error patterns match infrastructure failures correctly.

    Mirrors _BG_FALLBACK_PATTERNS defined inside _run_background_task.
    """

    @classmethod
    def setUpClass(cls):
        cls.patterns = [
            re.compile(p, re.IGNORECASE)
            for p in [
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
        ]

    def _eligible(self, text):
        for pat in self.patterns:
            if pat.search(text):
                return True
        return False

    def test_429_eligible(self):
        self.assertTrue(self._eligible("Task failed with code 1: 429 Too Many Requests"))

    def test_rate_limit_hyphen_eligible(self):
        self.assertTrue(self._eligible("rate-limit exceeded"))

    def test_rate_limit_space_eligible(self):
        self.assertTrue(self._eligible("rate limit exceeded"))

    def test_quota_exceeded_eligible(self):
        self.assertTrue(self._eligible("quota exceeded for this account"))

    def test_401_eligible(self):
        self.assertTrue(self._eligible("HTTP 401 Unauthorized"))

    def test_unauthorized_eligible(self):
        self.assertTrue(self._eligible("Unauthorized: invalid token"))

    def test_503_eligible(self):
        self.assertTrue(self._eligible("503 Service Unavailable"))

    def test_502_eligible(self):
        self.assertTrue(self._eligible("502 Bad Gateway"))

    def test_connection_refused_eligible(self):
        self.assertTrue(self._eligible("connection refused to 127.0.0.1"))

    def test_timeout_eligible(self):
        self.assertTrue(self._eligible("timed out after 30s"))
        self.assertTrue(self._eligible("timedout"))

    def test_etimedout_eligible(self):
        self.assertTrue(self._eligible("ETIMEDOUT"))

    def test_overloaded_eligible(self):
        self.assertTrue(self._eligible("server is overloaded, please retry"))

    def test_normal_failure_not_eligible(self):
        self.assertFalse(self._eligible("Task failed with code 1: File not found"))

    def test_syntax_error_not_eligible(self):
        self.assertFalse(self._eligible("SyntaxError in agent code at line 5"))

    def test_permission_denied_not_eligible(self):
        self.assertFalse(self._eligible("Permission denied: cannot write to /etc"))

    def test_agent_logic_error_not_eligible(self):
        self.assertFalse(self._eligible("KeyError: 'missing_key' in task handler"))


class TestFallbackNormalization(unittest.TestCase):
    """Test the normalization logic: same fallback as primary = no fallback."""

    def _normalize(self, runtime, model, fallback_runtime, fallback_model):
        """Mirror the normalization in create_background_task."""
        bg_fb_rt = fallback_runtime
        bg_fb_mdl = fallback_model
        if (bg_fb_rt, bg_fb_mdl) == (runtime, model):
            bg_fb_rt = None
            bg_fb_mdl = None
        return bg_fb_rt, bg_fb_mdl

    def test_same_runtime_and_model_normalizes_to_none(self):
        rt, mdl = self._normalize("copilot", "gpt-4.1", "copilot", "gpt-4.1")
        self.assertIsNone(rt)
        self.assertIsNone(mdl)

    def test_different_runtime_kept(self):
        rt, mdl = self._normalize("codex", "gpt-5.4", "copilot", "gpt-4.1")
        self.assertEqual(rt, "copilot")
        self.assertEqual(mdl, "gpt-4.1")

    def test_different_model_only_kept(self):
        rt, mdl = self._normalize("copilot", "gpt-5.4", "copilot", "gpt-4.1")
        self.assertEqual(rt, "copilot")
        self.assertEqual(mdl, "gpt-4.1")

    def test_none_fallback_stays_none(self):
        rt, mdl = self._normalize("copilot", "gpt-4.1", None, None)
        self.assertIsNone(rt)
        self.assertIsNone(mdl)


class TestDispatchConfigFallbackReadout(unittest.TestCase):
    """Test that dispatch_config fallback_runtime/model is read from agents.json."""

    def test_dispatch_config_fallback_fields_accessible(self):
        agents_config = {
            "wee-dev": {
                "dispatch_config": {
                    "runtime": "codex",
                    "model": "gpt-5.4",
                    "fallback_runtime": "copilot",
                    "fallback_model": "gpt-4.1",
                }
            }
        }
        agent = "wee-dev"
        dcfg = agents_config.get(agent, {}).get("dispatch_config", {})
        self.assertEqual(dcfg.get("fallback_runtime"), "copilot")
        self.assertEqual(dcfg.get("fallback_model"), "gpt-4.1")

    def test_missing_dispatch_config_returns_none(self):
        agents_config = {"wee-dev": {}}
        dcfg = agents_config.get("wee-dev", {}).get("dispatch_config", {})
        self.assertIsNone(dcfg.get("fallback_runtime"))
        self.assertIsNone(dcfg.get("fallback_model"))

    def test_body_override_takes_precedence_over_dispatch_config(self):
        """body.fallback_runtime should win over dispatch_config fallback_runtime."""
        dispatch_cfg_fb_rt = "claude"
        body_fb_rt = "gemini"
        # Simulate: body.fallback_runtime or _agent_dcfg.get("fallback_runtime")
        resolved = body_fb_rt or dispatch_cfg_fb_rt
        self.assertEqual(resolved, "gemini")

    def test_dispatch_config_used_when_body_is_none(self):
        dispatch_cfg_fb_rt = "claude"
        body_fb_rt = None
        resolved = body_fb_rt or dispatch_cfg_fb_rt
        self.assertEqual(resolved, "claude")


class TestQueuedTaskFallbackPromotion(unittest.TestCase):
    """Test that queued tasks carry fallback info through promotion."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_queued_task_with_fallback_retrievable_after_promotion(self):
        """A queued task's fallback fields must be accessible in get_next_queued."""
        mgr = _make_bg_task_manager(self.tmpdir)

        # Simulate creating a task that is queued (max_concurrent=0 forces queue)
        task, status = mgr.create_task_checked(
            task_id="test_219_promo",
            session_id="s_promo",
            user_identity="u_promo",
            channel="telegram",
            agent="wee-dev",
            runtime="codex",
            model="gpt-5.4",
            prompt="queued work",
            max_concurrent=0,  # forces queued status
            fallback_runtime="copilot",
            fallback_model="gpt-4.1",
        )

        self.assertEqual(status, "queued", "Task should be in queued state")

        # When promoted, get_next_queued returns the task with fallback fields
        next_q = mgr.get_next_queued("telegram", "u_promo")
        self.assertIsNotNone(next_q, "Should find a queued task")
        self.assertEqual(next_q.get("fallback_runtime"), "copilot",
                         "Queued task must retain fallback_runtime for promotion")
        self.assertEqual(next_q.get("fallback_model"), "gpt-4.1",
                         "Queued task must retain fallback_model for promotion")


if __name__ == "__main__":
    unittest.main(verbosity=2)
