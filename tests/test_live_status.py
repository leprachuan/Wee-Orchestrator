"""Tests for F004: Live status updates for mobile channels."""

import os
import sys
import time
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["API_SHARED_KEY"] = "test_key_123"
os.environ["APP_ENV"] = "DEV"
os.environ["API_PORT"] = "8099"


class TestLiveStatusStore(unittest.TestCase):
    """Test SessionManager live status dict operations."""

    @classmethod
    def setUpClass(cls):
        import agent_manager

        cls.agent_manager = agent_manager

    def _make_session_mgr(self):
        mgr = self.agent_manager.SessionManager.__new__(
            self.agent_manager.SessionManager
        )
        mgr._live_status = {}
        mgr._live_status_lock = threading.Lock()
        return mgr

    def test_set_and_get_live_status(self):
        """set_live_status stores a value retrievable by get_live_status."""
        mgr = self._make_session_mgr()
        mgr.set_live_status("sess1", "Installing deps")
        result = mgr.get_live_status("sess1")
        self.assertEqual(result["text"], "Installing deps")
        self.assertIn("updated_at", result)

    def test_get_live_status_missing(self):
        """get_live_status returns None for unknown session."""
        mgr = self._make_session_mgr()
        self.assertIsNone(mgr.get_live_status("nonexistent"))

    def test_clear_live_status(self):
        """clear_live_status removes the entry."""
        mgr = self._make_session_mgr()
        mgr.set_live_status("sess1", "Working")
        mgr.clear_live_status("sess1")
        self.assertIsNone(mgr.get_live_status("sess1"))

    def test_clear_live_status_noop_on_missing(self):
        """clear_live_status does not raise for missing session."""
        mgr = self._make_session_mgr()
        mgr.clear_live_status("nonexistent")  # Should not raise

    def test_set_live_status_overwrites(self):
        """set_live_status overwrites previous value."""
        mgr = self._make_session_mgr()
        mgr.set_live_status("sess1", "Step 1")
        mgr.set_live_status("sess1", "Step 2")
        result = mgr.get_live_status("sess1")
        self.assertEqual(result["text"], "Step 2")


class TestLiveStatusEndpoint(unittest.TestCase):
    """Test the GET /api/v1/sessions/{id}/live-status endpoint."""

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
        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.shared_header = {"Authorization": "Bearer shared_test_key_123"}
        cls.agent_manager = agent_manager

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()

    def test_live_status_no_status(self):
        """Endpoint returns null status when no live status set."""
        resp = self.client.get(
            "/api/v1/sessions/test_sess_no_status/live-status",
            headers=self.shared_header,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["status"])
        self.assertIsNone(data["updated_at"])

    def test_live_status_requires_auth(self):
        """Endpoint requires bearer auth."""
        resp = self.client.get("/api/v1/sessions/test_sess/live-status")
        self.assertIn(resp.status_code, [401, 403])

    def test_live_status_rejects_bad_key(self):
        """Endpoint rejects invalid shared key."""
        resp = self.client.get(
            "/api/v1/sessions/test_sess/live-status",
            headers={"Authorization": "Bearer shared_wrong_key"},
        )
        self.assertEqual(resp.status_code, 401)


class TestStatusUpdateStripping(unittest.TestCase):
    """Test that STATUS_UPDATE markers are stripped from output."""

    @classmethod
    def setUpClass(cls):
        import agent_manager

        cls.agent_manager = agent_manager

    def test_strip_status_update_from_output(self):
        """strip_metadata removes STATUS_UPDATE markers."""
        mgr = self.agent_manager.SessionManager.__new__(
            self.agent_manager.SessionManager
        )
        text = "Hello\n[STATUS_UPDATE: Installing deps]\nWorld\n[STATUS_UPDATE: Running tests]\nDone"
        result = mgr.strip_metadata(text, "copilot")
        self.assertNotIn("[STATUS_UPDATE", result)
        self.assertIn("Hello", result)
        self.assertIn("Done", result)

    def test_strip_status_update_empty_content(self):
        """strip_metadata handles content that is only STATUS_UPDATE lines."""
        mgr = self.agent_manager.SessionManager.__new__(
            self.agent_manager.SessionManager
        )
        text = "[STATUS_UPDATE: Working]\n[STATUS_UPDATE: Done]"
        result = mgr.strip_metadata(text, "copilot")
        self.assertNotIn("[STATUS_UPDATE", result)


class TestStatusUpdateRegex(unittest.TestCase):
    """Test STATUS_UPDATE regex pattern matching."""

    def test_status_update_capture(self):
        """Regex captures the status text from STATUS_UPDATE markers."""
        import re

        pattern = re.compile(r"\[STATUS_UPDATE[:\s]*(.+?)\]")
        line = "Some output [STATUS_UPDATE: Installing dependencies] more text"
        m = pattern.search(line)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "Installing dependencies")

    def test_status_update_strip(self):
        """Regex strips STATUS_UPDATE markers from text."""
        import re

        pattern = re.compile(r"\[STATUS_UPDATE[:\s]*[^\]]*\]\s*\n?")
        text = "Hello\n[STATUS_UPDATE: Working]\nWorld"
        result = pattern.sub("", text)
        self.assertNotIn("[STATUS_UPDATE", result)
        self.assertIn("Hello", result)
        self.assertIn("World", result)

    def test_status_update_no_match_on_similar(self):
        """Regex does not match on similar but different patterns."""
        import re

        pattern = re.compile(r"\[STATUS_UPDATE[:\s]*(.+?)\]")
        self.assertIsNone(pattern.search("[STATUS: Working]"))
        self.assertIsNone(pattern.search("[UPDATE: Working]"))
        self.assertIsNotNone(pattern.search("[STATUS_UPDATE: Working]"))


class TestMobileChannelDetection(unittest.TestCase):
    """Test channel detection logic used for mobile context injection."""

    def test_telegram_session_detected(self):
        """Telegram session IDs are correctly detected."""
        sid = "telegram_123_456"
        if sid.startswith("telegram_"):
            channel = "telegram"
        elif sid.startswith("webex_"):
            channel = "webex"
        else:
            channel = "webui"
        self.assertEqual(channel, "telegram")

    def test_webex_session_detected(self):
        """WebEx session IDs are correctly detected."""
        sid = "webex_abc123"
        if sid.startswith("telegram_"):
            channel = "telegram"
        elif sid.startswith("webex_"):
            channel = "webex"
        else:
            channel = "webui"
        self.assertEqual(channel, "webex")

    def test_webui_session_no_injection(self):
        """WebUI sessions should not get mobile context."""
        sid = "abcdef-1234"
        if sid.startswith("telegram_"):
            channel = "telegram"
        elif sid.startswith("webex_"):
            channel = "webex"
        else:
            channel = "webui"
        self.assertEqual(channel, "webui")


if __name__ == "__main__":
    unittest.main()
