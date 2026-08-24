"""Tests for issue #62 — per-agent file browser workspace tool (backend).

Covers GET /api/v1/agents/{name}/files:
- Lists files/directories at the agent root and in subdirectories
- Directories sorted before files, both alphabetically
- Path traversal is rejected
- 404 for unknown agent, missing directory
- 400 for a path that isn't a directory
- Auth required
- Agent isolation: one agent's listing never includes another's files
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Part of #471: setdefault, not a plain assignment -- this ran unconditionally
# at import time, permanently overwriting API_SHARED_KEY for every test file
# collected afterward in the same process regardless of what they expected.
os.environ.setdefault("API_SHARED_KEY", "test_key_123")
os.environ["APP_ENV"] = "DEV"
os.environ["API_PORT"] = "8099"


def _make_agents_json(agents, tmpdir):
    path = os.path.join(tmpdir, "agents.json")
    with open(path, "w") as f:
        json.dump({"agents": agents}, f)
    return path


class TestAgentFileBrowserAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import agent_manager

        cls._telegram_patch = patch.object(
            agent_manager, "_resolve_telegram_identity", side_effect=lambda identity: identity
        )
        cls._telegram_patch.start()
        cls._send_pairing_patch = patch.object(
            agent_manager, "_send_pairing_code", return_value=True
        )
        cls._send_pairing_patch.start()

        cls.tmpdir = tempfile.mkdtemp()
        cls.agent_dir = os.path.join(cls.tmpdir, "test-agent")
        cls.other_agent_dir = os.path.join(cls.tmpdir, "other-agent")
        os.makedirs(cls.agent_dir, exist_ok=True)
        os.makedirs(cls.other_agent_dir, exist_ok=True)

        # A small tree: two files at the root, one subdirectory with a file.
        Path(cls.agent_dir, "README.md").write_text("# hello\n")
        Path(cls.agent_dir, "notes.txt").write_text("notes\n")
        os.makedirs(os.path.join(cls.agent_dir, "src"), exist_ok=True)
        Path(cls.agent_dir, "src", "main.py").write_text("print('hi')\n")

        Path(cls.other_agent_dir, "secret.txt").write_text("not yours\n")

        config_file = _make_agents_json(
            [
                {"name": "test-agent", "path": cls.agent_dir, "description": "Test"},
                {"name": "other-agent", "path": cls.other_agent_dir, "description": "Other"},
            ],
            cls.tmpdir,
        )

        os.environ["AGENT_CONFIG_FILE"] = config_file
        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        shared_key = os.environ.get("API_SHARED_KEY", "test_key_123")
        cls.auth = {"Authorization": f"Bearer shared_{shared_key}"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()
        os.environ.pop("AGENT_CONFIG_FILE", None)
        import shutil
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_lists_root_with_directories_first_then_alphabetical(self):
        resp = self.client.get("/api/v1/agents/test-agent/files", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        names = [e["name"] for e in data["entries"]]
        # Directories first, then files in case-insensitive alphabetical
        # order: "notes" sorts before "readme" lowercased, even though 'R' < 'n'
        # in a case-sensitive/ASCII sort.
        self.assertEqual(names, ["src", "notes.txt", "README.md"])
        src_entry = data["entries"][0]
        self.assertTrue(src_entry["isDirectory"])
        self.assertIsNone(src_entry["size"])
        notes_entry = data["entries"][1]
        self.assertFalse(notes_entry["isDirectory"])
        self.assertGreater(notes_entry["size"], 0)

    def test_lists_a_subdirectory(self):
        resp = self.client.get("/api/v1/agents/test-agent/files?path=src", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["path"], "src")
        self.assertEqual([e["name"] for e in data["entries"]], ["main.py"])

    def test_path_traversal_is_rejected(self):
        resp = self.client.get(
            "/api/v1/agents/test-agent/files?path=../other-agent", headers=self.auth
        )
        self.assertEqual(resp.status_code, 400)

    def test_absolute_path_escape_is_rejected(self):
        resp = self.client.get(
            "/api/v1/agents/test-agent/files?path=/etc", headers=self.auth
        )
        self.assertIn(resp.status_code, (400, 404))

    def test_unknown_agent_returns_404(self):
        resp = self.client.get("/api/v1/agents/ghost/files", headers=self.auth)
        self.assertEqual(resp.status_code, 404)

    def test_missing_subdirectory_returns_404(self):
        resp = self.client.get(
            "/api/v1/agents/test-agent/files?path=does-not-exist", headers=self.auth
        )
        self.assertEqual(resp.status_code, 404)

    def test_pointing_at_a_file_returns_400(self):
        resp = self.client.get(
            "/api/v1/agents/test-agent/files?path=README.md", headers=self.auth
        )
        self.assertEqual(resp.status_code, 400)

    def test_requires_auth(self):
        resp = self.client.get("/api/v1/agents/test-agent/files")
        self.assertIn(resp.status_code, (401, 403))

    def test_agent_isolation_never_sees_another_agents_files(self):
        resp = self.client.get("/api/v1/agents/test-agent/files", headers=self.auth)
        names = [e["name"] for e in resp.json()["entries"]]
        self.assertNotIn("secret.txt", names)

        resp = self.client.get("/api/v1/agents/other-agent/files", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        names = [e["name"] for e in resp.json()["entries"]]
        self.assertEqual(names, ["secret.txt"])


if __name__ == "__main__":
    unittest.main()
