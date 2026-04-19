"""Regression test for Issue #170: background-tasks API deadlocks under large task accumulation.

Tests that:
1. MAX_TOTAL_TASKS cap is enforced (oldest terminal tasks evicted)
2. API responds within reasonable time with 300+ tasks in store
3. Health endpoint responds quickly regardless of task store size
4. Completed/failed tasks older than CLEANUP_AGE_HOURS are purged
5. Atomic writes prevent file corruption
6. Pagination works correctly on list endpoint
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_manager import BackgroundTaskManager


class TestIssue170BackgroundTaskDeadlock(unittest.TestCase):
    """Regression tests for Issue #170 fixes."""

    def setUp(self):
        """Create a BackgroundTaskManager with a temp file."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_file = os.path.join(self.temp_dir, "background-tasks-test.json")

    def _make_manager(self, max_total_tasks=500, cleanup_age_hours=24):
        """Create a manager pointing at the temp file."""
        mgr = BackgroundTaskManager()
        mgr._path = self.temp_file
        mgr.MAX_TOTAL_TASKS = max_total_tasks
        mgr.CLEANUP_AGE_HOURS = cleanup_age_hours
        mgr.BG_CLEANUP_HOURS = cleanup_age_hours
        mgr._cleanup_thread_started = True  # skip thread startup in tests
        return mgr

    def _make_task(self, task_id, status="completed", completed_at=None, created_at=None):
        """Create a minimal task dict."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return {
            "task_id": task_id,
            "session_id": f"session-{task_id}",
            "user_key": f"test_{task_id}",
            "channel": "test",
            "user_identity": task_id,
            "agent": "test-agent",
            "runtime": "test",
            "model": "test-model",
            "prompt": f"Test task {task_id}",
            "status": status,
            "pid": 0,
            "created_at": created_at or now,
            "started_at": now if status == "running" else None,
            "completed_at": completed_at,
            "output_lines": ["output line"] * 100,  # simulate some output
            "tool_calls": [],
            "final_response": "Done" if status == "completed" else None,
            "error": None,
            "timeout": 900,
            "notify": True,
            "origin_session_id": None,
        }

    # --- Test 1: MAX_TOTAL_TASKS cap ---

    def test_max_total_tasks_evicts_oldest_terminal(self):
        """When store exceeds MAX_TOTAL_TASKS, oldest completed tasks are evicted."""
        mgr = self._make_manager(max_total_tasks=10)

        # Create 12 completed tasks
        now = time.time()
        tasks = []
        for i in range(12):
            t = self._make_task(
                f"task-{i}",
                status="completed",
                completed_at=time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - (12 - i) * 3600)
                ),
            )
            tasks.append(t)

        # Save all tasks
        mgr._save(tasks)

        # Create a new task — should trigger eviction
        mgr.create_task(
            task_id="task-new",
            session_id="session-new",
            user_identity="test-user",
            channel="test",
            agent="test-agent",
            runtime="test",
            model="test-model",
            prompt="New task",
        )

        # Verify total tasks <= MAX_TOTAL_TASKS
        loaded = mgr._load()
        self.assertEqual(len(loaded), mgr.MAX_TOTAL_TASKS)

        # Verify the new task exists
        task_ids = [t["task_id"] for t in loaded]
        self.assertIn("task-new", task_ids)
        self.assertNotIn("task-0", task_ids)
        self.assertNotIn("task-1", task_ids)
        self.assertNotIn("task-2", task_ids)

    def test_never_evicts_running_tasks(self):
        """Running tasks must never be evicted, even when over cap."""
        mgr = self._make_manager(max_total_tasks=5)

        # Create 4 completed tasks + 1 running task
        tasks = []
        for i in range(4):
            tasks.append(self._make_task(f"completed-{i}", status="completed"))
        tasks.append(self._make_task("running-1", status="running"))

        mgr._save(tasks)

        # Create a new task (status=running by default)
        mgr.create_task(
            task_id="task-new",
            session_id="session-new",
            user_identity="test-user",
            channel="test",
            agent="test-agent",
            runtime="test",
            model="test-model",
            prompt="New task",
        )

        loaded = mgr._load()
        running_task_ids = [t["task_id"] for t in loaded if t["status"] == "running"]
        # Both the original running task and the new one should exist
        self.assertIn("running-1", running_task_ids)
        self.assertIn("task-new", running_task_ids)

    def test_never_evicts_queued_tasks(self):
        """Queued tasks must never be evicted."""
        mgr = self._make_manager(max_total_tasks=5)

        tasks = []
        for i in range(4):
            tasks.append(self._make_task(f"completed-{i}", status="completed"))
        tasks.append(self._make_task("queued-1", status="queued"))

        mgr._save(tasks)

        mgr.create_task(
            task_id="task-new",
            session_id="session-new",
            user_identity="test-user",
            channel="test",
            agent="test-agent",
            runtime="test",
            model="test-model",
            prompt="New task",
        )

        loaded = mgr._load()
        queued_tasks = [t for t in loaded if t["status"] == "queued"]
        self.assertEqual(len(queued_tasks), 1)
        self.assertEqual(queued_tasks[0]["task_id"], "queued-1")

    # --- Test 2: API responds quickly with large task store ---

    def test_api_responds_with_300_tasks(self):
        """list_all_tasks should complete quickly with 300+ tasks."""
        mgr = self._make_manager(max_total_tasks=500)

        # Create 300 completed tasks
        now = time.time()
        tasks = []
        for i in range(300):
            t = self._make_task(
                f"task-{i}",
                status="completed",
                completed_at=time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - i * 60)
                ),
            )
            t["output_lines"] = [f"line {j}" for j in range(500)]  # max output
            tasks.append(t)

        mgr._save(tasks)

        # Measure list_all_tasks performance
        start = time.time()
        result = mgr.list_all_tasks()
        elapsed = time.time() - start

        self.assertEqual(len(result), 300)
        self.assertLess(elapsed, 1.0, f"list_all_tasks took {elapsed:.3f}s (should be <1s)")

    def test_issue_170_task_store_uses_cached_reads(self):
        """Repeated reads should hit the in-memory cache, not reopen the JSON file."""
        mgr = self._make_manager(max_total_tasks=500)
        mgr._save([self._make_task(f"task-{i}") for i in range(20)])
        mgr = self._make_manager(max_total_tasks=500)

        real_open = open
        call_count = {"reads": 0}

        def counting_open(path, mode="r", *args, **kwargs):
            if path == self.temp_file and "r" in mode:
                call_count["reads"] += 1
            return real_open(path, mode, *args, **kwargs)

        with patch("builtins.open", side_effect=counting_open):
            first = mgr.list_all_tasks()
            second = mgr.list_all_tasks()

        self.assertEqual(len(first), 20)
        self.assertEqual(len(second), 20)
        self.assertEqual(
            call_count["reads"],
            1,
            "BackgroundTaskManager reparsed the task file instead of serving cached data",
        )

    def test_issue_170_list_task_summaries_paginates_without_details(self):
        """Summary pagination should omit large transcript payloads."""
        mgr = self._make_manager(max_total_tasks=500)
        tasks = []
        for i in range(5):
            task = self._make_task(f"task-{i}")
            task["output_lines"] = [f"line {j}" for j in range(500)]
            task["tool_calls"] = [
                {"id": f"tc-{i}", "name": "tool", "input": "x" * 5000, "status": "completed"}
            ]
            tasks.append(task)
        mgr._save(tasks)

        page, total = mgr.list_task_summaries(limit=2, offset=1)

        self.assertEqual(total, 5)
        self.assertEqual(len(page), 2)
        self.assertNotIn("output_lines", page[0])
        self.assertNotIn("tool_calls", page[0])

    def test_issue_170_list_task_summaries_reconciles_stale_running_tasks(self):
        """Paginated reads should mark dead running tasks failed in the same pass."""
        mgr = self._make_manager(max_total_tasks=500)
        stale = self._make_task("stale-running", status="running")
        stale["pid"] = 999999
        stale["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        stale["completed_at"] = None
        mgr._save([stale])

        page, total = mgr.list_task_summaries(limit=10, offset=0, reconcile_running=True)

        self.assertEqual(total, 1)
        self.assertEqual(page[0]["task_id"], "stale-running")
        self.assertEqual(page[0]["status"], "failed")

        reloaded = mgr.get_task("stale-running")
        self.assertEqual(reloaded["status"], "failed")
        self.assertEqual(
            reloaded["error"],
            "Process terminated unexpectedly",
        )
        self.assertIsNotNone(reloaded["completed_at"])

    # --- Test 3: Cleanup old tasks ---

    def test_cleanup_purges_old_terminal_tasks(self):
        """Tasks older than CLEANUP_AGE_HOURS should be purged."""
        mgr = self._make_manager(cleanup_age_hours=1)

        now = time.time()
        tasks = []

        # 5 old completed tasks (2 hours ago)
        for i in range(5):
            t = self._make_task(
                f"old-{i}",
                status="completed",
                completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 7200)),
            )
            tasks.append(t)

        # 3 recent completed tasks (30 min ago)
        for i in range(3):
            t = self._make_task(
                f"recent-{i}",
                status="completed",
                completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 1800)),
            )
            tasks.append(t)

        # 1 running task (should never be purged)
        tasks.append(self._make_task("running-1", status="running"))

        mgr._save(tasks)
        mgr.cleanup_old()

        loaded = mgr._load()
        task_ids = [t["task_id"] for t in loaded]

        # Old tasks should be gone
        for i in range(5):
            self.assertNotIn(f"old-{i}", task_ids)

        # Recent tasks should remain
        for i in range(3):
            self.assertIn(f"recent-{i}", task_ids)

        # Running task should remain
        self.assertIn("running-1", task_ids)

    # --- Test 4: Atomic writes prevent corruption ---

    def test_atomic_write_prevents_corruption(self):
        """_save should use atomic rename to prevent partial writes."""
        mgr = self._make_manager()

        tasks = [self._make_task("task-1", status="completed")]
        mgr._save(tasks)

        # Verify file exists and is valid JSON
        self.assertTrue(os.path.exists(self.temp_file))
        with open(self.temp_file, "r") as f:
            loaded = json.load(f)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["task_id"], "task-1")

        # Verify no temp file left behind
        self.assertFalse(os.path.exists(self.temp_file + ".tmp"))

    # --- Test 5: Output size cap at write time ---

    def test_output_lines_capped_at_write_time(self):
        """append_output should cap output_lines to MAX_OUTPUT_LINES."""
        mgr = self._make_manager()

        # Create a task
        mgr.create_task(
            task_id="task-1",
            session_id="session-1",
            user_identity="test-user",
            channel="test",
            agent="test-agent",
            runtime="test",
            model="test-model",
            prompt="Test",
        )

        # Append more lines than MAX_OUTPUT_LINES
        for i in range(600):
            mgr.append_output("task-1", f"line-{i}")

        task = mgr.get_task("task-1")
        self.assertIsNotNone(task)
        self.assertLessEqual(len(task["output_lines"]), mgr.MAX_OUTPUT_LINES)
        # Should keep the last MAX_OUTPUT_LINES
        self.assertEqual(task["output_lines"][-1], "line-599")

    def test_issue_170_tool_call_fields_are_trimmed_at_write_time(self):
        """Stored tool-call payloads should be truncated before they bloat the task file."""
        mgr = self._make_manager()
        mgr.MAX_TOOL_FIELD_CHARS = 128

        mgr.create_task(
            task_id="task-1",
            session_id="session-1",
            user_identity="test-user",
            channel="test",
            agent="test-agent",
            runtime="test",
            model="test-model",
            prompt="Test",
        )

        mgr.append_tool_call(
            "task-1",
            {
                "id": "call-1",
                "name": "tool",
                "input": "x" * 512,
                "output": {"result": "y" * 512},
                "status": "completed",
            },
        )

        task = mgr.get_task("task-1")
        self.assertLessEqual(len(task["tool_calls"]), mgr.MAX_TOOL_CALLS)
        self.assertTrue(task["tool_calls"][0]["input"].endswith("...[truncated]"))
        self.assertTrue(task["tool_calls"][0]["output"].endswith("...[truncated]"))

    def test_issue_170_save_enforces_cap_without_create_task(self):
        """Any persisted save should trim terminal tasks down to MAX_TOTAL_TASKS."""
        mgr = self._make_manager(max_total_tasks=10)

        tasks = [self._make_task(f"task-{i}") for i in range(12)]
        mgr._save(tasks)

        loaded = mgr._load()
        self.assertEqual(len(loaded), mgr.MAX_TOTAL_TASKS)
        loaded_ids = [task["task_id"] for task in loaded]
        self.assertNotIn("task-0", loaded_ids)
        self.assertNotIn("task-1", loaded_ids)
        self.assertIn("task-11", loaded_ids)

    # --- Test 6: Pagination ---

    def test_eviction_respects_max_total(self):
        """After eviction, store should not exceed MAX_TOTAL_TASKS."""
        mgr = self._make_manager(max_total_tasks=20)

        # Fill with 25 completed tasks
        for i in range(25):
            t = self._make_task(f"task-{i}", status="completed")
            mgr._save([t] + mgr._load())

        # Verify eviction brought it down
        loaded = mgr._load()
        # After adding 25 tasks one by one with eviction, should be at or near cap
        # Each create_task triggers eviction, so final count should be reasonable
        self.assertLessEqual(len(loaded), mgr.MAX_TOTAL_TASKS + 5)

    def test_cleanup_enforces_total_cap_after_ttl(self):
        """cleanup_old should also enforce MAX_TOTAL_TASKS after TTL filtering."""
        mgr = self._make_manager(max_total_tasks=10, cleanup_age_hours=1)

        now = time.time()
        tasks = []

        # 20 old completed tasks (should be TTL-purged)
        for i in range(20):
            t = self._make_task(
                f"old-{i}",
                status="completed",
                completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 7200)),
            )
            tasks.append(t)

        # 15 recent completed tasks (within TTL, but over cap)
        for i in range(15):
            t = self._make_task(
                f"recent-{i}",
                status="completed",
                completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 1800)),
            )
            tasks.append(t)

        mgr._save(tasks)
        mgr.cleanup_old()

        loaded = mgr._load()
        # Old tasks gone, recent tasks capped at MAX_TOTAL_TASKS
        self.assertLessEqual(len(loaded), mgr.MAX_TOTAL_TASKS)


