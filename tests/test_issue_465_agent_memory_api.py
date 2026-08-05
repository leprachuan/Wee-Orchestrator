"""Tests for issue #465 — per-agent memory API so clients can list and edit memories.

Covers:
- GET /api/v1/agents/{name}/memories lists memory files with a name and summary
- GET .../memories/{name} returns one memory's content
- PUT .../memories/{name} writes content back, creating parent dirs as needed
- DELETE .../memories/{name} removes a memory file
- 404 for unknown agents and missing memories
- 400 for a path-traversal attempt
- auth is required on every route
- round-trip: write then read back
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["API_SHARED_KEY"] = "test_key_123"
os.environ["APP_ENV"] = "DEV"
os.environ["API_PORT"] = "8099"


def _make_agents_json(agents, tmpdir):
    path = os.path.join(tmpdir, "agents.json")
    with open(path, "w") as f:
        json.dump({"agents": agents}, f)
    return path


class TestAgentMemoryAPI(unittest.TestCase):
    """Integration tests for GET/PUT/DELETE /api/v1/agents/{name}/memories[/{name}]."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import agent_manager

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

        cls.tmpdir = tempfile.mkdtemp()
        cls.agent_dir = os.path.join(cls.tmpdir, "test-agent")
        os.makedirs(cls.agent_dir, exist_ok=True)

        config_file = _make_agents_json(
            [
                {
                    "name": "test-agent",
                    "path": cls.agent_dir,
                    "description": "Test agent for memory API tests",
                },
            ],
            cls.tmpdir,
        )

        os.environ["AGENT_CONFIG_FILE"] = config_file
        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.auth = {"Authorization": "Bearer shared_test_key_123"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()
        os.environ.pop("AGENT_CONFIG_FILE", None)
        import shutil
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self):
        # Reset the agent's memories directory before each test for clean state.
        import shutil
        memories_dir = Path(self.agent_dir) / "memories"
        shutil.rmtree(memories_dir, ignore_errors=True)

    # ── GET list tests ──────────────────────────────────────────────────────────

    def test_list_returns_empty_when_no_memories_dir(self):
        resp = self.client.get("/api/v1/agents/test-agent/memories", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"memories": []})

    def test_list_returns_files_with_name_and_summary(self):
        memories_dir = Path(self.agent_dir) / "memories"
        memories_dir.mkdir(parents=True)
        (memories_dir / "MEMORY.md").write_text("# Durable facts\n- fact one\n")
        daily_dir = memories_dir / "daily"
        daily_dir.mkdir()
        (daily_dir / "2026-08-05.md").write_text("## Notes\nDid a thing today.\n")

        resp = self.client.get("/api/v1/agents/test-agent/memories", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        memories = resp.json()["memories"]
        names = [m["name"] for m in memories]
        self.assertIn("MEMORY.md", names)
        self.assertIn("daily/2026-08-05.md", names)

        by_name = {m["name"]: m["summary"] for m in memories}
        self.assertEqual(by_name["MEMORY.md"], "Durable facts")
        self.assertEqual(by_name["daily/2026-08-05.md"], "Notes")

    def test_list_puts_memory_md_first(self):
        memories_dir = Path(self.agent_dir) / "memories"
        memories_dir.mkdir(parents=True)
        (memories_dir / "aaa_category.md").write_text("z\n")
        (memories_dir / "MEMORY.md").write_text("m\n")

        resp = self.client.get("/api/v1/agents/test-agent/memories", headers=self.auth)
        names = [m["name"] for m in resp.json()["memories"]]
        self.assertEqual(names[0], "MEMORY.md")

    def test_list_unknown_agent_returns_404(self):
        resp = self.client.get("/api/v1/agents/nonexistent-agent/memories", headers=self.auth)
        self.assertEqual(resp.status_code, 404)

    def test_list_requires_auth(self):
        resp = self.client.get("/api/v1/agents/test-agent/memories")
        self.assertIn(resp.status_code, (401, 403))

    # ── GET single tests ─────────────────────────────────────────────────────────

    def test_get_single_returns_content(self):
        memories_dir = Path(self.agent_dir) / "memories"
        memories_dir.mkdir(parents=True)
        (memories_dir / "MEMORY.md").write_text("# Facts\n- one\n- two\n")

        resp = self.client.get("/api/v1/agents/test-agent/memories/MEMORY.md", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["content"], "# Facts\n- one\n- two\n")
        self.assertTrue(data["exists"])

    def test_get_single_nested_name_returns_content(self):
        memories_dir = Path(self.agent_dir) / "memories" / "daily"
        memories_dir.mkdir(parents=True)
        (memories_dir / "2026-08-05.md").write_text("today's notes\n")

        resp = self.client.get(
            "/api/v1/agents/test-agent/memories/daily/2026-08-05.md", headers=self.auth
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["content"], "today's notes\n")

    def test_get_single_missing_file_reports_not_exists(self):
        resp = self.client.get("/api/v1/agents/test-agent/memories/MEMORY.md", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["content"], "")
        self.assertFalse(data["exists"])

    def test_get_single_unknown_agent_returns_404(self):
        resp = self.client.get(
            "/api/v1/agents/nonexistent-agent/memories/MEMORY.md", headers=self.auth
        )
        self.assertEqual(resp.status_code, 404)

    def test_get_single_requires_auth(self):
        resp = self.client.get("/api/v1/agents/test-agent/memories/MEMORY.md")
        self.assertIn(resp.status_code, (401, 403))

    # ── PUT tests ────────────────────────────────────────────────────────────────

    def test_put_writes_content_to_disk(self):
        content = "# Durable facts\n- new fact\n"
        resp = self.client.put(
            "/api/v1/agents/test-agent/memories/MEMORY.md",
            json={"content": content},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "saved")
        memory_path = Path(self.agent_dir) / "memories" / "MEMORY.md"
        self.assertTrue(memory_path.exists())
        self.assertEqual(memory_path.read_text(), content)

    def test_put_creates_parent_directories_for_nested_names(self):
        content = "fresh daily note\n"
        resp = self.client.put(
            "/api/v1/agents/test-agent/memories/daily/2026-08-05.md",
            json={"content": content},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        daily_path = Path(self.agent_dir) / "memories" / "daily" / "2026-08-05.md"
        self.assertTrue(daily_path.exists())
        self.assertEqual(daily_path.read_text(), content)

    def test_put_overwrites_existing_file(self):
        memories_dir = Path(self.agent_dir) / "memories"
        memories_dir.mkdir(parents=True)
        memory_path = memories_dir / "MEMORY.md"
        memory_path.write_text("old content")

        resp = self.client.put(
            "/api/v1/agents/test-agent/memories/MEMORY.md",
            json={"content": "new content"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(memory_path.read_text(), "new content")

    def test_put_unknown_agent_returns_404(self):
        resp = self.client.put(
            "/api/v1/agents/ghost-agent/memories/MEMORY.md",
            json={"content": "x"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 404)

    def test_put_requires_auth(self):
        resp = self.client.put(
            "/api/v1/agents/test-agent/memories/MEMORY.md",
            json={"content": "x"},
        )
        self.assertIn(resp.status_code, (401, 403))

    def test_put_rejects_missing_content_field(self):
        resp = self.client.put(
            "/api/v1/agents/test-agent/memories/MEMORY.md",
            json={"not_content": "x"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 400)

    def test_put_rejects_non_json_body(self):
        resp = self.client.put(
            "/api/v1/agents/test-agent/memories/MEMORY.md",
            content=b"not json",
            headers={**self.auth, "Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 400)

    # ── DELETE tests ─────────────────────────────────────────────────────────────

    def test_delete_removes_file(self):
        memories_dir = Path(self.agent_dir) / "memories"
        memories_dir.mkdir(parents=True)
        memory_path = memories_dir / "scratch.md"
        memory_path.write_text("temporary\n")

        resp = self.client.delete(
            "/api/v1/agents/test-agent/memories/scratch.md", headers=self.auth
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "deleted")
        self.assertFalse(memory_path.exists())

    def test_delete_missing_file_returns_404(self):
        resp = self.client.delete(
            "/api/v1/agents/test-agent/memories/does-not-exist.md", headers=self.auth
        )
        self.assertEqual(resp.status_code, 404)

    def test_delete_requires_auth(self):
        resp = self.client.delete("/api/v1/agents/test-agent/memories/MEMORY.md")
        self.assertIn(resp.status_code, (401, 403))

    # ── Path traversal guard ─────────────────────────────────────────────────────

    def test_path_traversal_is_rejected(self):
        resp = self.client.get(
            "/api/v1/agents/test-agent/memories/..%2f..%2f..%2fetc%2fpasswd",
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 400)

    def test_agent_isolation_one_agents_memories_are_not_visible_to_another(self):
        agent_a_dir = os.path.join(self.tmpdir, "agent-a")
        agent_b_dir = os.path.join(self.tmpdir, "agent-b")
        os.makedirs(os.path.join(agent_a_dir, "memories"), exist_ok=True)
        os.makedirs(os.path.join(agent_b_dir, "memories"), exist_ok=True)
        Path(agent_a_dir, "memories", "MEMORY.md").write_text("agent a secret\n")

        config_file = _make_agents_json(
            [
                {"name": "agent-a", "path": agent_a_dir, "description": "A"},
                {"name": "agent-b", "path": agent_b_dir, "description": "B"},
            ],
            self.tmpdir,
        )
        with patch.dict(os.environ, {"AGENT_CONFIG_FILE": config_file}):
            import agent_manager
            app = agent_manager.create_api_app()
            from fastapi.testclient import TestClient
            client = TestClient(app)

            resp = client.get("/api/v1/agents/agent-b/memories", headers=self.auth)
            self.assertEqual(resp.json(), {"memories": []})

            resp = client.get("/api/v1/agents/agent-a/memories", headers=self.auth)
            names = [m["name"] for m in resp.json()["memories"]]
            self.assertEqual(names, ["MEMORY.md"])

    # ── Round-trip test ──────────────────────────────────────────────────────────

    def test_round_trip_write_then_read_then_list(self):
        expected = "# Memory\n\n- durable fact one\n- durable fact two\n"
        put_resp = self.client.put(
            "/api/v1/agents/test-agent/memories/MEMORY.md",
            json={"content": expected},
            headers=self.auth,
        )
        self.assertEqual(put_resp.status_code, 200)

        get_resp = self.client.get("/api/v1/agents/test-agent/memories/MEMORY.md", headers=self.auth)
        self.assertEqual(get_resp.json()["content"], expected)
        self.assertTrue(get_resp.json()["exists"])

        list_resp = self.client.get("/api/v1/agents/test-agent/memories", headers=self.auth)
        names = [m["name"] for m in list_resp.json()["memories"]]
        self.assertIn("MEMORY.md", names)


if __name__ == "__main__":
    unittest.main()
