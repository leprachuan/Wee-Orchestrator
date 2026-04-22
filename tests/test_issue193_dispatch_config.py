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
    """Unit tests for the priority resolution: body > dispatch_config > session."""

    def _resolve(
        self,
        body_runtime,
        body_model,
        body_perm,
        body_yolo,
        dispatch_cfg,
        session_runtime="copilot",
        session_model="gpt-5-mini",
    ):
        """Simulate the resolution logic from create_background_task."""
        runtime = body_runtime or dispatch_cfg.get("runtime") or session_runtime
        model = body_model or dispatch_cfg.get("model") or session_model
        _dc_perm = (
            "elevated"
            if dispatch_cfg.get("yolo")
            else dispatch_cfg.get("permission_mode", "")
        )
        perm_mode = (
            body_perm or ("elevated" if body_yolo else None) or _dc_perm or "restricted"
        )
        if perm_mode not in ("elevated", "restricted", "sandboxed"):
            perm_mode = "restricted"
        return runtime, model, perm_mode

    def test_body_overrides_all(self):
        """Explicit body values must override dispatch_config and session."""
        rt, mdl, pm = self._resolve(
            "my-runtime",
            "my-model",
            "sandboxed",
            None,
            {
                "runtime": "openai",
                "model": "gpt-5.4",
                "permission_mode": "elevated",
            },
            "session-runtime",
            "session-model",
        )
        self.assertEqual(rt, "my-runtime")
        self.assertEqual(mdl, "my-model")
        self.assertEqual(pm, "sandboxed")

    def test_dispatch_config_overrides_session_defaults(self):
        """dispatch_config beats session defaults when body is empty."""
        rt, mdl, pm = self._resolve(
            None,
            None,
            None,
            None,
            {
                "runtime": "openai",
                "model": "gpt-5.4",
                "permission_mode": "elevated",
            },
            "session-runtime",
            "session-model",
        )
        self.assertEqual(rt, "openai")
        self.assertEqual(mdl, "gpt-5.4")
        self.assertEqual(pm, "elevated")

    def test_wee_qa_dispatch_config_scenario(self):
        """Simulate exact wee-qa dispatch scenario from the issue report."""
        rt, mdl, pm = self._resolve(
            None,
            None,
            None,
            None,
            {"runtime": "openai", "model": "gpt-5.4-mini", "yolo": True},
            "copilot",
            "gpt-5-mini",
        )
        self.assertEqual(rt, "openai")
        self.assertEqual(mdl, "gpt-5.4-mini")
        self.assertEqual(pm, "elevated")

    def test_session_defaults_when_dispatch_config_empty(self):
        """Session defaults apply when dispatch_config is empty."""
        rt, mdl, pm = self._resolve(
            None, None, None, None, {}, "copilot", "claude-haiku"
        )
        self.assertEqual(rt, "copilot")
        self.assertEqual(mdl, "claude-haiku")
        self.assertEqual(pm, "restricted")

    def test_body_yolo_true_elevates_perm_mode(self):
        """body.yolo=True must resolve to elevated perm_mode."""
        _, _, pm = self._resolve(None, None, None, True, {}, "copilot", "gpt-5-mini")
        self.assertEqual(pm, "elevated")

    def test_body_permission_mode_takes_highest_priority(self):
        """Explicit body.permission_mode wins over all other sources."""
        _, _, pm = self._resolve(
            None,
            None,
            "sandboxed",
            True,
            {"permission_mode": "elevated", "yolo": True},
        )
        self.assertEqual(pm, "sandboxed")


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
