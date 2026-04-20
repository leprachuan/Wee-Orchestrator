"""Regression tests for Issue #159: Fallback Runtime/Model for Scheduled Tasks."""

import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")


class TestFallbackEligibility(unittest.TestCase):
    """Test _is_fallback_eligible() pattern matching."""

    @classmethod
    def setUpClass(cls):
        from scheduler.executor import TaskSchedulerExecutor

        cls.executor = TaskSchedulerExecutor.__new__(TaskSchedulerExecutor)

    def test_rate_limit_429_eligible(self):
        self.assertTrue(
            self.executor._is_fallback_eligible("Error: 429 Too Many Requests")
        )

    def test_rate_limit_text_eligible(self):
        self.assertTrue(
            self.executor._is_fallback_eligible("rate limit exceeded, try again later")
        )

    def test_quota_exceeded_eligible(self):
        self.assertTrue(
            self.executor._is_fallback_eligible(
                "Error: quota exceeded for this billing period"
            )
        )

    def test_auth_401_eligible(self):
        self.assertTrue(self.executor._is_fallback_eligible("HTTP 401 Unauthorized"))

    def test_auth_missing_eligible(self):
        self.assertTrue(
            self.executor._is_fallback_eligible("Missing Authentication header")
        )

    def test_service_unavailable_503_eligible(self):
        self.assertTrue(self.executor._is_fallback_eligible("503 Service Unavailable"))

    def test_bad_gateway_502_eligible(self):
        self.assertTrue(self.executor._is_fallback_eligible("502 Bad Gateway"))

    def test_connection_refused_eligible(self):
        self.assertTrue(
            self.executor._is_fallback_eligible("Connection refused to endpoint")
        )

    def test_timeout_eligible(self):
        self.assertTrue(
            self.executor._is_fallback_eligible("timed out waiting for response")
        )

    def test_overloaded_eligible(self):
        self.assertTrue(
            self.executor._is_fallback_eligible("The server is overloaded right now")
        )

    def test_task_logic_error_not_eligible(self):
        self.assertFalse(
            self.executor._is_fallback_eligible("NameError: name 'x' is not defined")
        )

    def test_empty_error_not_eligible(self):
        self.assertFalse(self.executor._is_fallback_eligible(""))

    def test_none_error_not_eligible(self):
        self.assertFalse(self.executor._is_fallback_eligible(None))

    def test_normal_failure_not_eligible(self):
        self.assertFalse(
            self.executor._is_fallback_eligible(
                "Task completed with errors: missing file"
            )
        )

    def test_api_key_invalid_eligible(self):
        self.assertTrue(
            self.executor._is_fallback_eligible("Error: api_key invalid or expired")
        )

    def test_case_insensitive(self):
        self.assertTrue(self.executor._is_fallback_eligible("RATE LIMIT EXCEEDED"))
        self.assertTrue(self.executor._is_fallback_eligible("Quota Exceeded"))


