"""
Tests for background task queue feature (641eb74):
- Queuing when concurrent limit is hit
- promote_queued_task() transitions status to "running"
- get_next_queued() returns oldest queued task (FIFO)
- count_queued() / count_running() counts
- kill_task() on a queued task cancels without SIGTERM
- Queue position reported correctly
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_manager import BackgroundTaskManager


def _make_mgr(tmp_path: str) -> BackgroundTaskManager:
    """Create a BackgroundTaskManager that writes to a temp file."""
    mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
    mgr._path = tmp_path
    mgr._lock = threading.Lock()
    return mgr


def _task_id(n: int) -> str:
    return f"task-{n:04d}"


class TestBackgroundTaskManagerQueue(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.mgr = _make_mgr(self.tmp.name)
        self.channel = "webex"
        self.identity = "testuser@example.com"

    def tearDown(self):
        os.unlink(self.tmp.name)

    # ── helpers ────────────────────────────────────────────────────────────────

    def _add(self, n: int, status: str = "running") -> dict:
        return self.mgr.create_task(
            task_id=_task_id(n),
            session_id=f"sess-{n}",
            user_identity=self.identity,
            channel=self.channel,
            agent="fosterbot",
            runtime="claude",
            model="sonnet",
            prompt=f"test task {n}",
            pid=0,
            status=status,
        )

    # ── count_running / count_queued ───────────────────────────────────────────

    def test_count_running_empty(self):
        self.assertEqual(self.mgr.count_running(self.channel, self.identity), 0)

    def test_count_queued_empty(self):
        self.assertEqual(self.mgr.count_queued(self.channel, self.identity), 0)

    def test_count_running_counts_only_running(self):
        self._add(1, "running")
        self._add(2, "running")
        self._add(3, "queued")
        self._add(4, "completed")
        self.assertEqual(self.mgr.count_running(self.channel, self.identity), 2)

    def test_count_queued_counts_only_queued(self):
        self._add(1, "running")
        self._add(2, "queued")
        self._add(3, "queued")
        self._add(4, "completed")
        self.assertEqual(self.mgr.count_queued(self.channel, self.identity), 2)

    # ── get_next_queued ────────────────────────────────────────────────────────

    def test_get_next_queued_none_when_empty(self):
        self.assertIsNone(self.mgr.get_next_queued(self.channel, self.identity))

    def test_get_next_queued_none_when_only_running(self):
        self._add(1, "running")
        self.assertIsNone(self.mgr.get_next_queued(self.channel, self.identity))

    def test_get_next_queued_returns_oldest_fifo(self):
        # Insert with 1-second gaps so created_at differs
        self._add(1, "queued")
        time.sleep(1.1)
        self._add(2, "queued")
        nxt = self.mgr.get_next_queued(self.channel, self.identity)
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt["task_id"], _task_id(1))

    def test_get_next_queued_ignores_running_and_completed(self):
        self._add(1, "running")
        self._add(2, "completed")
        self._add(3, "queued")
        nxt = self.mgr.get_next_queued(self.channel, self.identity)
        self.assertEqual(nxt["task_id"], _task_id(3))

    # ── promote_queued_task ────────────────────────────────────────────────────

    def test_promote_queued_task_changes_status_to_running(self):
        self._add(1, "queued")
        self.mgr.promote_queued_task(_task_id(1), "new-session-id")
        task = self.mgr.get_task(_task_id(1))
        self.assertEqual(task["status"], "running")

    def test_promote_queued_task_updates_session_id(self):
        self._add(1, "queued")
        self.mgr.promote_queued_task(_task_id(1), "fresh-sess-xyz")
        task = self.mgr.get_task(_task_id(1))
        self.assertEqual(task["session_id"], "fresh-sess-xyz")

    def test_promote_queued_task_sets_started_at(self):
        self._add(1, "queued")
        before = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.mgr.promote_queued_task(_task_id(1), "s")
        task = self.mgr.get_task(_task_id(1))
        self.assertIsNotNone(task.get("started_at"))
        self.assertGreaterEqual(task["started_at"], before)

    def test_promote_does_not_affect_other_tasks(self):
        self._add(1, "queued")
        self._add(2, "queued")
        self.mgr.promote_queued_task(_task_id(1), "s1")
        task2 = self.mgr.get_task(_task_id(2))
        self.assertEqual(task2["status"], "queued")

    # ── kill_task on queued ────────────────────────────────────────────────────

    def test_kill_queued_task_sets_status_killed(self):
        self._add(1, "queued")
        result = self.mgr.kill_task(_task_id(1))
        self.assertTrue(result)
        task = self.mgr.get_task(_task_id(1))
        self.assertEqual(task["status"], "killed")

    def test_kill_queued_task_does_not_send_sigterm(self):
        """kill_task on a queued task must NOT call os.kill (no PID)."""
        self._add(1, "queued")
        with patch("os.kill") as mock_kill:
            self.mgr.kill_task(_task_id(1))
            mock_kill.assert_not_called()

    def test_kill_queued_task_sets_completed_at(self):
        self._add(1, "queued")
        before = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.mgr.kill_task(_task_id(1))
        task = self.mgr.get_task(_task_id(1))
        self.assertIsNotNone(task.get("completed_at"))
        self.assertGreaterEqual(task["completed_at"], before)

    def test_kill_nonexistent_task_returns_false(self):
        self.assertFalse(self.mgr.kill_task("does-not-exist"))

    def test_kill_completed_task_returns_false(self):
        self._add(1, "completed")
        self.assertFalse(self.mgr.kill_task(_task_id(1)))

    # ── create_task with status=queued ─────────────────────────────────────────

    def test_create_queued_task_has_null_started_at(self):
        task = self._add(1, "queued")
        self.assertIsNone(task["started_at"])

    def test_create_running_task_has_started_at(self):
        task = self._add(1, "running")
        self.assertIsNotNone(task["started_at"])

    # ── queue position counting ────────────────────────────────────────────────

    def test_queue_position_increments(self):
        """After queuing N tasks, count_queued() should equal N."""
        for i in range(1, 6):
            self._add(i, "queued")
        self.assertEqual(self.mgr.count_queued(self.channel, self.identity), 5)

    def test_queue_position_decrements_on_promote(self):
        for i in range(1, 4):
            self._add(i, "queued")
        self.mgr.promote_queued_task(_task_id(1), "s")
        self.assertEqual(self.mgr.count_queued(self.channel, self.identity), 2)
        self.assertEqual(self.mgr.count_running(self.channel, self.identity), 1)

    # ── cross-user isolation ───────────────────────────────────────────────────

    def test_queue_counts_are_per_user(self):
        """Queued tasks from user A must not appear in user B's counts."""
        self._add(1, "queued")
        other = "other-user@example.com"
        count = self.mgr.count_queued(self.channel, other)
        self.assertEqual(count, 0)

    def test_get_next_queued_is_per_user(self):
        self._add(1, "queued")
        other = "other-user@example.com"
        self.assertIsNone(self.mgr.get_next_queued(self.channel, other))


