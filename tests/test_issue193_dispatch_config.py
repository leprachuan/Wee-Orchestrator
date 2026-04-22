"""Regression tests for Issue #193.

Tests verify:
1. AGENTS dict preserves dispatch_config when loading agents.json
2. reload_agents_from_disk also preserves dispatch_config
3. dispatch_config.runtime/model/timeout used when body fields are empty
4. Explicit body values override dispatch_config (priority order correct)
5. /background slash command handler applies dispatch_config fallback
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")
os.environ.setdefault("API_SHARED_KEY", "test_key_123")
os.environ.setdefault("APP_ENV", "DEV")

from agent_manager import SessionManager  # noqa: E402

AGENTS_WITH_DISPATCH = {
    "agents": [
        {
            "name": "wee-qa",
            "description": "QA agent",
            "path": "/opt/wee-qa",
            "dispatch_config": {
                "runtime": "openai",
                "model": "gpt-5.4-mini",
                "permission_mode": "elevated",
                "yolo": True,
                "timeout": 1800,
            },
        },
        {
            "name": "plain-agent",
            "description": "Agent without dispatch_config",
            "path": "/opt/plain",
        },
    ]
}


class TestDispatchConfigPreservedOnLoad(unittest.TestCase):
    """Verify dispatch_config survives the agents.json → AGENTS dict journey."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cfg_path = Path(self.temp_dir.name) / "agents.json"
        with open(self.cfg_path, "w") as f:
            json.dump(AGENTS_WITH_DISPATCH, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_agents_config_preserves_dispatch_config(self):
        """_load_agents_config must keep dispatch_config in the AGENTS dict."""
        mgr = SessionManager(str(self.cfg_path))
        self.assertIn("wee-qa", mgr.AGENTS)
        dc = mgr.AGENTS["wee-qa"].get("dispatch_config", {})
        self.assertNotEqual(dc, {}, "dispatch_config must not be stripped on load")
        self.assertEqual(dc.get("runtime"), "openai")
        self.assertEqual(dc.get("model"), "gpt-5.4-mini")
        self.assertEqual(dc.get("timeout"), 1800)
        self.assertTrue(dc.get("yolo"))

    def test_load_agents_config_missing_dispatch_config_is_empty_dict(self):
        """Agent without dispatch_config must have an empty dict, not KeyError."""
        mgr = SessionManager(str(self.cfg_path))
        self.assertIn("plain-agent", mgr.AGENTS)
        dc = mgr.AGENTS["plain-agent"].get("dispatch_config", {})
        self.assertEqual(dc, {})

    def test_reload_agents_from_disk_preserves_dispatch_config(self):
        """reload_agents_from_disk must also keep dispatch_config."""
        mgr = SessionManager(str(self.cfg_path))
        # Force a reload
        ok, msg = mgr.reload_agents_from_disk()
        self.assertTrue(ok, f"Reload failed: {msg}")
        dc = mgr.AGENTS["wee-qa"].get("dispatch_config", {})
        self.assertNotEqual(
            dc, {}, "dispatch_config stripped by reload_agents_from_disk"
        )
        self.assertEqual(dc.get("runtime"), "openai")
        self.assertEqual(dc.get("timeout"), 1800)

    def test_dispatch_config_lookup_succeeds_after_load(self):
        """AGENTS.get(...).get('dispatch_config', {}) returns real values."""
        mgr = SessionManager(str(self.cfg_path))
        # Simulate what create_background_task does:
        dc = mgr.AGENTS.get("wee-qa", {}).get("dispatch_config", {})
        self.assertEqual(dc.get("runtime"), "openai")
        self.assertEqual(dc.get("model"), "gpt-5.4-mini")
        self.assertIsNotNone(dc.get("timeout"))


class TestSlashBgTimeoutUsesDispatchConfig(unittest.TestCase):
    """Verify /background slash handler picks up dispatch_config.timeout."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cfg_path = Path(self.temp_dir.name) / "agents.json"
        with open(self.cfg_path, "w") as f:
            json.dump(AGENTS_WITH_DISPATCH, f)
        self.mgr = SessionManager(str(self.cfg_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_fake_session(self, runtime="copilot", model="gpt-5-mini"):
        return {
            "runtime": runtime,
            "model": model,
            "channel": "webui",
            "identity": "test_user",
        }

    def test_slash_bg_timeout_comes_from_dispatch_config(self):
        """When no timeout= override, /background uses dispatch_config.timeout."""
        # Build the fake session data for the slash handler
        session_data = self._make_fake_session()
        captured = {}

        def fake_create_task_checked(**kwargs):
            captured.update(kwargs)
            fake_task = MagicMock()
            fake_task.task_id = "bg_test"
            return fake_task, "running"

        self.mgr._bg_task_mgr = MagicMock()
        self.mgr._bg_task_mgr.create_task_checked.side_effect = fake_create_task_checked
        self.mgr._bg_identity = "test_user"

        with patch("concurrent.futures.ThreadPoolExecutor"):
            result = self.mgr._slash_background(
                "agent=wee-qa say hello",
                session_data,
                "test-session-id",
            )

        # Check that timeout came from dispatch_config (1800) not default (900)
        # The timeout is passed positionally to _execute_background_task
        # But we can check indirectly via the output message
        self.assertIn("1800", result, "bg_timeout must be 1800 from dispatch_config")

    def test_slash_bg_explicit_timeout_overrides_dispatch_config(self):
        """Explicit timeout= override wins over dispatch_config.timeout."""
        session_data = self._make_fake_session()

        self.mgr._bg_task_mgr = MagicMock()
        fake_task = MagicMock()
        fake_task.task_id = "bg_test2"
        self.mgr._bg_task_mgr.create_task_checked.return_value = (fake_task, "running")
        self.mgr._bg_identity = "test_user"

        with patch("concurrent.futures.ThreadPoolExecutor"):
            result = self.mgr._slash_background(
                "agent=wee-qa timeout=300 say hello",
                session_data,
                "test-session-id",
            )

        # Explicit timeout=300 must override dispatch_config 1800
        self.assertIn("300", result)
        self.assertNotIn("1800", result)

    def test_slash_bg_runtime_comes_from_dispatch_config(self):
        """When no runtime= override, /background uses dispatch_config.runtime."""
        session_data = self._make_fake_session()

        self.mgr._bg_task_mgr = MagicMock()
        fake_task = MagicMock()
        fake_task.task_id = "bg_test3"
        self.mgr._bg_task_mgr.create_task_checked.return_value = (fake_task, "running")
        self.mgr._bg_identity = "test_user"

        with patch("concurrent.futures.ThreadPoolExecutor"):
            result = self.mgr._slash_background(
                "agent=wee-qa say hello",
                session_data,
                "test-session-id",
            )

        # wee-qa dispatch_config.runtime = "openai"
        self.assertIn("openai", result)


class TestDispatchConfigPriorityResolution(unittest.TestCase):
    """Tests verify priority resolution via the PRODUCTION endpoint.

    These tests POST to /api/v1/background-tasks and assert the
    permission_mode in the response — exercising the real code path in
    create_background_task() instead of a reimplemented helper.
    """

    @classmethod
    def setUpClass(cls):
        import tempfile

        from fastapi.testclient import TestClient

        import agent_manager as am

        # Inject a test agents.json that has wee-qa with dispatch_config
        cls.temp_dir = tempfile.TemporaryDirectory()
        cfg_path = os.path.join(cls.temp_dir.name, "agents.json")
        with open(cfg_path, "w") as f:
            json.dump(
                {
                    "agents": [
                        {
                            "name": "wee-qa",
                            "description": "QA agent",
                            "path": "/opt/wee-qa",
                            "dispatch_config": {
                                "runtime": "openai",
                                "model": "gpt-5.4-mini",
                                "permission_mode": "elevated",
                                "yolo": True,
                                "timeout": 1800,
                            },
                        },
                        {
                            "name": "plain-agent",
                            "description": "Agent without dispatch_config",
                            "path": "/opt/plain",
                        },
                    ]
                },
                f,
            )
        os.environ["AGENT_CONFIG_FILE"] = cfg_path
        cls.app = am.create_api_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=False)
        cls.headers = {"Authorization": "Bearer shared_test_key_123"}

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def _post(self, payload):
        """POST to the production endpoint and return the JSON response."""
        resp = self.client.post(
            "/api/v1/background-tasks",
            json=payload,
            headers=self.headers,
        )
        self.assertIn(
            resp.status_code,
            (200, 201),
            f"Unexpected status {resp.status_code}: {resp.text[:300]}",
        )
        return resp.json()

    def test_body_overrides_all(self):
        """Explicit body.permission_mode overrides dispatch_config and yolo."""
        data = self._post(
            {
                "prompt": "test",
                "agent": "wee-qa",
                "runtime": "my-runtime",
                "model": "my-model",
                "permission_mode": "sandboxed",
            }
        )
        self.assertEqual(data.get("permission_mode"), "sandboxed")
        self.assertEqual(data.get("runtime"), "my-runtime")
        self.assertEqual(data.get("model"), "my-model")

    def test_dispatch_config_overrides_session_defaults(self):
        """dispatch_config.permission_mode beats session defaults when body is empty."""
        data = self._post({"prompt": "test", "agent": "wee-qa"})
        # wee-qa has dispatch_config.yolo=True → should resolve to "elevated"
        self.assertEqual(
            data.get("permission_mode"),
            "elevated",
            "dispatch_config.yolo=True must resolve to 'elevated'",
        )

    def test_wee_qa_dispatch_config_scenario(self):
        """Full wee-qa scenario: runtime/model/permission_mode from dispatch_config."""
        data = self._post({"prompt": "test", "agent": "wee-qa"})
        self.assertEqual(data.get("runtime"), "openai")
        self.assertEqual(data.get("model"), "gpt-5.4-mini")
        self.assertEqual(data.get("permission_mode"), "elevated")

    def test_session_defaults_when_dispatch_config_empty(self):
        """plain-agent (no dispatch_config) falls back to restricted."""
        data = self._post({"prompt": "test", "agent": "plain-agent"})
        self.assertEqual(data.get("permission_mode"), "restricted")

    def test_body_yolo_true_elevates_perm_mode(self):
        """body.yolo=True must resolve to elevated perm_mode."""
        data = self._post(
            {
                "prompt": "test",
                "agent": "plain-agent",
                "yolo": True,
            }
        )
        self.assertEqual(data.get("permission_mode"), "elevated")

    def test_body_permission_mode_takes_highest_priority(self):
        """Explicit body.permission_mode wins over yolo and dispatch_config."""
        data = self._post(
            {
                "prompt": "test",
                "agent": "wee-qa",
                "permission_mode": "sandboxed",
                "yolo": True,
            }
        )
        self.assertEqual(data.get("permission_mode"), "sandboxed")

    def test_body_yolo_false_overrides_dispatch_config_yolo_true(self):
        """body.yolo=False must beat dispatch_config.yolo=True.

        This is the core regression for Issue #193: body.yolo=False was falsy
        so the 'or' chain fell through to dispatch_config, letting yolo=True
        in dispatch_config elevate the permission even when the caller
        explicitly opted out.
        """
        data = self._post(
            {
                "prompt": "test",
                "agent": "wee-qa",  # dispatch_config has yolo=True
                "yolo": False,  # explicit False must win
            }
        )
        self.assertEqual(
            data.get("permission_mode"),
            "restricted",
            "body.yolo=False must override dispatch_config.yolo=True",
        )

    def test_body_yolo_none_still_uses_dispatch_config(self):
        """Omitted body.yolo (None) should fall through to dispatch_config."""
        # wee-qa has dispatch_config.yolo=True
        data = self._post({"prompt": "test", "agent": "wee-qa"})
        self.assertEqual(
            data.get("permission_mode"),
            "elevated",
            "omitted body.yolo must let dispatch_config.yolo=True elevate",
        )


class TestBackgroundTaskRequestYoloField(unittest.TestCase):
    """Verify the /api/v1/background-tasks endpoint accepts yolo field."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        import agent_manager as am

        cls.app = am.create_api_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=False)
        cls.headers = {"Authorization": "Bearer shared_test_key_123"}

    def test_yolo_field_accepted_not_422(self):
        """POST /api/v1/background-tasks must accept yolo field (no 422)."""
        resp = self.client.post(
            "/api/v1/background-tasks",
            json={"prompt": "test", "agent": "wee-qa", "yolo": True},
            headers=self.headers,
        )
        self.assertNotEqual(
            resp.status_code,
            422,
            f"yolo field should be accepted, got 422: {resp.text[:200]}",
        )

    def test_yolo_false_accepted_not_422(self):
        """POST /api/v1/background-tasks must accept yolo=false."""
        resp = self.client.post(
            "/api/v1/background-tasks",
            json={"prompt": "test", "agent": "wee-qa", "yolo": False},
            headers=self.headers,
        )
        self.assertNotEqual(resp.status_code, 422)

    def test_yolo_omitted_accepted_not_422(self):
        """POST /api/v1/background-tasks must accept requests without yolo."""
        resp = self.client.post(
            "/api/v1/background-tasks",
            json={"prompt": "test", "agent": "wee-qa"},
            headers=self.headers,
        )
        self.assertNotEqual(resp.status_code, 422)

    def test_openapi_schema_has_yolo_property(self):
        """OpenAPI schema for background-tasks must include yolo property."""
        resp = self.client.get("/openapi.json")
        self.assertEqual(resp.status_code, 200)
        schema = resp.json()
        schemas = schema.get("components", {}).get("schemas", {})
        bg_req = schemas.get("BackgroundTaskRequest", {})
        props = bg_req.get("properties", {})
        self.assertIn(
            "yolo",
            props,
            "BackgroundTaskRequest schema must include 'yolo' property",
        )


if __name__ == "__main__":
    unittest.main()
