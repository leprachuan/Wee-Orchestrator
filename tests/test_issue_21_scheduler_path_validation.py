"""Regression tests for Issue #21: scheduler path traversal and workdir validation."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


management = _load_module(
    "issue21_scheduler_management", REPO / "scheduler" / "management.py"
)
TaskScheduler = management.TaskScheduler


class TestIssue21SchedulerPathValidation(unittest.TestCase):
    """Issue #21: scheduler must reject traversal and off-allowlist workdirs."""

    def _scheduler_env(self, root: Path, allowed_root: Path):
        jobs_file = root / "jobs.json"
        logs_dir = root / "logs"
        results_dir = root / "results"
        return {
            "SCHEDULER_JOBS_FILE": str(jobs_file),
            "SCHEDULER_LOGS_DIR": str(logs_dir),
            "SCHEDULER_RESULTS_DIR": str(results_dir),
            "SCHEDULER_ALLOWED_WORKDIRS": str(allowed_root),
        }

    def test_issue_21_get_logs_rejects_path_traversal_job_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            allowed_root = tmp_path / "allowed"
            allowed_root.mkdir()
            with patch.dict(
                os.environ, self._scheduler_env(tmp_path, allowed_root), clear=False
            ):
                scheduler = TaskScheduler()

                secret = tmp_path / "secret.log"
                secret.write_text("top-secret")

                result = scheduler.get_logs("../secret")

                self.assertFalse(result["success"])
                self.assertIn("Invalid job ID", result["message"])

    def test_issue_21_get_logs_reads_only_valid_job_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            allowed_root = tmp_path / "allowed"
            allowed_root.mkdir()
            with patch.dict(
                os.environ, self._scheduler_env(tmp_path, allowed_root), clear=False
            ):
                scheduler = TaskScheduler()

                log_file = scheduler.logs_dir / "daily-report.log"
                log_file.write_text("expected-log")

                result = scheduler.get_logs("daily-report")

                self.assertTrue(result["success"])
                self.assertEqual(result["result"], "expected-log")

    def test_issue_21_schedule_task_sanitizes_traversal_name_before_logging(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            allowed_root = tmp_path / "allowed"
            allowed_root.mkdir()
            with patch.dict(
                os.environ, self._scheduler_env(tmp_path, allowed_root), clear=False
            ):
                scheduler = TaskScheduler()

                result = scheduler.schedule_task(
                    name="../../escape",
                    schedule="in 5 minutes",
                    mode="command",
                    task="pwd",
                    working_dir=str(allowed_root),
                )

                self.assertTrue(result["success"])
                self.assertEqual(result["result"]["id"], "escape")
                self.assertFalse((tmp_path / "escape.log").exists())
                self.assertTrue((scheduler.logs_dir / "escape.log").exists())

    def test_issue_21_get_results_rejects_path_traversal_job_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            allowed_root = tmp_path / "allowed"
            allowed_root.mkdir()
            with patch.dict(
                os.environ, self._scheduler_env(tmp_path, allowed_root), clear=False
            ):
                scheduler = TaskScheduler()

                secret = tmp_path / "secret.jsonl"
                secret.write_text(json.dumps({"success": True}) + "\n")

                result = scheduler.get_results("../secret")

                self.assertFalse(result["success"])
                self.assertIn("Invalid job ID", result["message"])

    def test_issue_21_update_job_rejects_working_dir_outside_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            allowed_root = tmp_path / "allowed"
            allowed_root.mkdir()
            with patch.dict(
                os.environ, self._scheduler_env(tmp_path, allowed_root), clear=False
            ):
                scheduler = TaskScheduler()

                create = scheduler.schedule_task(
                    name="Issue 21 Job",
                    schedule="in 5 minutes",
                    mode="command",
                    task="pwd",
                    working_dir=str(allowed_root),
                )
                self.assertTrue(create["success"])

                blocked = tmp_path / "blocked"
                blocked.mkdir()
                result = scheduler.update_job(
                    create["result"]["id"], {"working_dir": str(blocked)}
                )

                self.assertFalse(result["success"])
                self.assertIn("allowed root", result["message"])

    def test_issue_21_update_job_accepts_working_dir_under_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            allowed_root = tmp_path / "allowed"
            nested = allowed_root / "project"
            nested.mkdir(parents=True)
            with patch.dict(
                os.environ, self._scheduler_env(tmp_path, allowed_root), clear=False
            ):
                scheduler = TaskScheduler()

                create = scheduler.schedule_task(
                    name="Allowed Workdir",
                    schedule="in 5 minutes",
                    mode="command",
                    task="pwd",
                    working_dir=str(allowed_root),
                )
                self.assertTrue(create["success"])

                result = scheduler.update_job(
                    create["result"]["id"], {"working_dir": str(nested)}
                )

                self.assertTrue(result["success"])
                self.assertEqual(result["result"]["working_dir"], str(nested))

    def test_issue_21_results_api_rejects_path_traversal_job_id(self):
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            allowed_root = tmp_path / "allowed"
            allowed_root.mkdir()
            env = {
                **self._scheduler_env(tmp_path, allowed_root),
                "API_SHARED_KEY": "test_key_123",
                "APP_ENV": "DEV",
                "API_PORT": "8099",
            }
            with patch.dict(os.environ, env, clear=False):
                agent_manager = _load_module(
                    "issue21_agent_manager", REPO / "agent_manager.py"
                )
                app = agent_manager.create_api_app()
                client = TestClient(app)

                response = client.get(
                    "/api/v1/scheduler/jobs/%2E%2E%2Fsecret/results",
                    headers={"Authorization": "Bearer shared_test_key_123"},
                )

                self.assertEqual(response.status_code, 404)
                self.assertNotIn("secret", response.text.lower())


if __name__ == "__main__":
    unittest.main()
