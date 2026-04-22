"""Regression test for Issue #192: TOCTOU race in background task concurrency check.

Tests that:
1. create_task_checked() atomically enforces the concurrency limit
2. Concurrent simultaneous requests cannot both slip through the limit
3. Status is correctly set to 'running' or 'queued' based on the slot count
4. The method returns a (task, status) tuple
5. Multiple threads racing simultaneously never exceed max_concurrent
"""

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_manager import BackgroundTaskManager  # noqa: E402


class TestIssue192ToctouRace(unittest.TestCase):
    """Regression tests for Issue #192 — atomic check-and-create."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_file = os.path.join(self.temp_dir, "bg-tasks-test.json")

    def _make_manager(self, max_tasks=500):
        mgr = BackgroundTaskManager()
        mgr._path = self.temp_file
        mgr.MAX_TOTAL_TASKS = max_tasks
        mgr._cleanup_thread_started = True
        return mgr

    # ── Test 1: create_task_checked returns (task, status) tuple ──────────────

    def test_returns_task_and_status_tuple(self):
        mgr = self._make_manager()
        result = mgr.create_task_checked(
            task_id="toctou-1",
            session_id="sess-1",
            user_identity="user-a",
            channel="api",
            agent="orchestrator",
            runtime="copilot",
            model="claude-haiku",
            prompt="Hello",
            max_concurrent=5,
        )
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        task, status = result
        self.assertIn("task_id", task)
        self.assertEqual(task["task_id"], "toctou-1")
        self.assertIn(status, ("running", "queued"))

    # ── Test 2: first task runs, second queues when limit=1 ───────────────────

    def test_second_task_queued_at_limit(self):
        mgr = self._make_manager()
        _, status1 = mgr.create_task_checked(
            task_id="t1",
            session_id="s1",
            user_identity="user-a",
            channel="api",
            agent="orchestrator",
            runtime="copilot",
            model="m",
            prompt="p",
            max_concurrent=1,
        )
        _, status2 = mgr.create_task_checked(
            task_id="t2",
            session_id="s2",
            user_identity="user-a",
            channel="api",
            agent="orchestrator",
            runtime="copilot",
            model="m",
            prompt="p",
            max_concurrent=1,
        )
        self.assertEqual(status1, "running")
        self.assertEqual(status2, "queued")

    # ── Test 3: task runs when slot available ─────────────────────────────────

    def test_task_runs_when_below_limit(self):
        mgr = self._make_manager()
        _, status = mgr.create_task_checked(
            task_id="t1",
            session_id="s1",
            user_identity="user-a",
            channel="api",
            agent="orchestrator",
            runtime="copilot",
            model="m",
            prompt="p",
            max_concurrent=5,
        )
        self.assertEqual(status, "running")

    # ── Test 4: exactly max_concurrent tasks run, rest queue ─────────────────

    def test_exactly_max_concurrent_run(self):
        mgr = self._make_manager()
        max_c = 3
        statuses = []
        for i in range(6):
            _, s = mgr.create_task_checked(
                task_id=f"t{i}",
                session_id=f"s{i}",
                user_identity="user-a",
                channel="api",
                agent="orchestrator",
                runtime="copilot",
                model="m",
                prompt="p",
                max_concurrent=max_c,
            )
            statuses.append(s)
        running = statuses.count("running")
        queued = statuses.count("queued")
        self.assertEqual(running, max_c, f"Expected {max_c} running, got {running}")
        self.assertEqual(
            queued, 6 - max_c, f"Expected {6 - max_c} queued, got {queued}"
        )

    # ── Test 5: concurrent threads never exceed max_concurrent ───────────────

    def test_concurrent_threads_never_exceed_limit(self):
        """The core TOCTOU regression test.

        20 threads all call create_task_checked simultaneously with max_concurrent=5.
        After all complete, the number of 'running' tasks must be exactly 5
        (never more).
        """
        mgr = self._make_manager()
        max_c = 5
        n_threads = 20
        results = []
        errors = []
        barrier = threading.Barrier(n_threads)  # synchronize all threads

        def race():
            tid = threading.get_ident()
            task_id = f"toctou-{tid}"
            try:
                barrier.wait()  # all threads start simultaneously
                _, status = mgr.create_task_checked(
                    task_id=task_id,
                    session_id=f"sess-{tid}",
                    user_identity="user-concurrent",
                    channel="api",
                    agent="orchestrator",
                    runtime="copilot",
                    model="m",
                    prompt="race test",
                    max_concurrent=max_c,
                )
                results.append(status)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=race) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Thread errors: {errors}")
        self.assertEqual(len(results), n_threads)

        running_count = results.count("running")
        queued_count = results.count("queued")

        self.assertLessEqual(
            running_count,
            max_c,
            f"TOCTOU BUG: {running_count} tasks marked 'running'"
            f" but max_concurrent={max_c}",
        )
        self.assertEqual(
            running_count,
            max_c,
            f"Expected exactly {max_c} running tasks, got {running_count}",
        )
        self.assertEqual(
            queued_count,
            n_threads - max_c,
            f"Expected {n_threads - max_c} queued, got {queued_count}",
        )

    # ── Test 6: agent-scoped concurrency limit ────────────────────────────────

    def test_agent_scoped_concurrency(self):
        """Different agents have independent concurrency limits."""
        mgr = self._make_manager()
        # user fills agent-a slots
        for i in range(3):
            _, s = mgr.create_task_checked(
                task_id=f"a{i}",
                session_id=f"sa{i}",
                user_identity="user-x",
                channel="api",
                agent="agent-a",
                runtime="copilot",
                model="m",
                prompt="p",
                max_concurrent=3,
            )
        # agent-b should still get a 'running' slot (different agent scope)
        _, status_b = mgr.create_task_checked(
            task_id="b1",
            session_id="sb1",
            user_identity="user-x",
            channel="api",
            agent="agent-b",
            runtime="copilot",
            model="m",
            prompt="p",
            max_concurrent=3,
        )
        self.assertEqual(
            status_b,
            "running",
            "agent-b task should run (different agent from agent-a)",
        )

    # ── Test 7: task fields are correctly set ─────────────────────────────────

    def test_running_task_has_started_at(self):
        mgr = self._make_manager()
        task, status = mgr.create_task_checked(
            task_id="check-fields",
            session_id="s",
            user_identity="u",
            channel="api",
            agent="orchestrator",
            runtime="copilot",
            model="m",
            prompt="p",
            max_concurrent=5,
        )
        self.assertEqual(status, "running")
        self.assertIsNotNone(task.get("started_at"))
        self.assertIsNone(task.get("completed_at"))

    def test_queued_task_has_no_started_at(self):
        mgr = self._make_manager()
        # Fill the slot
        mgr.create_task_checked(
            task_id="blocker",
            session_id="s0",
            user_identity="u",
            channel="api",
            agent="orchestrator",
            runtime="copilot",
            model="m",
            prompt="p",
            max_concurrent=1,
        )
        task, status = mgr.create_task_checked(
            task_id="waiter",
            session_id="s1",
            user_identity="u",
            channel="api",
            agent="orchestrator",
            runtime="copilot",
            model="m",
            prompt="p",
            max_concurrent=1,
        )
        self.assertEqual(status, "queued")
        self.assertIsNone(task.get("started_at"))

    # ── Test 8: task is persisted to store ────────────────────────────────────

    def test_task_persisted_to_store(self):
        mgr = self._make_manager()
        mgr.create_task_checked(
            task_id="persist-me",
            session_id="s",
            user_identity="u",
            channel="api",
            agent="orchestrator",
            runtime="copilot",
            model="m",
            prompt="p",
            max_concurrent=5,
        )
        retrieved = mgr.get_task("persist-me")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["task_id"], "persist-me")


class TestIssue192EvictionCap(unittest.TestCase):
    """Regression: _evict_oldest_terminal must not exceed MAX_TOTAL_TASKS cap.

    Enforces that the task store count stays within the configured cap.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_file = os.path.join(self.temp_dir, "eviction-test.json")

    def _make_manager(self, max_tasks=3):
        mgr = BackgroundTaskManager()
        mgr._path = self.temp_file
        mgr.MAX_TOTAL_TASKS = max_tasks
        mgr._cleanup_thread_started = True
        return mgr

    def _add_terminal(self, mgr, n, status="completed"):
        """Add a terminal (completed/failed/killed) task directly."""
        mgr.create_task(
            task_id=f"terminal-{n}",
            session_id=f"s{n}",
            user_identity="evict-user",
            channel="api",
            agent="orchestrator",
            runtime="copilot",
            model="m",
            prompt=f"task {n}",
            status=status,
        )

    def test_store_never_exceeds_max_total_tasks_via_create_task(self):
        """create_task evicts terminal tasks so store stays at MAX_TOTAL_TASKS."""
        mgr = self._make_manager(max_tasks=3)
        # Pre-fill with completed tasks (all evictable)
        for i in range(3):
            mgr.create_task(
                task_id=f"cap-completed-{i}",
                session_id=f"sc{i}",
                user_identity="cap-user",
                channel="api",
                agent="orchestrator",
                runtime="copilot",
                model="m",
                prompt=f"task {i}",
                status="completed",
            )
        self.assertEqual(len(mgr.list_all_tasks()), 3)
        # Now add 3 more running tasks — each should evict one completed task
        for i in range(3, 6):
            mgr.create_task(
                task_id=f"cap-running-{i}",
                session_id=f"sr{i}",
                user_identity="cap-user",
                channel="api",
                agent="orchestrator",
                runtime="copilot",
                model="m",
                prompt=f"task {i}",
                status="running",
            )
        all_tasks = mgr.list_all_tasks()
        self.assertLessEqual(
            len(all_tasks),
            3,
            f"Store exceeded MAX_TOTAL_TASKS=3 after create_task:"
            f" {len(all_tasks)} tasks",
        )

    def test_store_never_exceeds_max_total_tasks_via_create_task_checked(self):
        """create_task_checked evicts terminal tasks; store stays at MAX_TOTAL_TASKS."""
        mgr = self._make_manager(max_tasks=3)
        # Pre-fill with completed tasks (all evictable)
        for i in range(3):
            mgr.create_task(
                task_id=f"pre-{i}",
                session_id=f"sp{i}",
                user_identity="cap-user",
                channel="api",
                agent="orchestrator",
                runtime="copilot",
                model="m",
                prompt=f"pre {i}",
                status="completed",
            )
        self.assertEqual(len(mgr.list_all_tasks()), 3)
        # Add 3 more via create_task_checked — each should evict one completed task
        for i in range(3, 6):
            mgr.create_task_checked(
                task_id=f"checked-{i}",
                session_id=f"s{i}",
                user_identity="cap-user",
                channel="api",
                agent="orchestrator",
                runtime="copilot",
                model="m",
                prompt=f"task {i}",
                max_concurrent=99,  # not testing concurrency here, just eviction
            )
        all_tasks = mgr.list_all_tasks()
        self.assertLessEqual(
            len(all_tasks),
            3,
            f"Store exceeded MAX_TOTAL_TASKS=3 via create_task_checked:"
            f" {len(all_tasks)} tasks",
        )

    def test_eviction_at_exact_cap_boundary(self):
        """When store is at MAX_TOTAL_TASKS, next create must evict one terminal."""
        mgr = self._make_manager(max_tasks=3)
        # Add 2 completed + 1 running = 3 total (AT cap)
        self._add_terminal(mgr, 1, "completed")
        self._add_terminal(mgr, 2, "completed")
        mgr.create_task(
            task_id="running-1",
            session_id="sr1",
            user_identity="u",
            channel="api",
            agent="orchestrator",
            runtime="copilot",
            model="m",
            prompt="r",
            status="running",
        )
        self.assertEqual(len(mgr.list_all_tasks()), 3, "Setup: expected 3 tasks at cap")
        # Now create one more — eviction must fire to stay at cap
        mgr.create_task(
            task_id="new-task",
            session_id="sn",
            user_identity="u",
            channel="api",
            agent="orchestrator",
            runtime="copilot",
            model="m",
            prompt="new",
            status="running",
        )
        all_tasks = mgr.list_all_tasks()
        self.assertLessEqual(
            len(all_tasks),
            3,
            f"EVICTION BUG: store has {len(all_tasks)} tasks"
            f" after create at exact cap boundary",
        )
        # The new task must be present
        task_ids = {t["task_id"] for t in all_tasks}
        self.assertIn("new-task", task_ids, "New task was not retained after eviction")


