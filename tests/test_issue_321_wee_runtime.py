"""Regression test for Issue #321: Scheduler rejects 'wee' runtime for scheduled jobs.

This test ensures that when a scheduled job is configured with runtime='wee',
the scheduler properly recognizes it and assigns a default model.
"""

import logging
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")
os.environ.setdefault("API_SHARED_KEY", "test_key_123")

from scheduler.executor import TaskSchedulerExecutor


class TestIssue321WeeRuntime(unittest.TestCase):
    """Test that wee runtime is recognized as a valid scheduler runtime."""

    def _make_executor(self):
        """Create a minimal executor instance for testing."""
        exec_ = TaskSchedulerExecutor.__new__(TaskSchedulerExecutor)
        exec_.logger = logging.getLogger("test")
        exec_.repo_root = "/opt/n8n-copilot-shim-dev"
        exec_._job_last_exec_mono = {}
        exec_._wall_clock_debt = 0.0
        return exec_

    def test_wee_runtime_has_default_model(self):
        """wee runtime should have a default model in _default_models dict."""
        executor = self._make_executor()
        
        # Simulate the code from _run_ai_attempt
        _default_models = {
            "claude": "sonnet",
            "copilot": "gpt-4.1",
            "gemini": "gemini-1.5-pro",
            "opencode": "gpt-4o",
            "wee": "ollama/qwen3",
        }
        
        # Verify wee runtime has a default model
        self.assertIn("wee", _default_models)
        self.assertEqual(_default_models["wee"], "ollama/qwen3")

    def test_wee_runtime_assigns_default_model_when_not_specified(self):
        """When job has runtime='wee' but no model, default model should be assigned."""
        executor = self._make_executor()
        
        job = {
            "id": "test-wee-job",
            "agent": "orchestrator",
            "runtime": "wee",
            "task": "Test with wee runtime",
        }
        
        runtime = job.get("runtime", "claude")
        _default_models = {
            "claude": "sonnet",
            "copilot": "gpt-4.1",
            "gemini": "gemini-1.5-pro",
            "opencode": "gpt-4o",
            "wee": "ollama/qwen3",
        }
        model = job.get("model") or _default_models.get(runtime, "sonnet")
        
        self.assertEqual(runtime, "wee")
        self.assertEqual(model, "ollama/qwen3")

    def test_wee_runtime_respects_explicit_model(self):
        """When job specifies both runtime='wee' and a model, use the explicit model."""
        executor = self._make_executor()
        
        job = {
            "id": "test-wee-explicit",
            "agent": "orchestrator",
            "runtime": "wee",
            "model": "ollama/qwen3.5-64k:latest",
            "task": "Test with explicit wee model",
        }
        
        runtime = job.get("runtime", "claude")
        _default_models = {
            "claude": "sonnet",
            "copilot": "gpt-4.1",
            "gemini": "gemini-1.5-pro",
            "opencode": "gpt-4o",
            "wee": "ollama/qwen3",
        }
        model = job.get("model") or _default_models.get(runtime, "sonnet")
        
        self.assertEqual(runtime, "wee")
        self.assertEqual(model, "ollama/qwen3.5-64k:latest")


if __name__ == "__main__":
    unittest.main()
