"""Tests for F018: POST /api/v1/todos quick-add endpoint."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_123")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "8099")


class TestTodoQuickAdd(unittest.TestCase):
    """Test POST /api/v1/todos endpoint for creating new TODOs."""

    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch as _patch

        from fastapi.testclient import TestClient

        import agent_manager

        cls._telegram_patch = _patch.object(
            agent_manager,
            "_resolve_telegram_identity",
            side_effect=lambda identity: identity,
        )
        cls._telegram_patch.start()
        cls._send_pairing_patch = _patch.object(
            agent_manager,
            "_send_pairing_code",
            return_value=True,
        )
        cls._send_pairing_patch.start()
        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.headers = {"Authorization": "Bearer shared_test_key_123"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()

    def setUp(self):
        """Create a temp TODO directory under /opt for agent resolution."""
        self.tmp_base = tempfile.mkdtemp(
            prefix="f018test_", dir="/opt"
        )
        self.agent_name = os.path.basename(self.tmp_base)
        self.todos_dir = Path(self.tmp_base) / "TODOs"
        self.active_dir = self.todos_dir / "ACTIVE"
        self.active_dir.mkdir(parents=True)

    def tearDown(self):
        """Clean up temp directory."""
        shutil.rmtree(self.tmp_base, ignore_errors=True)

    def _post(self, body):
        return self.client.post(
            "/api/v1/todos",
            json=body,
            headers=self.headers,
        )

    def test_create_todo_basic(self):
        """Create a TODO with just a title."""
        resp = self._post({
            "title": "Buy groceries",
            "agent": self.agent_name,
        })
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["todo"], "Buy groceries")
        self.assertTrue(
            (self.active_dir / "Buy groceries").exists()
        )

    def test_create_todo_with_due_date(self):
        """Create a TODO with a due date."""
        resp = self._post({
            "title": "Meeting prep",
            "due_date": "2026-04-15 09:00",
            "agent": self.agent_name,
        })
        data = resp.json()
        self.assertTrue(data["success"])
        content = (self.active_dir / "Meeting prep").read_text()
        self.assertIn("DUE: 2026-04-15 09:00", content)

    def test_create_todo_with_labels(self):
        """Create a TODO with labels."""
        resp = self._post({
            "title": "Fix bug",
            "labels": ["urgent", "backend"],
            "agent": self.agent_name,
        })
        data = resp.json()
        self.assertTrue(data["success"])
        content = (self.active_dir / "Fix bug").read_text()
        self.assertIn("LABELS: {urgent},{backend}", content)

    def test_create_todo_with_details(self):
        """Create a TODO with details body."""
        resp = self._post({
            "title": "Research options",
            "details": "Compare A vs B",
            "agent": self.agent_name,
        })
        data = resp.json()
        self.assertTrue(data["success"])
        content = (
            self.active_dir / "Research options"
        ).read_text()
        self.assertIn("Compare A vs B", content)

    def test_create_todo_full(self):
        """Create a TODO with all fields."""
        resp = self._post({
            "title": "Deploy v2",
            "due_date": "2026-05-01 14:00",
            "labels": ["devops"],
            "details": "Run migration first",
            "agent": self.agent_name,
        })
        data = resp.json()
        self.assertTrue(data["success"])
        content = (self.active_dir / "Deploy v2").read_text()
        self.assertIn("DUE: 2026-05-01 14:00", content)
        self.assertIn("LABELS: {devops}", content)
        self.assertIn("Run migration first", content)

    def test_create_todo_empty_title_rejected(self):
        """Empty title should be rejected."""
        resp = self._post({
            "title": "",
            "agent": self.agent_name,
        })
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("required", data["error"].lower())

    def test_create_todo_whitespace_title_rejected(self):
        """Whitespace-only title should be rejected."""
        resp = self._post({
            "title": "   ",
            "agent": self.agent_name,
        })
        data = resp.json()
        self.assertFalse(data["success"])

    def test_create_todo_path_traversal_slash(self):
        """Slash in title should be rejected."""
        resp = self._post({
            "title": "../etc/passwd",
            "agent": self.agent_name,
        })
        data = resp.json()
        self.assertFalse(data["success"])

    def test_create_todo_path_traversal_dotdot(self):
        """Double-dot in title should be rejected."""
        resp = self._post({
            "title": "..sneaky",
            "agent": self.agent_name,
        })
        data = resp.json()
        self.assertFalse(data["success"])

    def test_create_todo_duplicate_rejected(self):
        """Duplicate title should be rejected."""
        (self.active_dir / "Existing task").write_text("")
        resp = self._post({
            "title": "Existing task",
            "agent": self.agent_name,
        })
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("already exists", data["error"])

    def test_create_todo_no_auth_rejected(self):
        """Request without auth should be rejected."""
        resp = self.client.post(
            "/api/v1/todos",
            json={"title": "No auth"},
        )
        self.assertIn(resp.status_code, (401, 403))

    def test_create_todo_shows_in_list(self):
        """Created TODO should appear in GET /api/v1/todos."""
        self._post({
            "title": "Visible item",
            "agent": self.agent_name,
        })
        resp = self.client.get(
            f"/api/v1/todos?agent={self.agent_name}",
            headers=self.headers,
        )
        data = resp.json()
        titles = [t["description"] for t in data["todos"]]
        self.assertIn("Visible item", titles)

    def test_create_todo_missing_body(self):
        """Invalid JSON body should be handled."""
        resp = self.client.post(
            "/api/v1/todos",
            headers={
                **self.headers,
                "Content-Type": "application/json",
            },
            content=b"not json",
        )
        data = resp.json()
        self.assertFalse(data["success"])

    def test_create_todo_empty_file_for_titleonly(self):
        """Title-only TODO creates file with empty or minimal content."""
        resp = self._post({
            "title": "Simple task",
            "agent": self.agent_name,
        })
        data = resp.json()
        self.assertTrue(data["success"])
        fpath = self.active_dir / "Simple task"
        self.assertTrue(fpath.exists())
        # No headers, so file should be empty
        self.assertEqual(fpath.read_text().strip(), "")


if __name__ == "__main__":
    unittest.main()
