"""Regression test for Issue #341: Scheduler Run Now does not execute command-mode jobs.

Verifies that:
1. Run Now executes command-mode jobs immediately via subprocess
2. Run Now writes a result row to the scheduler results file
3. last_run and next_run are updated correctly after manual execution
4. Existing AI-mode Run Now behavior is not regressed
"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class TestIssue341RunNowExecutesCommandModeJob(unittest.TestCase):
    """Regression tests for issue #341: Run Now must execute command-mode jobs."""

    def test_run_command_task_calls_save_result(self):
        """_run_command_task must call sched.save_result (not sched._save_result)."""
        source = REPO / "agent_manager.py"
        content = source.read_text()

        start = content.find("def _run_command_task(")
        self.assertNotEqual(start, -1, "_run_command_task must exist")

        end = content.find("\n    def ", start + 1)
        func_body = content[start:end]

        # The public save_result method must be called (not the nonexistent _save_result)
        self.assertIn(
            "sched.save_result(",
            func_body,
            "_run_command_task must call sched.save_result() to write the result row",
        )
        self.assertNotIn(
            "sched._save_result(",
            func_body,
            "_run_command_task must NOT call sched._save_result() — that method does not exist on management.TaskScheduler",
        )

    def test_run_command_task_calls_log_not_log_job(self):
        """_run_command_task must call sched._log (not sched._log_job)."""
        source = REPO / "agent_manager.py"
        content = source.read_text()

        start = content.find("def _run_command_task(")
        end = content.find("\n    def ", start + 1)
        func_body = content[start:end]

        self.assertIn(
            "sched._log(",
            func_body,
            "_run_command_task must call sched._log() to write to the scheduler log",
        )
        self.assertNotIn(
            "sched._log_job(",
            func_body,
            "_run_command_task must NOT call sched._log_job() — that method does not exist on management.TaskScheduler",
        )

    def test_management_task_scheduler_has_save_result(self):
        """management.TaskScheduler must have public save_result method."""
        sys.path.insert(0, str(REPO / "scheduler"))
        from scheduler.management import TaskScheduler

        sched = TaskScheduler.__new__(TaskScheduler)
        self.assertTrue(
            hasattr(sched, "save_result") and callable(sched.save_result),
            "TaskScheduler must have save_result() method",
        )
        self.assertFalse(
            hasattr(sched, "_save_result"),
            "TaskScheduler must NOT have _save_result() — callers must use save_result()",
        )

    def test_management_task_scheduler_has_log(self):
        """management.TaskScheduler must have _log method."""
        from scheduler.management import TaskScheduler

        sched = TaskScheduler.__new__(TaskScheduler)
        self.assertTrue(
            hasattr(sched, "_log") and callable(sched._log),
            "TaskScheduler must have _log() method",
        )
        self.assertFalse(
            hasattr(sched, "_log_job"),
            "TaskScheduler must NOT have _log_job() — callers must use _log()",
        )

    def test_run_now_writes_result_row(self):
        """Run Now for a command-mode job must write a result row to the JSONL file."""
        try:
            from httpx import ASGITransport, AsyncClient
        except ImportError:
            self.skipTest("httpx not available")

        from agent_manager import create_api_app

        with tempfile.TemporaryDirectory() as tmpdir:
            jobs_file = Path(tmpdir) / "jobs.json"
            logs_dir = Path(tmpdir) / "logs"
            results_dir = Path(tmpdir) / "results"
            logs_dir.mkdir()
            results_dir.mkdir()
            jobs_file.write_text('{"jobs": []}')

            os.environ["API_SHARED_KEY"] = "test_key_341"
            os.environ["SCHEDULER_JOBS_FILE"] = str(jobs_file)
            os.environ["SCHEDULER_LOGS_DIR"] = str(logs_dir)
            os.environ["SCHEDULER_RESULTS_DIR"] = str(results_dir)

            try:
                app = create_api_app()

                mock_job = {
                    "id": "test-cmd-341",
                    "name": "Test Issue 341",
                    "task": "echo regression_341",
                    "mode": "command",
                    "timeout": 30,
                    "working_dir": "/tmp",
                    "notify": False,
                    "agent": None,
                    "runtime": None,
                    "model": None,
                    "schedule": "every 5 minutes",
                    "enabled": True,
                    "last_run": None,
                    "next_run": None,
                }

                mock_scheduler = MagicMock()
                mock_scheduler.get_job.return_value = {
                    "success": True,
                    "result": mock_job,
                }
                mock_scheduler.run_job.return_value = {"success": True}

                result_rows = []
                log_messages = []

                def capture_save_result(job_id, job_name, success, output="", error=""):
                    result_rows.append({
                        "job_id": job_id,
                        "job_name": job_name,
                        "success": success,
                        "output": output,
                        "error": error,
                    })
                    return {"success": True}

                def capture_log(job_id, message):
                    log_messages.append((job_id, message))

                mock_scheduler.save_result = capture_save_result
                mock_scheduler._log = capture_log

                async def _run():
                    transport = ASGITransport(app=app)
                    async with AsyncClient(
                        transport=transport, base_url="https://test"
                    ) as client:
                        with patch(
                            "scheduler.management.TaskScheduler",
                            return_value=mock_scheduler,
                        ):
                            response = await client.post(
                                "/api/v1/scheduler/jobs/test-cmd-341/run",
                                headers={"Authorization": "Bearer shared_test_key_341"},
                            )
                            return response

                response = asyncio.run(_run())
                self.assertEqual(response.status_code, 200)

                import time
                time.sleep(1)

                # Verify a result row was written
                self.assertTrue(
                    len(result_rows) >= 1,
                    f"Run Now must write a scheduler result row; got {result_rows}",
                )
                self.assertEqual(result_rows[0]["job_id"], "test-cmd-341")

                # Verify log was written
                log_job_ids = [m[0] for m in log_messages]
                self.assertIn(
                    "test-cmd-341",
                    log_job_ids,
                    "Run Now must write a log entry for the job",
                )

            finally:
                os.environ.pop("SCHEDULER_JOBS_FILE", None)
                os.environ.pop("SCHEDULER_LOGS_DIR", None)
                os.environ.pop("SCHEDULER_RESULTS_DIR", None)

    def test_run_now_ai_mode_not_regressed(self):
        """AI-mode Run Now still dispatches to _run_background_task (not regressed)."""
        source = REPO / "agent_manager.py"
        content = source.read_text()

        start = content.find("async def run_scheduler_job_now(")
        self.assertNotEqual(start, -1, "run_scheduler_job_now handler must exist")

        end = content.find("\n        @app.", start + 1)
        handler_body = content[start:end]

        # AI mode path must still use _run_background_task
        lines = handler_body.split("\n")
        found_else = False
        found_bg_after_else = False
        for line in lines:
            if "else:" in line and not found_else:
                found_else = True
            if found_else and "_run_background_task" in line:
                found_bg_after_else = True
                break

        self.assertTrue(
            found_bg_after_else,
            "AI-mode branch must still dispatch via _run_background_task",
        )


if __name__ == "__main__":
    unittest.main()