class TestBackgroundTaskManagerQueueIntegration(unittest.TestCase):
    """
    Integration-style: simulate the full flow of hitting the limit, queuing,
    and promoting via the same methods the API uses.
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.mgr = _make_mgr(self.tmp.name)
        self.channel = "webex"
        self.identity = "integ-user@example.com"

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _submit(self, n: int):
        """Simulate what the API endpoint does: queue if at limit, else run."""
        running = self.mgr.count_running(self.channel, self.identity)
        status = "queued" if running >= BackgroundTaskManager.MAX_TASKS_PER_USER else "running"
        return self.mgr.create_task(
            task_id=_task_id(n),
            session_id=f"sess-{n}",
            user_identity=self.identity,
            channel=self.channel,
            agent="fosterbot",
            runtime="claude",
            model="sonnet",
            prompt=f"task {n}",
            pid=0,
            status=status,
        )

    def test_sixth_task_is_queued(self):
        for i in range(1, 7):
            t = self._submit(i)
        self.assertEqual(self.mgr.count_running(self.channel, self.identity), 5)
        self.assertEqual(self.mgr.count_queued(self.channel, self.identity), 1)
        t6 = self.mgr.get_task(_task_id(6))
        self.assertEqual(t6["status"], "queued")

    def test_completing_a_task_allows_queue_promotion(self):
        """Simulate a task finishing and promoting the next queued one."""
        for i in range(1, 8):  # 5 running + 2 queued
            self._submit(i)

        self.assertEqual(self.mgr.count_running(self.channel, self.identity), 5)
        self.assertEqual(self.mgr.count_queued(self.channel, self.identity), 2)

        # Simulate task 1 completing → promote next queued
        self.mgr.complete_task(_task_id(1), "done")
        nxt = self.mgr.get_next_queued(self.channel, self.identity)
        self.assertIsNotNone(nxt)
        self.mgr.promote_queued_task(nxt["task_id"], "promoted-sess")

        self.assertEqual(self.mgr.count_running(self.channel, self.identity), 5)
        self.assertEqual(self.mgr.count_queued(self.channel, self.identity), 1)

    def test_fifo_promotion_order(self):
        """First queued task should be promoted first."""
        for i in range(1, 7):  # 5 running + 1 queued (task 6)
            self._submit(i)
        time.sleep(1.1)
        self._submit(7)  # second queued (task 7)

        self.assertEqual(self.mgr.count_queued(self.channel, self.identity), 2)

        # Complete task 1
        self.mgr.complete_task(_task_id(1), "done")
        nxt = self.mgr.get_next_queued(self.channel, self.identity)
        self.assertEqual(nxt["task_id"], _task_id(6))  # oldest queued first


if __name__ == "__main__":
    unittest.main(verbosity=2)
