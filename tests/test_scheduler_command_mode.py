"""Tests for issue #78: command-mode tasks must not invoke LLM runtimes.

Tests _execute_task routing logic and the misconfiguration warning.
"""

import logging
import os
import sys
import unittest
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")
os.environ.setdefault("API_SHARED_KEY", "test_key_123")

from scheduler.executor import TaskSchedulerExecutor


class TestCommandModeRouting(unittest.TestCase):
    """Verify command-mode tasks always go to _execute_command_mode, never _execute_ai_mode."""

    def _make_executor(self):
        exec_ = TaskSchedulerExecutor.__new__(TaskSchedulerExecutor)
        exec_.logger = logging.getLogger("test")
        exec_._job_last_exec_mono = {}
        exec_._wall_clock_debt = 0.0
        return exec_

    def test_command_mode_calls_execute_command_mode(self):
        """mode='command' must route to _execute_command_mode, never _execute_ai_mode."""
        executor = self._make_executor()
        job = {"id": "cmd-job", "mode": "command", "task": "echo hello"}

        with patch.object(
            executor, "_execute_command_mode", return_value="ok"
        ) as mock_cmd, patch.object(executor, "_execute_ai_mode") as mock_ai:
            result = executor._execute_task(job)

        mock_cmd.assert_called_once_with(job)
        mock_ai.assert_not_called()
        self.assertEqual(result, "ok")

    def test_ai_mode_calls_execute_ai_mode(self):
        """mode='ai' (default) routes to _execute_ai_mode."""
        executor = self._make_executor()
        job = {"id": "ai-job", "mode": "ai", "task": "do something"}

        with patch.object(executor, "_execute_command_mode") as mock_cmd, patch.object(
            executor, "_execute_ai_mode", return_value="ai_result"
        ) as mock_ai:
            result = executor._execute_task(job)

        mock_cmd.assert_not_called()
        mock_ai.assert_called_once_with(job)
        self.assertEqual(result, "ai_result")

    def test_default_mode_is_ai(self):
        """Jobs without explicit mode default to AI mode."""
        executor = self._make_executor()
        job = {"id": "no-mode-job", "task": "do something"}

        with patch.object(executor, "_execute_command_mode") as mock_cmd, patch.object(
            executor, "_execute_ai_mode", return_value="result"
        ) as mock_ai:
            executor._execute_task(job)

        mock_cmd.assert_not_called()
        mock_ai.assert_called_once()

    def test_command_mode_with_runtime_emits_warning(self):
        """command+runtime is a misconfiguration — executor must log a warning."""
        executor = self._make_executor()
        job = {
            "id": "buggy-job",
            "mode": "command",
            "runtime": "copilot",
            "model": "claude-haiku-4.5",
            "task": "python3 /opt/bin/dispatch.py",
        }

        with patch.object(
            executor, "_execute_command_mode", return_value="ok"
        ), patch.object(executor, "_execute_ai_mode") as mock_ai, self.assertLogs(
            level="WARNING"
        ) as log_ctx:
            executor._execute_task(job)

        mock_ai.assert_not_called()
        self.assertTrue(
            any("mode='command'" in msg and "runtime" in msg for msg in log_ctx.output),
            f"Expected warning about runtime field. Got: {log_ctx.output}",
        )

    def test_command_mode_with_only_runtime_emits_warning(self):
        """command + runtime (no model) also warns."""
        executor = self._make_executor()
        job = {"id": "j2", "mode": "command", "runtime": "copilot", "task": "ls"}

        with patch.object(
            executor, "_execute_command_mode", return_value="ok"
        ), self.assertLogs(level="WARNING"):
            executor._execute_task(job)

    def test_command_mode_without_runtime_no_warning(self):
        """Clean command-mode job (no runtime/model) must not emit a warning."""
        executor = self._make_executor()
        job = {"id": "clean-job", "mode": "command", "task": "echo hi"}

        with patch.object(executor, "_execute_command_mode", return_value="ok"):
            with self.assertNoLogs("test", level="WARNING"):
                executor._execute_task(job)

    def test_command_mode_result_unchanged_despite_warning(self):
        """Misconfigured job must still execute and return the command result."""
        executor = self._make_executor()
        job = {
            "id": "buggy-but-runs",
            "mode": "command",
            "runtime": "copilot",
            "task": "echo test",
        }

        with patch.object(
            executor, "_execute_command_mode", return_value="cmd_output"
        ), self.assertLogs(level="WARNING"):
            result = executor._execute_task(job)

        self.assertEqual(result, "cmd_output")


if __name__ == "__main__":
    unittest.main(verbosity=2)