class TestResolveFallback(unittest.TestCase):
    """Test _resolve_fallback() priority: per-job > global env > None."""

    @classmethod
    def setUpClass(cls):
        from scheduler.executor import TaskSchedulerExecutor

        cls.executor = TaskSchedulerExecutor.__new__(TaskSchedulerExecutor)

    def test_per_job_fallback(self):
        job = {
            "runtime": "copilot",
            "model": "claude-opus-4.6",
            "fallback_runtime": "claude",
            "fallback_model": "claude-haiku-4.5",
        }
        rt, model = self.executor._resolve_fallback(job)
        self.assertEqual(rt, "claude")
        self.assertEqual(model, "claude-haiku-4.5")

    def test_per_job_runtime_only(self):
        job = {
            "runtime": "copilot",
            "model": "claude-opus-4.6",
            "fallback_runtime": "claude",
        }
        rt, model = self.executor._resolve_fallback(job)
        self.assertEqual(rt, "claude")
        self.assertIsNone(model)

    def test_per_job_model_only(self):
        job = {
            "runtime": "copilot",
            "model": "claude-opus-4.6",
            "fallback_model": "claude-haiku-4.5",
        }
        rt, model = self.executor._resolve_fallback(job)
        self.assertIsNone(rt)
        self.assertEqual(model, "claude-haiku-4.5")

    @patch.dict(
        os.environ,
        {
            "SCHEDULER_FALLBACK_RUNTIME": "claude",
            "SCHEDULER_FALLBACK_MODEL": "claude-sonnet-4.6",
        },
    )
    def test_global_env_fallback(self):
        job = {"runtime": "copilot", "model": "claude-opus-4.6"}
        rt, model = self.executor._resolve_fallback(job)
        self.assertEqual(rt, "claude")
        self.assertEqual(model, "claude-sonnet-4.6")

    @patch.dict(os.environ, {"SCHEDULER_FALLBACK_RUNTIME": "claude"})
    def test_per_job_overrides_global(self):
        job = {
            "runtime": "copilot",
            "model": "claude-opus-4.6",
            "fallback_runtime": "opencode",
            "fallback_model": "gpt-5.4",
        }
        rt, model = self.executor._resolve_fallback(job)
        self.assertEqual(rt, "opencode")
        self.assertEqual(model, "gpt-5.4")

    def test_no_fallback_configured(self):
        job = {"runtime": "copilot", "model": "claude-opus-4.6"}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SCHEDULER_FALLBACK_RUNTIME", None)
            os.environ.pop("SCHEDULER_FALLBACK_MODEL", None)
            rt, model = self.executor._resolve_fallback(job)
            self.assertIsNone(rt)
            self.assertIsNone(model)

    def test_same_runtime_model_returns_none(self):
        job = {
            "runtime": "copilot",
            "model": "claude-opus-4.6",
            "fallback_runtime": "copilot",
            "fallback_model": "claude-opus-4.6",
        }
        rt, model = self.executor._resolve_fallback(job)
        self.assertIsNone(rt)
        self.assertIsNone(model)