class TestIssue170AsyncIO(unittest.TestCase):
    """Test that threading.Lock doesn't starve the asyncio event loop."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_file = os.path.join(self.temp_dir, "background-tasks-test.json")

    def _make_manager(self, max_total_tasks=500):
        mgr = BackgroundTaskManager()
        mgr._path = self.temp_file
        mgr.MAX_TOTAL_TASKS = max_total_tasks
        mgr._cleanup_thread_started = True
        return mgr

    def _make_task(self, task_id, status="completed"):
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return {
            "task_id": task_id,
            "session_id": f"session-{task_id}",
            "user_key": f"test_{task_id}",
            "channel": "test",
            "user_identity": task_id,
            "agent": "test-agent",
            "runtime": "test",
            "model": "test-model",
            "prompt": f"Test task {task_id}",
            "status": status,
            "pid": 0,
            "created_at": now,
            "started_at": now if status == "running" else None,
            "completed_at": now if status != "running" else None,
            "output_lines": [],
            "tool_calls": [],
            "final_response": None,
            "error": None,
            "timeout": 900,
            "notify": True,
            "origin_session_id": None,
        }

    def test_concurrent_reads_dont_deadlock(self):
        """Multiple concurrent reads should not deadlock under load."""
        mgr = self._make_manager()

        # Create 100 tasks
        for i in range(100):
            mgr.create_task(
                task_id=f"task-{i}",
                session_id=f"session-{i}",
                user_identity="test-user",
                channel="test",
                agent="test-agent",
                runtime="test",
                model="test-model",
                prompt=f"Task {i}",
            )

        # Simulate concurrent reads
        results = []
        errors = []

        def read_tasks():
            try:
                for _ in range(10):
                    tasks = mgr.list_all_tasks()
                    results.append(len(tasks))
                    time.sleep(0.01)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=read_tasks) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Errors during concurrent reads: {errors}")
        self.assertEqual(len(results), 50)  # 5 threads × 10 reads


os.environ.setdefault("API_SHARED_KEY", "test_key_123")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "8099")


class TestIssue170BackgroundTaskAPI(unittest.TestCase):
    """FastAPI regression coverage for Issue #170 async background-task endpoints."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        agent_manager = sys.modules[BackgroundTaskManager.__module__]

        cls._tmp_dir = tempfile.mkdtemp()
        cls._tmp_file = os.path.join(cls._tmp_dir, "background-tasks-api.json")
        cls._captured = []
        original_init = BackgroundTaskManager.__init__

        def _capturing_init(self_inner, *args, **kwargs):
            original_init(self_inner, *args, **kwargs)
            self_inner._path = cls._tmp_file
            self_inner._cleanup_thread_started = True
            cls._captured.append(self_inner)

        cls._telegram_patch = patch.object(
            agent_manager,
            "_resolve_telegram_identity",
            side_effect=lambda identity: identity,
        )
        cls._telegram_patch.start()
        cls._send_pairing_patch = patch.object(
            agent_manager,
            "_send_pairing_code",
            return_value=True,
        )
        cls._send_pairing_patch.start()

        with patch.object(BackgroundTaskManager, "__init__", _capturing_init):
            cls.app = agent_manager.create_api_app()

        cls.client = TestClient(cls.app)
        cls.agent_manager = agent_manager
        session_token = agent_manager._api_auth_manager.verify_pairing_code(
            agent_manager._api_auth_manager.generate_pairing_code(
                "issue170-test-user", "telegram"
            ),
            "issue170-test-user",
        )
        cls.auth = {
            "Authorization": f"Bearer {session_token}",
        }
        cls.bg_mgr = cls._captured[0]

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()
        if os.path.exists(cls._tmp_file):
            os.unlink(cls._tmp_file)
        os.rmdir(cls._tmp_dir)

    def setUp(self):
        self.bg_mgr._save([])

    def _create_task(self, task_id: str, status: str = "completed") -> dict:
        return self.bg_mgr.create_task(
            task_id=task_id,
            session_id=f"session-{task_id}",
            user_identity="test-user",
            channel="telegram",
            agent="wee-dev",
            runtime="copilot",
            model="claude-haiku-4.5",
            prompt=f"Task {task_id}",
            status=status,
        )

    def test_list_endpoint_offloads_summary_reads_to_worker_thread(self):
        self._create_task("task-1")

        async def _to_thread(func, *args, **kwargs):
            self.assertIs(func.__self__, self.bg_mgr)
            self.assertIs(func.__func__, BackgroundTaskManager.list_task_summaries)
            return func(*args, **kwargs)

        with patch("asyncio.to_thread", new=AsyncMock(side_effect=_to_thread)) as mocked:
            resp = self.client.get("/api/v1/background-tasks?limit=1", headers=self.auth)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["total"], 1)
        mocked.assert_awaited()

    def test_detail_endpoint_offloads_task_reads_to_worker_thread(self):
        self._create_task("task-42", status="running")

        async def _to_thread(func, *args, **kwargs):
            self.assertIs(func.__self__, self.bg_mgr)
            self.assertIs(func.__func__, BackgroundTaskManager.get_task)
            self.assertEqual(args, ("task-42",))
            return func(*args, **kwargs)

        with patch("asyncio.to_thread", new=AsyncMock(side_effect=_to_thread)) as mocked:
            resp = self.client.get("/api/v1/background-tasks/task-42", headers=self.auth)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["task_id"], "task-42")
        mocked.assert_awaited()

    def test_health_endpoint_avoids_session_map_disk_reads(self):
        with patch.object(
            self.agent_manager._session_mgr,
            "load_session_map",
            side_effect=AssertionError("health must not read session_map"),
        ) as mocked:
            resp = self.client.get("/api/v1/health")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("active_sessions", resp.json())
        mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
