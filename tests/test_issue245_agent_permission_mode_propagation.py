"""Regression tests for Issue #245.

Bug: When an agent in agents.json uses the new schema (primary_runtime/
primary_model) and declares ``permission_mode: "elevated"`` and/or
``yolo: true`` at the top level, those fields are silently dropped when
the background task is dispatched.  The synthesized _dispatch_config in
create_background_task() only copied runtime/model/fallback fields,
causing perm_mode to fall through to "restricted".

Symptom: An agent declared elevated ran in the default sandbox,
blocking operations like SSH to the dev host.

Fix: include permission_mode and yolo in the synthesized _dispatch_config
when building it from the new schema.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")
os.environ["API_SHARED_KEY"] = "test_key_123"
os.environ.setdefault("APP_ENV", "DEV")


def _isolated_app():
    import agent_manager as am

    tmp = tempfile.mkdtemp()
    app = am.create_api_app()
    app.state.bg_task_mgr._path = os.path.join(tmp, "background-tasks.json")
    app.state.bg_task_mgr._tasks_cache = None
    return app, SimpleNamespace(
        name=tmp,
        cleanup=lambda: shutil.rmtree(tmp, ignore_errors=True),
    )


class TestNewSchemaPermissionPropagation(unittest.TestCase):
    """Top-level permission_mode/yolo on a new-schema agent must propagate."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        cls.temp_dir = tempfile.TemporaryDirectory()
        cfg_path = os.path.join(cls.temp_dir.name, "agents.json")
        with open(cfg_path, "w") as f:
            json.dump(
                {
                    "agents": [
                        {
                            # Mirrors the real wee-dev entry: new schema +
                            # top-level permission_mode/yolo (no
                            # dispatch_config block).
                            "name": "wee-dev",
                            "description": "Wee engineering agent",
                            "path": "/opt/wee-dev",
                            "primary_runtime": "codex",
                            "primary_model": "gpt-5.4",
                            "fallback_runtime": "copilot",
                            "fallback_model": "claude-sonnet-4.6",
                            "permission_mode": "elevated",
                            "yolo": True,
                        },
                        {
                            # Agent that opts into sandboxed via top-level
                            # permission_mode (no yolo).
                            "name": "ro-agent",
                            "description": "Read-only agent",
                            "path": "/opt/plain",
                            "primary_runtime": "copilot",
                            "primary_model": "claude-haiku-4.5",
                            "permission_mode": "sandboxed",
                        },
                        {
                            # New-schema agent without any permission hints —
                            # must default to restricted.
                            "name": "default-agent",
                            "description": "Default agent",
                            "path": "/opt/default",
                            "primary_runtime": "copilot",
                            "primary_model": "claude-haiku-4.5",
                        },
                    ]
                },
                f,
            )
        os.environ["AGENT_CONFIG_FILE"] = cfg_path
        cls.app, cls.bg_store_dir = _isolated_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=False)
        cls.headers = {"Authorization": "Bearer shared_test_key_123"}

    @classmethod
    def tearDownClass(cls):
        cls.bg_store_dir.cleanup()
        cls.temp_dir.cleanup()

    def _post(self, payload):
        resp = self.client.post(
            "/api/v1/background-tasks", json=payload, headers=self.headers
        )
        self.assertIn(
            resp.status_code,
            (200, 201),
            f"Unexpected status {resp.status_code}: {resp.text[:300]}",
        )
        return resp.json()

    def test_top_level_yolo_true_promotes_to_elevated(self):
        """yolo:true at top level on new-schema agent → elevated."""
        data = self._post({"prompt": "test", "agent": "wee-dev"})
        self.assertEqual(
            data.get("permission_mode"),
            "elevated",
            "wee-qa declares yolo:true at top level — must yield elevated. "
            "Got: %r (full response: %r)" % (data.get("permission_mode"), data),
        )

    def test_top_level_permission_mode_sandboxed_honored(self):
        """permission_mode:sandboxed at top level on new-schema agent."""
        data = self._post({"prompt": "test", "agent": "ro-agent"})
        self.assertEqual(data.get("permission_mode"), "sandboxed")

    def test_new_schema_without_perm_defaults_restricted(self):
        """No perm hints on new-schema agent → restricted (unchanged)."""
        data = self._post({"prompt": "test", "agent": "default-agent"})
        self.assertEqual(data.get("permission_mode"), "restricted")

    def test_body_yolo_false_overrides_agent_elevated(self):
        """body.yolo=False must override agent-level yolo:true."""
        data = self._post({"prompt": "test", "agent": "wee-dev", "yolo": False})
        self.assertEqual(data.get("permission_mode"), "restricted")

    def test_body_permission_mode_overrides_agent(self):
        """body.permission_mode wins over agent-level permission_mode."""
        data = self._post(
            {
                "prompt": "test",
                "agent": "ro-agent",
                "permission_mode": "elevated",
            }
        )
        self.assertEqual(data.get("permission_mode"), "elevated")


if __name__ == "__main__":
    unittest.main()
