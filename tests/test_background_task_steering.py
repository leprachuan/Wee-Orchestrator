"""
Tests for background task steering feature (fc79f69):
- BackgroundTaskManager.write_steering() - creates/appends to steering file
- BackgroundTaskManager.read_steering() - returns content or None
- BackgroundTaskManager.cleanup_steering() - removes steering file
- BackgroundTaskManager.get_steering_path() - returns correct path
- POST /api/v1/background-tasks/{task_id}/steer - 200/404/409/422
- GET /api/v1/background-tasks/{task_id}/steering - 200/404
- Bot command /background steer <task_id> <instruction>
"""

import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_manager import BackgroundTaskManager


def _make_mgr(tmp_path: str, steering_dir: str) -> BackgroundTaskManager:
    """Create a BackgroundTaskManager with a temp tasks file and steering dir."""
    mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
    mgr._path = tmp_path
    mgr._lock = threading.Lock()
    mgr.STEERING_DIR = steering_dir
    return mgr


class TestBackgroundTaskManagerSteering(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.steering_tmpdir = tempfile.mkdtemp()
        self.mgr = _make_mgr(self.tmp.name, self.steering_tmpdir)

    def tearDown(self):
        os.unlink(self.tmp.name)
        shutil.rmtree(self.steering_tmpdir, ignore_errors=True)

    # ── get_steering_path ──────────────────────────────────────────────────────

    def test_get_steering_path_returns_md_file(self):
        path = self.mgr.get_steering_path("task-001")
        self.assertTrue(path.endswith("task-001.md"))
        self.assertIn(self.steering_tmpdir, path)

    # ── write_steering ─────────────────────────────────────────────────────────

    def test_write_steering_creates_file(self):
        path = self.mgr.write_steering("task-abc", "Do this first")
        self.assertTrue(os.path.exists(path))

    def test_write_steering_first_entry_has_header(self):
        path = self.mgr.write_steering("task-abc", "Do this first")
        content = open(path).read()
        self.assertIn("## Steering Instructions", content)
        self.assertIn("Do this first", content)

    def test_write_steering_append_second_entry_no_duplicate_header(self):
        path = self.mgr.write_steering("task-abc", "First instruction")
        self.mgr.write_steering("task-abc", "Second instruction")
        content = open(path).read()
        self.assertEqual(content.count("## Steering Instructions"), 1)
        self.assertIn("First instruction", content)
        self.assertIn("Second instruction", content)

    def test_write_steering_returns_correct_path(self):
        path = self.mgr.write_steering("task-xyz", "Go faster")
        expected = self.mgr.get_steering_path("task-xyz")
        self.assertEqual(path, expected)

    # ── read_steering ──────────────────────────────────────────────────────────

    def test_read_steering_returns_none_when_no_file(self):
        result = self.mgr.read_steering("nonexistent-task")
        self.assertIsNone(result)

    def test_read_steering_returns_content_after_write(self):
        self.mgr.write_steering("task-r1", "Some instruction")
        result = self.mgr.read_steering("task-r1")
        self.assertIsNotNone(result)
        self.assertIn("Some instruction", result)

    def test_read_steering_returns_none_for_empty_file(self):
        path = self.mgr.get_steering_path("task-empty")
        open(path, "w").close()  # create empty file
        result = self.mgr.read_steering("task-empty")
        self.assertIsNone(result)

    def test_read_steering_handles_oserror(self):
        path = self.mgr.get_steering_path("task-os")
        open(path, "w").write("content")
        with patch("builtins.open", side_effect=OSError("permission denied")):
            result = self.mgr.read_steering("task-os")
        self.assertIsNone(result)

    # ── cleanup_steering ───────────────────────────────────────────────────────

    def test_cleanup_steering_removes_file(self):
        self.mgr.write_steering("task-clean", "instruction")
        path = self.mgr.get_steering_path("task-clean")
        self.assertTrue(os.path.exists(path))
        self.mgr.cleanup_steering("task-clean")
        self.assertFalse(os.path.exists(path))

    def test_cleanup_steering_noop_when_file_missing(self):
        # Should not raise even if file doesn't exist
        self.mgr.cleanup_steering("task-missing-clean")


# ── API endpoint tests ─────────────────────────────────────────────────────────

os.environ.setdefault("API_SHARED_KEY", "test_key_123")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "8099")


