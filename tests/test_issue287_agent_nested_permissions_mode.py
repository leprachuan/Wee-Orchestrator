"""Regression tests for Issue #287.

Bug: background tasks ignore agent permissions.mode (nested format)
     from agents.json. Only top-level permission_mode/yolo were read
     after issue #245's fix. The nested {"permissions": {"mode": ...}}
     format used by wee-qa was silently dropped, causing codex to run
     in sandboxed mode and blocking external DNS (api.telegram.org).

Root cause: _load_agents_config() only copied top-level fields into the
AGENTS dict; "permissions" (nested) was omitted. Additionally,
create_background_task() only read body.permission_mode without checking
any agent config format — not nested, not top-level.

Fix:
  1. _load_agents_config() now copies "permission_mode", "yolo", and
     "permissions" into the AGENTS dict.
  2. create_background_task() resolves perm_mode using all three formats:
     nested permissions.mode > top-level permission_mode > yolo flag.
     Priority: body.permission_mode > body.yolo > agent config > "restricted".
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")
os.environ["API_SHARED_KEY"] = "test_key_123"
os.environ.setdefault("APP_ENV", "DEV")


def _make_client(agents):
    """Build an isolated TestClient with the given agents config."""
    from fastapi.testclient import TestClient

    tmp = tempfile.mkdtemp()
    cfg_path = os.path.join(tmp, "agents.json")
    with open(cfg_path, "w") as f:
        json.dump({"agents": agents}, f)
    os.environ["AGENT_CONFIG_FILE"] = cfg_path

    import agent_manager as am
    app = am.create_api_app()
    app.state.bg_task_mgr._path = os.path.join(tmp, "bg.json")
    app.state.bg_task_mgr._tasks_cache = None

    client = TestClient(app, raise_server_exceptions=False)
    return client, SimpleNamespace(cleanup=lambda: shutil.rmtree(tmp, ignore_errors=True))


HEADERS = {"Authorization": "Bearer shared_test_key_123"}


class TestNestedPermissionsModeFormat(unittest.TestCase):
    """Nested permissions.mode must propagate to background tasks (the wee-qa format)."""

    @classmethod
    def setUpClass(cls):
        cls.client, cls.store = _make_client([
            {
                "name": "wee-qa",
                "path": "/opt/wee-qa",
                "description": "QA agent",
                "primary_runtime": "codex",
                "primary_model": "gpt-5.4-mini",
                "permissions": {
                    "mode": "elevated",
                    "network": {"allow_urls": ["*"]},
                },
            },
            {
                "name": "sandboxed-agent",
                "path": "/opt/sandboxed",
                "description": "Sandboxed agent",
                "primary_runtime": "copilot",
                "primary_model": "claude-haiku-4.5",
                "permissions": {
                    "mode": "sandboxed",
                },
            },
            {
                "name": "plain-agent",
                "path": "/opt/plain",
                "description": "Agent with no permissions config",
                "primary_runtime": "copilot",
                "primary_model": "claude-haiku-4.5",
            },
        ])

    @classmethod
    def tearDownClass(cls):
        cls.store.cleanup()

    def _post(self, payload):
        resp = self.client.post(
            "/api/v1/background-tasks", json=payload, headers=HEADERS
        )
        self.assertIn(resp.status_code, (200, 201), resp.text[:300])
        return resp.json()

    def test_nested_permissions_mode_elevated_propagates(self):
        """wee-qa's nested permissions.mode=elevated must yield elevated."""
        data = self._post({"prompt": "test", "agent": "wee-qa"})
        self.assertEqual(
            data.get("permission_mode"),
            "elevated",
            f"Expected elevated from nested permissions.mode, got: {data.get('permission_mode')}. "
            f"Full: {data}",
        )

    def test_nested_permissions_mode_sandboxed_propagates(self):
        """Nested permissions.mode=sandboxed must yield sandboxed."""
        data = self._post({"prompt": "test", "agent": "sandboxed-agent"})
        self.assertEqual(data.get("permission_mode"), "sandboxed")

    def test_no_permissions_defaults_to_restricted(self):
        """Agent without any permissions config defaults to restricted."""
        data = self._post({"prompt": "test", "agent": "plain-agent"})
        self.assertEqual(data.get("permission_mode"), "restricted")

    def test_body_permission_mode_overrides_nested(self):
        """body.permission_mode wins over agent's nested permissions.mode."""
        data = self._post(
            {"prompt": "test", "agent": "wee-qa", "permission_mode": "restricted"}
        )
        self.assertEqual(data.get("permission_mode"), "restricted")

    def test_body_permission_mode_elevates_sandboxed_agent(self):
        """body.permission_mode=elevated wins over agent's sandboxed setting."""
        data = self._post(
            {"prompt": "test", "agent": "sandboxed-agent", "permission_mode": "elevated"}
        )
        self.assertEqual(data.get("permission_mode"), "elevated")


if __name__ == "__main__":
    unittest.main()