class TestExecuteAiModeFallback(unittest.TestCase):
    """Test _execute_ai_mode() fallback orchestration."""

    @classmethod
    def setUpClass(cls):
        from scheduler.executor import TaskSchedulerExecutor

        cls.ExecutorClass = TaskSchedulerExecutor

    def _make_executor(self):
        ex = self.ExecutorClass.__new__(self.ExecutorClass)
        ex.jobs_file = Path("/tmp/test_jobs_159.json")
        ex.repo_root = Path("/opt/n8n-copilot-shim-dev")
        ex.config_file = Path("/opt/n8n-copilot-shim-dev/agents.json")
        ex.logger = MagicMock()
        ex.config = MagicMock()
        ex.config.get.return_value = None
        ex._save_result = MagicMock()
        ex._notify_creator = MagicMock()
        ex._log_job = MagicMock()
        ex._write_checkpoint = MagicMock()
        ex._clear_checkpoint = MagicMock()
        return ex

    def _make_job(self, **overrides):
        job = {
            "id": "test-159",
            "name": "Test Fallback",
            "agent": "orchestrator",
            "runtime": "copilot",
            "model": "claude-opus-4.6",
            "mode": "ai",
            "task": "Say hello",
            "retries": 0,
            "enabled": True,
            "timeout": 60,
            "notify": False,
        }
        job.update(overrides)
        return job

    def test_primary_succeeds_no_fallback(self):
        """When primary succeeds, no fallback is attempted."""
        ex = self._make_executor()
        job = self._make_job(
            fallback_runtime="claude", fallback_model="claude-haiku-4.5"
        )

        call_count = [0]

        def mock_attempt(j, runtime_override=None, model_override=None):
            call_count[0] += 1
            return ("ok", None)

        ex._run_ai_attempt = mock_attempt
        result = ex._execute_ai_mode(job)
        self.assertEqual(call_count[0], 1)
        self.assertEqual(result, "ok")

    def test_fallback_triggered_on_rate_limit(self):
        """When primary fails with rate limit and fallback is configured, fallback runs."""  # noqa: E501
        ex = self._make_executor()
        job = self._make_job(
            fallback_runtime="claude", fallback_model="claude-haiku-4.5"
        )

        call_count = [0]

        def mock_attempt(j, runtime_override=None, model_override=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return (None, "429 Too Many Requests")
            return ("ok from fallback", None)

        ex._run_ai_attempt = mock_attempt
        result = ex._execute_ai_mode(job)
        self.assertEqual(call_count[0], 2)
        self.assertEqual(result, "ok from fallback")

    def test_no_fallback_on_logic_error(self):
        """Task logic errors should NOT trigger fallback."""
        ex = self._make_executor()
        job = self._make_job(
            fallback_runtime="claude", fallback_model="claude-haiku-4.5"
        )

        call_count = [0]

        def mock_attempt(j, runtime_override=None, model_override=None):
            call_count[0] += 1
            return (None, "NameError: name 'x' is not defined")

        ex._run_ai_attempt = mock_attempt
        result = ex._execute_ai_mode(job)
        self.assertEqual(call_count[0], 1)
        self.assertIsNone(result)

    def test_no_fallback_when_not_configured(self):
        """Without fallback config, no retry on eligible error."""
        ex = self._make_executor()
        job = self._make_job()  # No fallback fields

        call_count = [0]

        def mock_attempt(j, runtime_override=None, model_override=None):
            call_count[0] += 1
            return (None, "429 Too Many Requests")

        ex._run_ai_attempt = mock_attempt

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SCHEDULER_FALLBACK_RUNTIME", None)
            os.environ.pop("SCHEDULER_FALLBACK_MODEL", None)
            result = ex._execute_ai_mode(job)
        self.assertEqual(call_count[0], 1)
        self.assertIsNone(result)

    def test_both_attempts_fail(self):
        """When both primary and fallback fail, combined error is captured."""
        ex = self._make_executor()
        job = self._make_job(
            fallback_runtime="claude",
            fallback_model="claude-haiku-4.5",
            notify=True,
        )

        call_count = [0]

        def mock_attempt(j, runtime_override=None, model_override=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return (None, "503 Service Unavailable")
            return (None, "Connection refused")

        ex._run_ai_attempt = mock_attempt
        result = ex._execute_ai_mode(job)
        self.assertEqual(call_count[0], 2)
        self.assertIsNone(result)
        # _save_result should have been called with combined error
        ex._save_result.assert_called()
        error_arg = (
            ex._save_result.call_args[1].get("error") or ex._save_result.call_args[0][3]
        )
        self.assertIn("Primary", error_arg)
        self.assertIn("Fallback", error_arg)

    def test_notification_on_fallback_success(self):
        """Verify notification sent when fallback succeeds."""
        ex = self._make_executor()
        job = self._make_job(
            fallback_runtime="claude",
            fallback_model="claude-haiku-4.5",
            notify=True,
        )

        call_count = [0]

        def mock_attempt(j, runtime_override=None, model_override=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return (None, "rate limit exceeded")
            return ("fallback ok", None)

        ex._run_ai_attempt = mock_attempt
        result = ex._execute_ai_mode(job)
        self.assertEqual(result, "fallback ok")
        ex._notify_creator.assert_called_once()


class TestManagementFallbackFields(unittest.TestCase):
    """Test management.py accepts fallback_runtime/fallback_model."""

    def test_schedule_task_with_fallback(self):
        from scheduler.management import TaskScheduler

        sched = TaskScheduler.__new__(TaskScheduler)
        sched.jobs = []
        sched.jobs_file = Path("/tmp/test_mgmt_159.json")
        sched.logger = MagicMock()
        sched._log = MagicMock()
        sched._save_jobs = MagicMock()
        sched._calculate_next_run = MagicMock(
            return_value=datetime.now() + timedelta(hours=1)
        )
        sched._parse_to_cron = MagicMock(return_value="0 9 * * *")

        result = sched.schedule_task(
            name="Test Fallback Task",
            schedule="every day at 9am",
            agent="orchestrator",
            runtime="copilot",
            model="claude-opus-4.6",
            task="Say hello",
            fallback_runtime="claude",
            fallback_model="claude-haiku-4.5",
        )

        self.assertIn("result", result)
        job = result["result"]
        self.assertEqual(job["fallback_runtime"], "claude")
        self.assertEqual(job["fallback_model"], "claude-haiku-4.5")

    def test_schedule_task_without_fallback(self):
        from scheduler.management import TaskScheduler

        sched = TaskScheduler.__new__(TaskScheduler)
        sched.jobs = []
        sched.jobs_file = Path("/tmp/test_mgmt_159b.json")
        sched.logger = MagicMock()
        sched._log = MagicMock()
        sched._save_jobs = MagicMock()
        sched._calculate_next_run = MagicMock(
            return_value=datetime.now() + timedelta(hours=1)
        )
        sched._parse_to_cron = MagicMock(return_value="0 9 * * *")

        result = sched.schedule_task(
            name="Test No Fallback",
            schedule="every day at 9am",
            task="Say hello",
        )

        self.assertIn("result", result)
        job = result["result"]
        self.assertIsNone(job.get("fallback_runtime"))
        self.assertIsNone(job.get("fallback_model"))

    def test_update_job_fallback_fields_allowed(self):
        from scheduler.management import TaskScheduler

        sched = TaskScheduler.__new__(TaskScheduler)
        sched.jobs = [
            {
                "id": "upd-159",
                "name": "Update Test",
                "runtime": "copilot",
                "model": None,
                "fallback_runtime": None,
                "fallback_model": None,
                "schedule": "every day",
                "cron": "0 0 * * *",
                "enabled": True,
            }
        ]
        sched.jobs_file = Path("/tmp/test_mgmt_upd_159.json")
        sched.logger = MagicMock()
        sched._log = MagicMock()
        sched._save_jobs = MagicMock()
        sched._load_jobs = MagicMock(return_value={"jobs": sched.jobs})
        sched._calculate_next_run = MagicMock(
            return_value=datetime.now() + timedelta(hours=1)
        )
        sched._parse_to_cron = MagicMock(return_value="0 0 * * *")

        result = sched.update_job(
            "upd-159",
            {"fallback_runtime": "claude", "fallback_model": "claude-sonnet-4.6"},
        )

        self.assertTrue(result.get("success", False))
        updated = sched.jobs[0]
        self.assertEqual(updated["fallback_runtime"], "claude")
        self.assertEqual(updated["fallback_model"], "claude-sonnet-4.6")


class TestPydanticModels(unittest.TestCase):
    """Test that Pydantic models accept fallback fields."""

    def test_schedule_job_request_has_fallback_fields(self):
        with open("/opt/n8n-copilot-shim-dev/agent_manager.py") as f:
            src = f.read()

        self.assertIn("fallback_runtime: Optional[str]", src)
        self.assertIn("fallback_model: Optional[str]", src)

        # Verify in UpdateJobRequest specifically
        idx = src.index("class UpdateJobRequest")
        chunk = src[idx : idx + 1000]
        self.assertIn("fallback_runtime", chunk)
        self.assertIn("fallback_model", chunk)

    def test_create_route_passes_fallback(self):
        with open("/opt/n8n-copilot-shim-dev/agent_manager.py") as f:
            src = f.read()

        self.assertIn("fallback_runtime=body.fallback_runtime", src)
        self.assertIn("fallback_model=body.fallback_model", src)


class TestFallbackPatternCompleteness(unittest.TestCase):
    """Ensure all documented failure modes are covered."""

    @classmethod
    def setUpClass(cls):
        from scheduler.executor import TaskSchedulerExecutor

        cls.executor = TaskSchedulerExecutor.__new__(TaskSchedulerExecutor)

    def test_all_documented_patterns(self):
        failures = [
            "429 Too Many Requests",
            "rate limit exceeded",
            "quota exceeded",
            "401 Unauthorized",
            "Missing Authentication",
            "api_key invalid",
            "503 Service Unavailable",
            "502 Bad Gateway",
            "Connection refused",
            "connection timed out",
            "ETIMEDOUT",
            "server is overloaded",
        ]
        for failure in failures:
            with self.subTest(failure=failure):
                self.assertTrue(
                    self.executor._is_fallback_eligible(failure),
                    f"Expected '{failure}' to be fallback-eligible",
                )


class TestWebUIFallbackFields(unittest.TestCase):
    """Test that WebUI app.js includes fallback fields."""

    def test_app_js_has_fallback_fields(self):
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.js") as f:
            src = f.read()

        self.assertIn("fallback_runtime", src)
        self.assertIn("fallback_model", src)
        self.assertIn("Fallback Configuration", src)
        self.assertIn("populateRuntimeDropdown", src)
        self.assertIn("populateFallbackModelDropdown", src)

    def test_form_submission_includes_fallback(self):
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.js") as f:
            src = f.read()

        self.assertIn("payload.fallback_runtime", src)
        self.assertIn("payload.fallback_model", src)


if __name__ == "__main__":
    unittest.main()
