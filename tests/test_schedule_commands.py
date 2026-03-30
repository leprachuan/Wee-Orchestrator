#!/usr/bin/env python3
"""
Tests for /schedule slash commands in agent_manager.py

Tests cover:
- /schedule list
- /schedule status
- /schedule add
- /schedule info
- /schedule pause
- /schedule resume
- /schedule delete
- /schedule logs
- /schedule results
- /schedule <unknown> (help fallback)
- SCHEDULER_ENABLED=False guard
- /help includes schedule commands
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import agent_manager
from agent_manager import SessionManager


def _make_manager(temp_path):
    agents_config = {
        "agents": [{"name": "test_agent", "description": "Test", "path": "/tmp/test"}]
    }
    config_file = temp_path / "agents.json"
    with open(config_file, "w") as f:
        json.dump(agents_config, f)

    patcher = patch("agent_manager.Path.home")
    mock_home = patcher.start()
    mock_home.return_value = temp_path

    manager = SessionManager(str(config_file))
    return manager, patcher


def _mock_scheduler():
    """Return a mock TaskScheduler with sensible defaults."""
    sched = MagicMock()

    sched.list_jobs.return_value = {
        "success": True,
        "result": [
            {
                "id": "daily-report",
                "name": "Daily Report",
                "schedule": "every day at 9am",
                "next_run": "2025-01-02T09:00:00Z",
                "last_run": None,
                "agent": "orchestrator",
                "runtime": "claude",
                "recurring": True,
                "enabled": True,
                "task": "generate daily summary",
            }
        ],
        "message": "Found 1 jobs",
    }

    sched.doctor.return_value = {
        "success": True,
        "result": {"status": "ok", "jobs_count": 1, "next_run": "2025-01-02T09:00:00Z"},
    }

    sched.schedule_task.return_value = {
        "success": True,
        "result": {
            "id": "daily-report",
            "name": "Daily Report",
            "schedule": "every day at 9am",
            "next_run": "2025-01-02T09:00:00Z",
            "recurring": True,
        },
        "message": "Task 'Daily Report' scheduled for 2025-01-02T09:00:00Z",
    }

    sched.get_job.return_value = {
        "success": True,
        "result": {
            "id": "daily-report",
            "name": "Daily Report",
            "schedule": "every day at 9am",
            "next_run": "2025-01-02T09:00:00Z",
            "last_run": None,
            "agent": "orchestrator",
            "runtime": "claude",
            "recurring": True,
            "enabled": True,
            "task": "generate daily summary",
        },
    }

    sched.pause_job.return_value = {"success": True, "message": "Job paused."}
    sched.resume_job.return_value = {"success": True, "message": "Job resumed."}
    sched.delete_job.return_value = {"success": True, "message": "Job deleted."}

    sched.get_logs.return_value = {
        "success": True,
        "result": [
            "2025-01-01T09:00:00Z Scheduled run started",
            "2025-01-01T09:00:05Z Completed",
        ],
    }

    sched.get_results.return_value = {
        "success": True,
        "result": [
            {
                "timestamp": "2025-01-01T09:00:05Z",
                "success": True,
                "summary": "Daily summary generated.",
            }
        ],
    }

    return sched


class TestScheduleSlashCommands(unittest.TestCase):
    """Tests for /schedule slash command routing."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.manager, self.patcher = _make_manager(self.temp_path)
        self.session = "test_schedule_session"

        self.mock_sched = _mock_scheduler()

        # SCHEDULER_ENABLED and _get_scheduler are referenced as globals inside
        # SessionManager.execute() — inject them into the module namespace for tests.
        agent_manager.SCHEDULER_ENABLED = True
        agent_manager._get_scheduler = lambda: self.mock_sched

    def tearDown(self):
        self.patcher.stop()
        # Clean up injected globals
        for attr in ("SCHEDULER_ENABLED", "_get_scheduler"):
            if hasattr(agent_manager, attr):
                delattr(agent_manager, attr)
        self.temp_dir.cleanup()

    # --- /schedule list ---

    def test_schedule_list(self):
        result = self.manager.execute("/schedule list", self.session)
        self.assertIn("Scheduled Jobs", result)
        self.assertIn("daily-report", result)
        self.assertIn("Daily Report", result)
        self.mock_sched.list_jobs.assert_called_once()

    def test_schedule_no_subcommand_defaults_to_list(self):
        result = self.manager.execute("/schedule", self.session)
        self.assertIn("Scheduled Jobs", result)
        self.mock_sched.list_jobs.assert_called_once()

    def test_schedule_list_empty(self):
        self.mock_sched.list_jobs.return_value = {
            "success": True,
            "result": [],
            "message": "Found 0 jobs",
        }
        result = self.manager.execute("/schedule list", self.session)
        self.assertIn("No jobs scheduled", result)

    # --- /schedule status ---

    def test_schedule_status(self):
        result = self.manager.execute("/schedule status", self.session)
        self.assertIn("Scheduler Status", result)
        self.assertIn("ok", result)
        self.mock_sched.doctor.assert_called_once()

    # --- /schedule add ---

    def test_schedule_add(self):
        result = self.manager.execute(
            "/schedule add Daily Report | every day at 9am | generate daily summary",
            self.session,
        )
        self.assertIn("Job scheduled", result)
        self.assertIn("daily-report", result)
        self.assertIn("every day at 9am", result)
        self.mock_sched.schedule_task.assert_called_once()
        call_kwargs = self.mock_sched.schedule_task.call_args[1]
        self.assertEqual(call_kwargs["name"], "Daily Report")
        self.assertEqual(call_kwargs["schedule"], "every day at 9am")
        self.assertEqual(call_kwargs["task"], "generate daily summary")

    def test_schedule_add_recurring_detection(self):
        """Jobs with 'in X' schedule should be non-recurring."""
        self.manager.execute(
            "/schedule add One-time Ping | in 10 minutes | say hello",
            self.session,
        )
        call_kwargs = self.mock_sched.schedule_task.call_args[1]
        self.assertFalse(call_kwargs["recurring"])

    def test_schedule_add_recurring_flag_set(self):
        """Jobs with 'every' schedule should be recurring."""
        self.manager.execute(
            "/schedule add Daily Report | every day at 9am | generate summary",
            self.session,
        )
        call_kwargs = self.mock_sched.schedule_task.call_args[1]
        self.assertTrue(call_kwargs["recurring"])

    def test_schedule_add_missing_parts_returns_usage(self):
        result = self.manager.execute("/schedule add Bad Input", self.session)
        self.assertIn("Usage", result)
        self.assertIn("|", result)
        self.mock_sched.schedule_task.assert_not_called()

    def test_schedule_add_failure(self):
        self.mock_sched.schedule_task.return_value = {
            "success": False,
            "message": "Duplicate job ID",
        }
        result = self.manager.execute(
            "/schedule add Daily Report | every day at 9am | generate summary",
            self.session,
        )
        self.assertIn("Duplicate job ID", result)

    # --- /schedule info ---

    def test_schedule_info(self):
        result = self.manager.execute("/schedule info daily-report", self.session)
        self.assertIn("daily-report", result)
        self.assertIn("Daily Report", result)
        self.assertIn("every day at 9am", result)
        self.mock_sched.get_job.assert_called_once_with("daily-report")

    def test_schedule_info_not_found(self):
        self.mock_sched.get_job.return_value = {
            "success": False,
            "message": "Job not found",
        }
        result = self.manager.execute("/schedule info missing-job", self.session)
        self.assertIn("Job not found", result)

    # --- /schedule pause ---

    def test_schedule_pause(self):
        result = self.manager.execute("/schedule pause daily-report", self.session)
        self.assertIn("paused", result.lower())
        self.assertIn("daily-report", result)
        self.mock_sched.pause_job.assert_called_once_with("daily-report")

    def test_schedule_pause_failure(self):
        self.mock_sched.pause_job.return_value = {
            "success": False,
            "message": "Job not found",
        }
        result = self.manager.execute("/schedule pause bad-id", self.session)
        self.assertIn("Job not found", result)

    # --- /schedule resume ---

    def test_schedule_resume(self):
        result = self.manager.execute("/schedule resume daily-report", self.session)
        self.assertIn("resumed", result.lower())
        self.assertIn("daily-report", result)
        self.mock_sched.resume_job.assert_called_once_with("daily-report")

    def test_schedule_resume_failure(self):
        self.mock_sched.resume_job.return_value = {
            "success": False,
            "message": "Job not found",
        }
        result = self.manager.execute("/schedule resume bad-id", self.session)
        self.assertIn("Job not found", result)

    # --- /schedule delete ---

    def test_schedule_delete(self):
        result = self.manager.execute("/schedule delete daily-report", self.session)
        self.assertIn("deleted", result.lower())
        self.assertIn("daily-report", result)
        self.mock_sched.delete_job.assert_called_once_with("daily-report")

    def test_schedule_remove_alias(self):
        """'remove' should be an alias for 'delete'."""
        result = self.manager.execute("/schedule remove daily-report", self.session)
        self.assertIn("deleted", result.lower())
        self.mock_sched.delete_job.assert_called_once_with("daily-report")

    def test_schedule_delete_failure(self):
        self.mock_sched.delete_job.return_value = {
            "success": False,
            "message": "Job not found",
        }
        result = self.manager.execute("/schedule delete bad-id", self.session)
        self.assertIn("Job not found", result)

    # --- /schedule logs ---

    def test_schedule_logs(self):
        result = self.manager.execute("/schedule logs daily-report", self.session)
        self.assertIn("Logs for", result)
        self.assertIn("daily-report", result)
        self.assertIn("Scheduled run started", result)
        self.mock_sched.get_logs.assert_called_once_with("daily-report")

    def test_schedule_logs_empty(self):
        self.mock_sched.get_logs.return_value = {"success": True, "result": []}
        result = self.manager.execute("/schedule logs daily-report", self.session)
        self.assertIn("No logs", result)

    def test_schedule_logs_failure(self):
        self.mock_sched.get_logs.return_value = {
            "success": False,
            "message": "Job not found",
        }
        result = self.manager.execute("/schedule logs bad-id", self.session)
        self.assertIn("Job not found", result)

    # --- /schedule results ---

    def test_schedule_results(self):
        result = self.manager.execute("/schedule results daily-report", self.session)
        self.assertIn("Results for", result)
        self.assertIn("daily-report", result)
        self.assertIn("Daily summary generated", result)
        self.mock_sched.get_results.assert_called_once_with("daily-report")

    def test_schedule_results_empty(self):
        self.mock_sched.get_results.return_value = {"success": True, "result": []}
        result = self.manager.execute("/schedule results daily-report", self.session)
        self.assertIn("No results", result)

    def test_schedule_results_failure(self):
        self.mock_sched.get_results.return_value = {
            "success": False,
            "message": "Job not found",
        }
        result = self.manager.execute("/schedule results bad-id", self.session)
        self.assertIn("Job not found", result)

    # --- Unknown subcommand / help fallback ---

    def test_schedule_unknown_subcommand_shows_help(self):
        result = self.manager.execute("/schedule badcommand", self.session)
        self.assertIn("Schedule Commands", result)
        self.assertIn("/schedule list", result)
        self.assertIn("/schedule add", result)
        self.assertIn("/schedule pause", result)

    # --- SCHEDULER_ENABLED=False guard ---

    def test_schedule_disabled(self):
        agent_manager.SCHEDULER_ENABLED = False
        result = self.manager.execute("/schedule list", self.session)
        self.assertIn("not enabled", result.lower())
        self.mock_sched.list_jobs.assert_not_called()

    # --- /help includes /schedule ---

    def test_help_includes_schedule(self):
        result = self.manager.execute("/help", self.session)
        self.assertIn("/schedule", result)
        self.assertIn("Scheduler", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
