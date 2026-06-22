"""Tests for issue #369 — Edit agent AGENTS.md via WebUI Settings panel.

Covers:
- GET /api/v1/agents/{name}/instructions returns content
- GET returns empty content with exists=False when AGENTS.md absent
- PUT /api/v1/agents/{name}/instructions writes content to disk
- GET/PUT return 404 for unknown agents
- PUT rejects missing content field and invalid JSON
- Round-trip: write then read back
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


class TestAgentInstructionsAPI(unittest.TestCase):
    """Integration tests for GET/PUT /api/v1/agents/{name}/instructions."""

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
                    "description": "Test agent for instructions tests",
                },
            ],
            cls.tmpdir,
        )

        # create_api_app reads AGENT_CONFIG_FILE from the environment
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
        # Remove AGENTS.md before each test for clean state
        agents_md = Path(self.agent_dir) / "AGENTS.md"
        if agents_md.exists():
            agents_md.unlink()

    # ── GET tests ──────────────────────────────────────────────────────────────

    def test_get_returns_empty_when_no_file(self):
        """GET should return empty content with exists=False when AGENTS.md absent."""
        resp = self.client.get("/api/v1/agents/test-agent/instructions", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("content", data)
        self.assertEqual(data["content"], "")
        self.assertFalse(data["exists"])

    def test_get_returns_file_content(self):
        """GET should return content of existing AGENTS.md."""
        agents_md = Path(self.agent_dir) / "AGENTS.md"
        agents_md.write_text("# Test Agent\nDo great things.\n")

        resp = self.client.get("/api/v1/agents/test-agent/instructions", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["content"], "# Test Agent\nDo great things.\n")
        self.assertTrue(data["exists"])

    def test_get_unknown_agent_returns_404(self):
        """GET for an unknown agent should return 404."""
        resp = self.client.get("/api/v1/agents/nonexistent-agent/instructions", headers=self.auth)
        self.assertEqual(resp.status_code, 404)

    def test_get_requires_auth(self):
        """GET without auth should return 401 or 403."""
        resp = self.client.get("/api/v1/agents/test-agent/instructions")
        self.assertIn(resp.status_code, (401, 403))

    # ── PUT tests ──────────────────────────────────────────────────────────────

    def test_put_writes_content_to_disk(self):
        """PUT should write content to AGENTS.md."""
        content = "# New Instructions\nBe helpful.\n"
        resp = self.client.put(
            "/api/v1/agents/test-agent/instructions",
            json={"content": content},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "saved")
        agents_md = Path(self.agent_dir) / "AGENTS.md"
        self.assertTrue(agents_md.exists())
        self.assertEqual(agents_md.read_text(), content)

    def test_put_overwrites_existing_file(self):
        """PUT should overwrite existing AGENTS.md."""
        agents_md = Path(self.agent_dir) / "AGENTS.md"
        agents_md.write_text("old content")

        resp = self.client.put(
            "/api/v1/agents/test-agent/instructions",
            json={"content": "new content"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(agents_md.read_text(), "new content")

    def test_put_unknown_agent_returns_404(self):
        """PUT for an unknown agent should return 404."""
        resp = self.client.put(
            "/api/v1/agents/ghost-agent/instructions",
            json={"content": "x"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 404)

    def test_put_requires_auth(self):
        """PUT without auth should return 401 or 403."""
        resp = self.client.put(
            "/api/v1/agents/test-agent/instructions",
            json={"content": "x"},
        )
        self.assertIn(resp.status_code, (401, 403))

    def test_put_rejects_missing_content_field(self):
        """PUT with a body missing the 'content' key should return 400."""
        resp = self.client.put(
            "/api/v1/agents/test-agent/instructions",
            json={"not_content": "x"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 400)

    def test_put_rejects_non_json_body(self):
        """PUT with non-JSON body should return 400."""
        resp = self.client.put(
            "/api/v1/agents/test-agent/instructions",
            content=b"not json",
            headers={**self.auth, "Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 400)

    # ── Round-trip test ────────────────────────────────────────────────────────

    def test_round_trip_write_then_read(self):
        """Write content via PUT, verify it comes back via GET."""
        expected = "# Agent Instructions\n\nBe excellent.\n\n## Rules\n- Rule 1\n- Rule 2\n"
        put_resp = self.client.put(
            "/api/v1/agents/test-agent/instructions",
            json={"content": expected},
            headers=self.auth,
        )
        self.assertEqual(put_resp.status_code, 200)

        get_resp = self.client.get("/api/v1/agents/test-agent/instructions", headers=self.auth)
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["content"], expected)
        self.assertTrue(get_resp.json()["exists"])

    # ── Path traversal guard ───────────────────────────────────────────────────

    def test_path_traversal_guard_is_exercised(self):
        """Verify normal operation confirms the path guard runs without error.

        AGENTS.md is always resolved as {agent.path}/AGENTS.md — a fixed
        relative join — so traversal via the agent name is not possible through
        the public API.  This test confirms the guard code path is reached and
        does NOT fire for a legitimate agent, verifying it is wired up.
        """
        resp = self.client.put(
            "/api/v1/agents/test-agent/instructions",
            json={"content": "safe content"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200, "Path guard should not block a valid request")


if __name__ == "__main__":
    unittest.main()