class TestSteerAPIEndpoints(unittest.TestCase):
    """Tests for POST /steer and GET /steering API endpoints."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        import agent_manager

        # Temp backing store for the bg_task_mgr created inside create_api_app()
        cls._tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        cls._tmp.close()
        cls._steering_dir = tempfile.mkdtemp()

        # Capture the BackgroundTaskManager instance created by create_api_app()
        # by intercepting __init__ before the call.
        _captured = []
        _original_init = BackgroundTaskManager.__init__

        def _capturing_init(self_inner, *args, **kwargs):
            _original_init(self_inner, *args, **kwargs)
            # Redirect file-system paths to temp dirs
            self_inner._path = cls._tmp.name
            self_inner.STEERING_DIR = cls._steering_dir
            _captured.append(self_inner)

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
        cls.auth = {"Authorization": "Bearer shared_test_key_123"}
        # The first captured instance is the one used by the app
        cls.bg_mgr = _captured[0]

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()
        os.unlink(cls._tmp.name)
        shutil.rmtree(cls._steering_dir, ignore_errors=True)

    def _create_task(self, task_id: str, status: str = "running") -> dict:
        return self.bg_mgr.create_task(
            task_id=task_id,
            session_id=f"sess-{task_id}",
            user_identity="testuser@example.com",
            channel="webex",
            agent="orchestrator",
            runtime="copilot",
            model="sonnet",
            prompt="Test task",
            pid=0,
            status=status,
        )

    # ── POST /steer ────────────────────────────────────────────────────────────

    def test_steer_404_on_missing_task(self):
        resp = self.client.post(
            "/api/v1/background-tasks/no-such-task/steer",
            json={"instruction": "do something"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 404)

    def test_steer_409_on_non_running_task(self):
        self._create_task("steer-completed-01", status="completed")
        resp = self.client.post(
            "/api/v1/background-tasks/steer-completed-01/steer",
            json={"instruction": "do something"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 409)

    def test_steer_422_on_empty_instruction(self):
        self._create_task("steer-running-01")
        resp = self.client.post(
            "/api/v1/background-tasks/steer-running-01/steer",
            json={"instruction": "   "},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 422)

    def test_steer_422_on_overlong_instruction(self):
        self._create_task("steer-running-02")
        resp = self.client.post(
            "/api/v1/background-tasks/steer-running-02/steer",
            json={"instruction": "x" * 5001},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 422)

    def test_steer_200_on_valid_running_task(self):
        self._create_task("steer-running-03")
        resp = self.client.post(
            "/api/v1/background-tasks/steer-running-03/steer",
            json={"instruction": "Please focus on the database module"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["task_id"], "steer-running-03")
        self.assertEqual(data["status"], "steering_written")
        self.assertIn("steering_file", data)
        self.assertIn("instruction_preview", data)

    # ── GET /steering ──────────────────────────────────────────────────────────

    def test_get_steering_404_on_missing_task(self):
        resp = self.client.get(
            "/api/v1/background-tasks/no-such-task-get/steering",
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 404)

    def test_get_steering_200_has_steering_false_when_no_file(self):
        self._create_task("steer-get-nofile")
        resp = self.client.get(
            "/api/v1/background-tasks/steer-get-nofile/steering",
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["has_steering"])


# ── Bot command tests ──────────────────────────────────────────────────────────


class TestSteerBotCommand(unittest.TestCase):
    """Tests for /background steer <task_id> <instruction> bot command handler."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.steering_tmpdir = tempfile.mkdtemp()
        self.mgr = _make_mgr(self.tmp.name, self.steering_tmpdir)

    def tearDown(self):
        os.unlink(self.tmp.name)
        shutil.rmtree(self.steering_tmpdir, ignore_errors=True)

    def _add_task(self, task_id, status="running"):
        return self.mgr.create_task(
            task_id=task_id,
            session_id=f"sess-{task_id}",
            user_identity="botuser@example.com",
            channel="telegram",
            agent="orchestrator",
            runtime="copilot",
            model="sonnet",
            prompt="Bot test task",
            pid=0,
            status=status,
        )

    def _handle_steer(self, args: str) -> str:
        """Replicate the bot steer sub-command handler logic."""
        sub = args.strip()
        sub_lower = sub.lower()
        if sub_lower.startswith("steer "):
            parts = sub[6:].strip().split(None, 1)
            if len(parts) < 2:
                return "Usage: `/background steer <task_id> <instruction>`"
            tid, instruction = parts
            task = self.mgr.get_task(tid)
            if not task:
                return f"Task `{tid}` not found."
            if task["status"] != "running":
                return f"Task `{tid}` is {task['status']}, not running."
            self.mgr.write_steering(tid, instruction)
            return (
                f"\U0001f3af **Steering sent to `{tid}`**\n\n"
                f"Instruction: {instruction[:200]}"
            )
        return "Unknown sub-command"

    def test_missing_instruction_returns_usage(self):
        result = self._handle_steer("steer task-123")
        self.assertIn("Usage:", result)

    def test_nonexistent_task_returns_not_found(self):
        result = self._handle_steer("steer no-such-task Do something")
        self.assertIn("not found", result.lower())

    def test_non_running_task_returns_status_message(self):
        self._add_task("bot-queued-01", status="queued")
        result = self._handle_steer("steer bot-queued-01 Fix the bug")
        self.assertIn("queued", result)
        self.assertNotIn("Steering sent", result)

    def test_valid_running_task_writes_steering(self):
        self._add_task("bot-running-01", status="running")
        result = self._handle_steer("steer bot-running-01 Focus on auth module")
        self.assertIn("Steering sent", result)
        self.assertIn("bot-running-01", result)
        content = self.mgr.read_steering("bot-running-01")
        self.assertIsNotNone(content)
        self.assertIn("Focus on auth module", content)


if __name__ == "__main__":
    unittest.main()
