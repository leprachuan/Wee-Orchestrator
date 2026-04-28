"""Tests for F027: Verbose mode toggle for tool call visibility.

WebUI toggle button to show/hide tool call output in chat. Reuses F026
silent_mode session field. PATCH /api/v1/sessions/{id}/settings endpoint
persists the toggle per session.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_123")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "8099")


class TestSessionStatusIncludesSilentMode(unittest.TestCase):
    """Session status endpoint must include silent_mode field."""

    @classmethod
    def setUpClass(cls):
        import agent_manager

        cls.am = agent_manager
        cls.sm = agent_manager.SessionManager(
            config_file=os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "agents.json",
            ),
            app_env="DEV",
        )

    def test_webui_session_status_silent_mode_false(self):
        """WebUI sessions should return silent_mode=False in status."""
        sid = "webui_f027_status_1"
        data = self.sm.get_or_create_session_data(sid)
        self.assertFalse(
            data.get("silent_mode", False),
            "WebUI sessions should default silent_mode=False",
        )

    def test_telegram_session_status_silent_mode_true(self):
        """Telegram sessions should return silent_mode=True in status."""
        sid = "telegram_f027_status_2"
        data = self.sm.get_or_create_session_data(sid)
        self.assertTrue(
            data.get("silent_mode"),
            "Telegram sessions should default silent_mode=True",
        )

    def test_toggle_silent_mode_persists(self):
        """Toggling silent_mode via update_session_field persists."""
        sid = "webui_f027_toggle_3"
        self.sm.get_or_create_session_data(sid)

        data = self.sm.load_session_data(sid)
        self.assertFalse(data.get("silent_mode", False))

        self.sm.update_session_field(sid, "silent_mode", True)
        data = self.sm.load_session_data(sid)
        self.assertTrue(data.get("silent_mode"))

        self.sm.update_session_field(sid, "silent_mode", False)
        data = self.sm.load_session_data(sid)
        self.assertFalse(data.get("silent_mode"))

    def test_toggle_preserves_other_fields(self):
        """Toggling silent_mode must not clobber other session fields."""
        sid = "webui_f027_preserve_4"
        self.sm.get_or_create_session_data(sid)
        self.sm.update_session_field(sid, "model", "test-model-f027")
        self.sm.update_session_field(sid, "agent", "orchestrator")

        self.sm.update_session_field(sid, "silent_mode", True)

        data = self.sm.load_session_data(sid)
        self.assertTrue(data.get("silent_mode"))
        self.assertEqual(data.get("model"), "test-model-f027")
        self.assertEqual(data.get("agent"), "orchestrator")


class TestPatchSessionSettingsEndpoint(unittest.TestCase):
    """Tests for PATCH /api/v1/sessions/{session_id}/settings endpoint."""

    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch

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
        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.auth = {"Authorization": "Bearer shared_test_key_123"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()

    def _create_session(self, sid):
        """Helper to create a test session."""
        self.client.post(
            "/api/v1/sessions/create",
            json={"session_id": sid},
            headers=self.auth,
        )

    def test_patch_toggle_silent_mode_on(self):
        """PATCH should set silent_mode=True."""
        sid = "f027_patch_on"
        self._create_session(sid)
        resp = self.client.patch(
            f"/api/v1/sessions/{sid}/settings",
            json={"silent_mode": True},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("updated", body)
        self.assertTrue(body["updated"]["silent_mode"])

    def test_patch_toggle_silent_mode_off(self):
        """PATCH should set silent_mode=False."""
        sid = "f027_patch_off"
        self._create_session(sid)
        self.client.patch(
            f"/api/v1/sessions/{sid}/settings",
            json={"silent_mode": True},
            headers=self.auth,
        )
        resp = self.client.patch(
            f"/api/v1/sessions/{sid}/settings",
            json={"silent_mode": False},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["updated"]["silent_mode"])

    def test_patch_requires_auth(self):
        """PATCH without auth should return 401."""
        resp = self.client.patch(
            "/api/v1/sessions/any_session/settings",
            json={"silent_mode": True},
        )
        self.assertEqual(resp.status_code, 401)

    def test_patch_rejects_non_boolean(self):
        """PATCH with non-boolean silent_mode should return 422."""
        sid = "f027_patch_invalid"
        self._create_session(sid)
        resp = self.client.patch(
            f"/api/v1/sessions/{sid}/settings",
            json={"silent_mode": "yes"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 422)

    def test_patch_rejects_unknown_fields(self):
        """PATCH with only unknown fields should return 422."""
        sid = "f027_patch_unknown"
        self._create_session(sid)
        resp = self.client.patch(
            f"/api/v1/sessions/{sid}/settings",
            json={"unknown_field": True},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 422)

    def test_patch_ignores_unknown_with_valid(self):
        """PATCH with valid + unknown should apply valid only."""
        sid = "f027_patch_mixed"
        self._create_session(sid)
        resp = self.client.patch(
            f"/api/v1/sessions/{sid}/settings",
            json={"silent_mode": True, "bad_field": "ignored"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("silent_mode", body["updated"])
        self.assertNotIn("bad_field", body["updated"])

    def test_patch_session_not_found(self):
        """PATCH on non-existent session should return 404."""
        resp = self.client.patch(
            "/api/v1/sessions/nonexistent_f027_xyz/settings",
            json={"silent_mode": True},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 404)

    def test_status_reflects_patched_value(self):
        """GET status should show updated silent_mode after PATCH."""
        sid = "f027_status_after_patch"
        self._create_session(sid)

        self.client.patch(
            f"/api/v1/sessions/{sid}/settings",
            json={"silent_mode": True},
            headers=self.auth,
        )

        resp = self.client.get(
            f"/api/v1/sessions/{sid}/status",
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("silent_mode"))


class TestWebUIVerboseToggle(unittest.TestCase):
    """Tests for WebUI frontend changes (static file checks)."""

    def _read_file(self, *parts):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            *parts,
        )
        with open(path) as f:
            return f.read()

    def test_index_html_has_verbose_button(self):
        """index.html should contain the verbose toggle button."""
        html = self._read_file("webui", "dist", "index.html")
        self.assertIn('id="btn-verbose-toggle"', html)
        self.assertIn("meta-verbose", html)

    def test_index_html_cache_bust_updated(self):
        """index.html cache-bust should be v=20260417263ef08."""
        html = self._read_file("webui", "dist", "index.html")
        self.assertIn("v=20260417263ef08", html)

    def test_app_css_has_verbose_styles(self):
        """app.css should contain verbose toggle CSS rules."""
        css = self._read_file("webui", "dist", "app.css")
        self.assertIn(".meta-pill.meta-verbose", css)
        self.assertIn(".tool-calls-hidden .tc-line", css)
        self.assertIn(".verbose-off", css)

    def test_app_js_has_silent_mode_state(self):
        """app.js should have silentMode in STATE."""
        js = self._read_file("webui", "dist", "app.js")
        self.assertIn("silentMode:", js)

    def test_app_js_has_update_verbose_ui(self):
        """app.js should have _updateVerboseToggleUI function."""
        js = self._read_file("webui", "dist", "app.js")
        self.assertIn("_updateVerboseToggleUI", js)

    def test_app_js_has_settings_api_call(self):
        """app.js should call PATCH /sessions/{id}/settings."""
        js = self._read_file("webui", "dist", "app.js")
        self.assertIn("/settings", js)
        self.assertIn("silent_mode", js)

    def test_app_js_verbose_toggle_has_aria(self):
        """Toggle button should use aria-pressed attribute."""
        js = self._read_file("webui", "dist", "app.js")
        self.assertIn("aria-pressed", js)


if __name__ == "__main__":
    unittest.main()
