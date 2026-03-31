"""
Tests for background task visibility: all authorized users can see all tasks.
Verifies that the per-user RBAC filtering has been removed from visibility
while per-user rate limiting is preserved.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_manager import BackgroundTaskManager


def _make_mgr(tmp_path: str) -> BackgroundTaskManager:
    """Create a BackgroundTaskManager that writes to a temp file."""
    mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
    mgr._path = tmp_path
    mgr._lock = threading.Lock()
    return mgr


class TestBackgroundTaskVisibility(unittest.TestCase):
    """Verify all-user visibility for background tasks."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.mgr = _make_mgr(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _add_task(self, task_id, identity, channel="webui", status="running"):
        return self.mgr.create_task(
            task_id=task_id,
            session_id=f"sess-{task_id}",
            user_identity=identity,
            channel=channel,
            agent="orchestrator",
            runtime="copilot",
            model="sonnet",
            prompt=f"test task {task_id}",
            pid=0,
            status=status,
        )

    # ── list_all_tasks returns tasks from ALL users ────────────────────────────

    def test_list_all_tasks_returns_all_users(self):
        """list_all_tasks() must return tasks regardless of who created them."""
        self._add_task("t1", "alice@example.com")
        self._add_task("t2", "bob@example.com")
        self._add_task("t3", "charlie@example.com")
        tasks = self.mgr.list_all_tasks()
        task_ids = {t["task_id"] for t in tasks}
        self.assertEqual(task_ids, {"t1", "t2", "t3"})

    def test_list_all_tasks_returns_all_channels(self):
        """list_all_tasks() must return tasks from all channels."""
        self._add_task("t1", "user1", channel="webui")
        self._add_task("t2", "user1", channel="telegram")
        self._add_task("t3", "user2", channel="webex")
        tasks = self.mgr.list_all_tasks()
        self.assertEqual(len(tasks), 3)

    def test_list_all_tasks_returns_all_statuses(self):
        """list_all_tasks() must return tasks of every status."""
        self._add_task("t1", "alice", status="running")
        self._add_task("t2", "alice", status="queued")
        self._add_task("t3", "bob", status="completed")
        tasks = self.mgr.list_all_tasks()
        statuses = {t["status"] for t in tasks}
        self.assertEqual(statuses, {"running", "queued", "completed"})

    # ── get_task returns any task regardless of caller ─────────────────────────

    def test_get_task_returns_any_users_task(self):
        """get_task() must return a task regardless of who created it."""
        self._add_task("t1", "alice@example.com")
        task = self.mgr.get_task("t1")
        self.assertIsNotNone(task)
        self.assertEqual(task["task_id"], "t1")
        self.assertEqual(task["user_identity"], "alice@example.com")

    def test_get_task_returns_none_for_missing(self):
        """get_task() returns None for non-existent task."""
        self.assertIsNone(self.mgr.get_task("nonexistent"))

    # ── per-user list_tasks still works for rate limiting ──────────────────────

    def test_list_tasks_still_filters_for_rate_limiting(self):
        """list_tasks() (used for rate limiting) should still filter by user."""
        self._add_task("t1", "alice@example.com", channel="webui")
        self._add_task("t2", "bob@example.com", channel="webui")
        alice_tasks = self.mgr.list_tasks("webui", "alice@example.com")
        self.assertEqual(len(alice_tasks), 1)
        self.assertEqual(alice_tasks[0]["task_id"], "t1")

    def test_count_running_is_per_user(self):
        """count_running() should still be per-user for rate limiting."""
        self._add_task("t1", "alice@example.com", status="running")
        self._add_task("t2", "alice@example.com", status="running")
        self._add_task("t3", "bob@example.com", status="running")
        self.assertEqual(self.mgr.count_running("webui", "alice@example.com"), 2)
        self.assertEqual(self.mgr.count_running("webui", "bob@example.com"), 1)

    def test_count_queued_is_per_user(self):
        """count_queued() should still be per-user for rate limiting."""
        self._add_task("t1", "alice@example.com", status="queued")
        self._add_task("t2", "bob@example.com", status="queued")
        self._add_task("t3", "bob@example.com", status="queued")
        self.assertEqual(self.mgr.count_queued("webui", "alice@example.com"), 1)
        self.assertEqual(self.mgr.count_queued("webui", "bob@example.com"), 2)

    # ── list_all_tasks vs list_tasks divergence ────────────────────────────────

    def test_list_all_returns_superset_of_list_tasks(self):
        """list_all_tasks() >= list_tasks() for any user."""
        self._add_task("t1", "alice@example.com")
        self._add_task("t2", "bob@example.com")
        all_tasks = self.mgr.list_all_tasks()
        alice_tasks = self.mgr.list_tasks("webui", "alice@example.com")
        self.assertGreater(len(all_tasks), len(alice_tasks))

    # ── kill/delete accessible by task_id alone ────────────────────────────────

    def test_kill_task_works_regardless_of_caller(self):
        """kill_task() should work by task_id alone, no ownership check."""
        self._add_task("t1", "alice@example.com", status="queued")
        # Killing should succeed without needing to be alice
        result = self.mgr.kill_task("t1")
        self.assertTrue(result)
        task = self.mgr.get_task("t1")
        self.assertEqual(task["status"], "killed")

    def test_delete_task_works_regardless_of_caller(self):
        """delete_task() should work by task_id alone."""
        self._add_task("t1", "alice@example.com", status="completed")
        self.mgr.delete_task("t1")
        self.assertIsNone(self.mgr.get_task("t1"))


class TestVisibilityWithCrossChannel(unittest.TestCase):
    """Verify visibility works across channels."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.mgr = _make_mgr(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _add_task(self, task_id, identity, channel="webui", status="running"):
        return self.mgr.create_task(
            task_id=task_id,
            session_id=f"sess-{task_id}",
            user_identity=identity,
            channel=channel,
            agent="orchestrator",
            runtime="copilot",
            model="sonnet",
            prompt=f"test task {task_id}",
            pid=0,
            status=status,
        )

    def test_telegram_user_sees_webui_tasks(self):
        """Tasks from webui should appear in list_all_tasks for telegram user."""
        self._add_task("t1", "webui_user", channel="webui")
        all_tasks = self.mgr.list_all_tasks()
        self.assertEqual(len(all_tasks), 1)
        self.assertEqual(all_tasks[0]["channel"], "webui")

    def test_all_channels_mixed_in_list(self):
        """list_all_tasks merges tasks from webui, telegram, webex, api."""
        for i, ch in enumerate(["webui", "telegram", "webex", "api"]):
            self._add_task(f"t{i}", f"user_{ch}", channel=ch)
        tasks = self.mgr.list_all_tasks()
        channels = {t["channel"] for t in tasks}
        self.assertEqual(channels, {"webui", "telegram", "webex", "api"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