class TestIssue192EndpointConcurrency(unittest.TestCase):
    """Endpoint-level regression: concurrent POSTs to /api/v1/background-tasks
    must never allow both requests to get status='running' when there is only
    one available slot, proving the TOCTOU fix works end-to-end."""

    @classmethod
    def setUpClass(cls):
        import agent_manager as _am

        os.environ.setdefault("API_SHARED_KEY", "issue192concurrency")
        os.environ.setdefault("APP_ENV", "DEV")

        from fastapi.testclient import TestClient

        cls._am = _am
        cls.app = _am.create_api_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=False)
        cls.auth_header = {
            "Authorization": f"Bearer shared_{os.environ['API_SHARED_KEY']}",
            "X-User-Identity": "race-test-user-192",
            "X-Auth-Channel": "api",
        }
        # Access the bg_task_mgr created by create_api_app
        cls.bg_task_mgr = _am._session_mgr._bg_task_mgr
        # Redirect to an isolated temp file so we don't interfere with prod state
        cls.temp_dir = tempfile.mkdtemp()
        cls.temp_file = os.path.join(cls.temp_dir, "endpoint-race-test.json")
        cls.bg_task_mgr._path = cls.temp_file

    def setUp(self):
        # Clear task store before each test
        with self.bg_task_mgr._lock:
            self.bg_task_mgr._save_unlocked([])

    def test_two_concurrent_posts_at_limit_one_runs_one_queues(self):
        """Simultaneous requests when one slot remains: one 'running', one 'queued'.

        This is the endpoint-level proof that the TOCTOU race is closed.
        Without the atomic create_task_checked(), both requests would read
        count_running()=0 and both create tasks with status='running'.
        """
        original_max = BackgroundTaskManager.MAX_TASKS_PER_USER
        BackgroundTaskManager.MAX_TASKS_PER_USER = 1
        try:
            responses = []
            errors = []
            barrier = threading.Barrier(2)

            def post_task(n):
                try:
                    # Each thread gets its own client to avoid event-loop contention
                    from fastapi.testclient import TestClient

                    client = TestClient(self.app, raise_server_exceptions=False)
                    barrier.wait()  # All threads start simultaneously
                    resp = client.post(
                        "/api/v1/background-tasks",
                        json={
                            "prompt": f"race test {n}",
                            "agent": "orchestrator",
                            "runtime": "copilot",
                            "model": "m",
                            "timeout": 1,
                        },
                        headers=self.auth_header,
                    )
                    responses.append(resp.json())
                except Exception as exc:
                    errors.append(str(exc))

            threads = [threading.Thread(target=post_task, args=(i,)) for i in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            self.assertEqual(len(errors), 0, f"Thread errors: {errors}")
            self.assertEqual(
                len(responses), 2, f"Expected 2 responses, got: {responses}"
            )

            statuses = [r.get("status") for r in responses]
            running = statuses.count("running")
            queued = statuses.count("queued")

            self.assertLessEqual(
                running,
                1,
                f"TOCTOU BUG at API endpoint: {running} tasks got 'running'"
                f" with limit=1. "
                f"Responses: {responses}",
            )
            self.assertEqual(
                running, 1, f"Expected 1 running, got {running}. Responses: {responses}"
            )
            self.assertEqual(
                queued, 1, f"Expected 1 queued, got {queued}. Responses: {responses}"
            )
        finally:
            BackgroundTaskManager.MAX_TASKS_PER_USER = original_max



class TestIssue192SlashCommandPerAgentLimit(unittest.TestCase):
    """Regression: /background slash handler must use per-agent max_concurrent.

    The slash handler was passing BackgroundTaskManager.MAX_TASKS_PER_USER (the
    global cap) instead of the selected agent configured max_concurrent. An agent
    with max_concurrent=1 could still spawn a second running task via the slash
    command while the API path correctly enforced the limit.
    """

    def setUp(self):
        from agent_manager import SessionManager  # noqa: E402

        self.temp_dir = tempfile.mkdtemp()
        sm = SessionManager.__new__(SessionManager)
        sm._bg_task_mgr = BackgroundTaskManager()
        sm._bg_task_mgr._path = os.path.join(
            self.temp_dir, "bg-tasks-slash-test.json"
        )
        sm._bg_task_mgr._tasks_cache = None
        sm._bg_identity = "test-user-slash"
        sm.AGENTS = {
            "qa-agent": {"max_concurrent": 1, "path": "/fake/path"},
        }
        sm._execute_background_task = lambda *args, **kwargs: None
        self.sm = sm
        self.mgr = sm._bg_task_mgr

    def _call_slash(self, argument):
        session_data = {"channel": "telegram"}
        return self.sm._slash_background(
            argument, session_data, "n8n-test-session"
        )

    def test_slash_queues_when_agent_limit_reached(self):
        """Second /background for agent at max_concurrent=1 must be queued."""
        self.mgr.create_task(
            task_id="seed-running",
            session_id="seed-sess",
            user_identity="test-user-slash",
            channel="telegram",
            agent="qa-agent",
            runtime="claude",
            model="sonnet",
            prompt="seed task",
            status="running",
        )
        result = self._call_slash("agent=qa-agent second task")
        tasks = self.mgr.list_tasks("telegram", "test-user-slash")
        running = [t for t in tasks if t["status"] == "running"]
        queued = [t for t in tasks if t["status"] == "queued"]
        self.assertEqual(
            len(running),
            1,
            f"Expected 1 running, got {len(running)}. Tasks: {tasks}",
        )
        self.assertEqual(
            len(queued),
            1,
            f"Expected 1 queued, got {len(queued)}. Tasks: {tasks}",
        )
        self.assertIn(
            "queued",
            result.lower(),
            f"Expected queued in response, got: {result!r}",
        )

    def test_slash_runs_when_slot_available(self):
        """First /background for agent with open slot must start running."""
        result = self._call_slash("agent=qa-agent first task")
        tasks = self.mgr.list_tasks("telegram", "test-user-slash")
        running = [t for t in tasks if t["status"] == "running"]
        self.assertEqual(
            len(running),
            1,
            f"Expected 1 running, got {len(running)}. Tasks: {tasks}",
        )
        self.assertNotIn(
            "queued",
            result.lower(),
            f"Task should start but response says queued: {result!r}",
        )

    def test_slash_global_limit_not_used_for_per_agent_cap(self):
        """Slash must NOT use global MAX_TASKS_PER_USER when agent cap is lower."""
        original_max = BackgroundTaskManager.MAX_TASKS_PER_USER
        BackgroundTaskManager.MAX_TASKS_PER_USER = 5
        try:
            self.mgr.create_task(
                task_id="seed-global-test",
                session_id="seed-sess-g",
                user_identity="test-user-slash",
                channel="telegram",
                agent="qa-agent",
                runtime="claude",
                model="sonnet",
                prompt="seed",
                status="running",
            )
            self._call_slash("agent=qa-agent task two")
            tasks = self.mgr.list_tasks("telegram", "test-user-slash")
            running = [t for t in tasks if t["status"] == "running"]
            self.assertLessEqual(
                len(running),
                1,
                f"Slash bypassed per-agent cap: {len(running)} running."
                f" Tasks: {tasks}",
            )
        finally:
            BackgroundTaskManager.MAX_TASKS_PER_USER = original_max


if __name__ == "__main__":
    unittest.main()
