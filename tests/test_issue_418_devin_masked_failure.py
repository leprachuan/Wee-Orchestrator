"""Regression tests for Issue #418: Scheduled jobs on devin runtime silently
"succeed" while erroring on invalid model.

Root cause: Devin dropped every claude-haiku-* model, but the stale
DEVIN_MODELS / model-manifest.json entries still listed claude-haiku-4.5, so
`/model set` validation never rejected it. When agent_manager.py then invoked
the actual Devin CLI with that deprecated model, Devin printed an
"Error: Unknown model" message but exited 0, and
scheduler/executor.py::_run_ai_attempt() only checked returncode == 0 to
decide success -- so the failed run was logged as success: true.
"""

import logging
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")
os.environ.setdefault("API_SHARED_KEY", "test_key_123")

from scheduler.executor import TaskSchedulerExecutor  # noqa: E402


class TestMaskedFailureDetection(unittest.TestCase):
    """Test _detect_masked_failure() pattern matching."""

    @classmethod
    def setUpClass(cls):
        cls.executor = TaskSchedulerExecutor.__new__(TaskSchedulerExecutor)

    def test_devin_unknown_model_detected(self):
        output = "Error: Unknown model: 'claude-haiku-4.5' Available: claude-sonnet-4.6, ..."
        self.assertEqual(
            self.executor._detect_masked_failure(output),
            output.strip(),
        )

    def test_unknown_agent_detected(self):
        self.assertIsNotNone(
            self.executor._detect_masked_failure("Unknown agent: 'bogus'. Available: ...")
        )

    def test_unknown_runtime_detected(self):
        self.assertIsNotNone(
            self.executor._detect_masked_failure("Unknown runtime 'bogus'")
        )

    def test_generic_error_prefix_detected(self):
        self.assertIsNotNone(
            self.executor._detect_masked_failure("Error: something went wrong")
        )

    def test_normal_output_not_flagged(self):
        self.assertIsNone(
            self.executor._detect_masked_failure(
                "Heartbeat check complete. All services healthy."
            )
        )

    def test_empty_output_not_flagged(self):
        self.assertIsNone(self.executor._detect_masked_failure(""))
        self.assertIsNone(self.executor._detect_masked_failure(None))


class TestRunAiAttemptMaskedFailure(unittest.TestCase):
    """End-to-end test of _run_ai_attempt() treating a devin exit-0 model
    error as a failure instead of a success."""

    def _make_executor(self):
        exec_ = TaskSchedulerExecutor.__new__(TaskSchedulerExecutor)
        exec_.logger = logging.getLogger("test")
        exec_.repo_root = Path("/opt/n8n-copilot-shim-dev")
        exec_.config_file = "/opt/n8n-copilot-shim-dev/agents.json"
        exec_._log_job = MagicMock()
        exec_._write_checkpoint = MagicMock()
        exec_._clear_checkpoint = MagicMock()
        exec_._save_result = MagicMock()
        return exec_

    def test_devin_exit0_with_model_error_is_not_recorded_as_success(self):
        """Reproduces the exact issue #418 failure: devin exits 0 but the
        job used a deprecated model and printed an Unknown model error."""
        executor = self._make_executor()

        job = {
            "id": "heartbeat---fosterbot",
            "name": "heartbeat---fosterbot",
            "agent": "orchestrator",
            "runtime": "devin",
            "model": "claude-haiku-4.5",
            "task": "run heartbeat",
        }

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = (
            "Error: Unknown model: 'claude-haiku-4.5' "
            "Available: claude-sonnet-4.6, claude-opus-4.7, ..."
        )
        fake_result.stderr = ""

        with patch("scheduler.executor.subprocess.run", return_value=fake_result):
            output, error = executor._run_ai_attempt(job)

        # The masked failure must surface as a failure, not a success.
        self.assertIsNone(output)
        self.assertIsNotNone(error)
        self.assertIn("Unknown model", error)

        # _run_ai_attempt must not persist this run as a success anywhere.
        executor._save_result.assert_not_called()

    def test_devin_real_success_still_recorded(self):
        """Sanity check: a genuine success (no error signature) is unaffected."""
        executor = self._make_executor()

        job = {
            "id": "heartbeat---fosterbot",
            "name": "heartbeat---fosterbot",
            "agent": "orchestrator",
            "runtime": "devin",
            "model": "claude-sonnet-4.6",
            "task": "run heartbeat",
        }

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "Heartbeat complete. All services healthy."
        fake_result.stderr = ""

        with patch("scheduler.executor.subprocess.run", return_value=fake_result):
            output, error = executor._run_ai_attempt(job)

        self.assertIsNone(error)
        self.assertEqual(output, "Heartbeat complete. All services healthy.")
        executor._save_result.assert_called_once()
        _, kwargs = executor._save_result.call_args
        self.assertTrue(kwargs.get("success"))


class TestDevinModelListNoLongerContainsHaiku(unittest.TestCase):
    """Guard against re-introducing a deprecated haiku model for devin."""

    def test_static_devin_models_excludes_haiku(self):
        import agent_manager

        flat_ids = [
            model_id
            for models in agent_manager.SessionManager.DEVIN_MODELS.values()
            for (model_id, *_rest) in models
        ]
        for model_id in flat_ids:
            self.assertNotIn(
                "haiku",
                model_id.lower(),
                f"DEVIN_MODELS should not list deprecated haiku model {model_id!r}",
            )

    def test_manifest_devin_models_excludes_haiku(self):
        import json

        with open("/opt/n8n-copilot-shim-dev/model-manifest.json") as f:
            manifest = json.load(f)

        devin_models = manifest["runtimes"]["devin"]
        for model_id in devin_models:
            self.assertNotIn(
                "haiku",
                model_id.lower(),
                f"model-manifest.json devin list should not include {model_id!r}",
            )


if __name__ == "__main__":
    unittest.main()
