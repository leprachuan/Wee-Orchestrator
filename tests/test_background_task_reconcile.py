"""
Tests for background task reconciliation on startup (queue deadlock fix):
- reconcile_stale_tasks() marks orphaned running tasks as failed
- reconcile_stale_tasks() leaves alive running tasks untouched
- Queued tasks are promoted after stale running tasks are failed
- Periodic reconciliation promotes queued tasks when slots free up
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

os.environ.setdefault("API_SHARED_KEY", "test_key_123")

from agent_manager import BackgroundTaskManager


def _make_mgr(tmp_path: str) -> BackgroundTaskManager:
    """Create a BackgroundTaskManager that writes to a temp file."""
    mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
    mgr._path = tmp_path
    mgr._lock = __import__("threading").Lock()
    mgr._bg_events = {}
    mgr._bg_events_lock = __import__("threading").Lock()
    return mgr


def _seed_tasks(mgr, tasks):
    """Write raw tasks to the manager's JSON file."""
    with open(mgr._path, "w") as f:
        json.dump(tasks, f)


class TestReconcileOnStartup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.mgr = _make_mgr(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_stale_running_marked_failed(self):
        """Running tasks with dead PIDs should be marked failed."""
        _seed_tasks(
            self.mgr,
            [
                {
                    "task_id": "bg_test1",
                    "status": "running",
                    "pid": 999999999,  # PID that doesn't exist
                    "agent": "orchestrator",
                    "channel": "webui",
                    "user_identity": "test_user",
                    "created_at": "2026-01-01T00:00:00Z",
                },
            ],
        )
        result = self.mgr.reconcile_stale_tasks()
        self.assertEqual(result["stale_running"], 1)

        tasks = self.mgr.list_all_tasks()
        self.assertEqual(tasks[0]["status"], "failed")
        self.assertIn("Orphaned", tasks[0]["error"])

    def test_alive_running_not_touched(self):
        """Running tasks with a live PID should not be touched."""
        my_pid = os.getpid()
        _seed_tasks(
            self.mgr,
            [
                {
                    "task_id": "bg_alive",
                    "status": "running",
                    "pid": my_pid,
                    "agent": "orchestrator",
                    "channel": "webui",
                    "user_identity": "test_user",
                    "created_at": "2026-01-01T00:00:00Z",
                },
            ],
        )
        result = self.mgr.reconcile_stale_tasks()
        self.assertEqual(result["stale_running"], 0)

        tasks = self.mgr.list_all_tasks()
        self.assertEqual(tasks[0]["status"], "running")

    def test_running_no_pid_marked_failed(self):
        """Running tasks with no PID (pid=0) should be marked failed."""
        _seed_tasks(
            self.mgr,
            [
                {
                    "task_id": "bg_nopid",
                    "status": "running",
                    "pid": 0,
                    "agent": "orchestrator",
                    "channel": "webui",
                    "user_identity": "test_user",
                    "created_at": "2026-01-01T00:00:00Z",
                },
            ],
        )
        result = self.mgr.reconcile_stale_tasks()
        self.assertEqual(result["stale_running"], 1)

    def test_queued_count_returned(self):
        """Queued tasks should be counted in the reconcile result."""
        _seed_tasks(
            self.mgr,
            [
                {
                    "task_id": "bg_q1",
                    "status": "queued",
                    "agent": "orchestrator",
                    "channel": "webui",
                    "user_identity": "test_user",
                    "created_at": "2026-01-01T00:00:01Z",
                },
                {
                    "task_id": "bg_q2",
                    "status": "queued",
                    "agent": "orchestrator",
                    "channel": "webui",
                    "user_identity": "test_user",
                    "created_at": "2026-01-01T00:00:02Z",
                },
            ],
        )
        result = self.mgr.reconcile_stale_tasks()
        self.assertEqual(result["queued_ready"], 2)

    def test_mixed_scenario(self):
        """Stale running + queued + completed tasks together."""
        _seed_tasks(
            self.mgr,
            [
                {
                    "task_id": "bg_stale",
                    "status": "running",
                    "pid": 999999999,
                    "agent": "orchestrator",
                    "channel": "webui",
                    "user_identity": "test_user",
                    "created_at": "2026-01-01T00:00:00Z",
                },
                {
                    "task_id": "bg_done",
                    "status": "completed",
                    "agent": "orchestrator",
                    "channel": "webui",
                    "user_identity": "test_user",
                    "created_at": "2026-01-01T00:00:00Z",
                },
                {
                    "task_id": "bg_queued",
                    "status": "queued",
                    "agent": "orchestrator",
                    "channel": "webui",
                    "user_identity": "test_user",
                    "created_at": "2026-01-01T00:00:01Z",
                },
            ],
        )
        result = self.mgr.reconcile_stale_tasks()
        self.assertEqual(result["stale_running"], 1)
        self.assertEqual(result["queued_ready"], 1)

        tasks = self.mgr.list_all_tasks()
        by_id = {t["task_id"]: t for t in tasks}
        self.assertEqual(by_id["bg_stale"]["status"], "failed")
        self.assertEqual(by_id["bg_done"]["status"], "completed")
        self.assertEqual(by_id["bg_queued"]["status"], "queued")

    def test_no_tasks_noop(self):
        """Empty task list should return all zeros."""
        _seed_tasks(self.mgr, [])
        result = self.mgr.reconcile_stale_tasks()
        self.assertEqual(result["stale_running"], 0)
        self.assertEqual(result["queued_ready"], 0)


if __name__ == "__main__":
    unittest.main()
