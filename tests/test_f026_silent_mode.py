"""Tests for F026: Silent mode for mobile channels.

Silent mode hides tool call output from SSE responses on mobile channels
(Telegram, WebEx) while keeping tool execution functional. Default: enabled
for Telegram/WebEx, disabled for WebUI.
"""

import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_123")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "8099")


class TestSilentModeDefaults(unittest.TestCase):
    """Test silent_mode defaults based on channel detection."""

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

    def test_telegram_session_defaults_silent_on(self):
        """Telegram sessions should default silent_mode=True."""
        sid = "telegram_test_f026_1"
        data = self.sm.get_or_create_session_data(sid)
        self.assertTrue(
            data.get("silent_mode"),
            "Telegram sessions should have silent_mode=True by default",
        )

    def test_webex_session_defaults_silent_on(self):
        """WebEx sessions should default silent_mode=True."""
        sid = "webex_test_f026_2"
        data = self.sm.get_or_create_session_data(sid)
        self.assertTrue(
            data.get("silent_mode"),
            "WebEx sessions should have silent_mode=True by default",
        )

    def test_webui_session_defaults_silent_off(self):
        """WebUI sessions should default silent_mode=False."""
        sid = "webui_test_f026_3"
        data = self.sm.get_or_create_session_data(sid)
        self.assertFalse(
            data.get("silent_mode"),
            "WebUI sessions should have silent_mode=False by default",
        )

    def test_api_session_defaults_silent_off(self):
        """API sessions (no prefix) should default silent_mode=False."""
        sid = "test_f026_api_4"
        data = self.sm.get_or_create_session_data(sid)
        self.assertFalse(
            data.get("silent_mode"),
            "API sessions should have silent_mode=False by default",
        )


class TestSlashSilentCommand(unittest.TestCase):
    """Test /silent slash command handler."""

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

    def test_silent_registered(self):
        """/silent should be in the slash command registry."""
        entry = self.sm._slash_command_registry.get("/silent")
        self.assertIsNotNone(entry, "/silent not found in registry")
        self.assertIsNotNone(entry["handler"])
        self.assertIn("silent", entry["description"].lower())

    def test_silent_current_status_off(self):
        """Show current status when silent_mode is off."""
        sid = "test_f026_slash_5"
        data = self.sm.get_or_create_session_data(sid)
        result = self.sm._slash_silent(None, data, sid)
        self.assertIn("OFF", result)
        self.assertIn("shown", result)

    def test_silent_current_status_on(self):
        """Show current status when silent_mode is on."""
        sid = "telegram_test_f026_slash_6"
        data = self.sm.get_or_create_session_data(sid)
        result = self.sm._slash_silent(None, data, sid)
        self.assertIn("ON", result)
        self.assertIn("hidden", result)

    def test_silent_enable(self):
        """'/silent on' enables silent mode."""
        sid = "test_f026_enable_7"
        self.sm.get_or_create_session_data(sid)
        # Ensure starting state is off
        self.sm.update_session_field(sid, "silent_mode", False)
        data = self.sm.load_session_data(sid)
        result = self.sm._slash_silent("on", data, sid)
        self.assertIn("enabled", result.lower())
        updated = self.sm.load_session_data(sid)
        self.assertTrue(updated.get("silent_mode"))

    def test_silent_disable(self):
        """'/silent off' disables silent mode."""
        sid = "telegram_test_f026_disable_8"
        self.sm.get_or_create_session_data(sid)
        # Ensure starting state is on
        self.sm.update_session_field(sid, "silent_mode", True)
        data = self.sm.load_session_data(sid)
        result = self.sm._slash_silent("off", data, sid)
        self.assertIn("disabled", result.lower())
        updated = self.sm.load_session_data(sid)
        self.assertFalse(updated.get("silent_mode"))

    def test_silent_invalid_argument(self):
        """Invalid argument shows usage."""
        sid = "test_f026_slash_9"
        data = self.sm.get_or_create_session_data(sid)
        result = self.sm._slash_silent("banana", data, sid)
        self.assertIn("Usage", result)

    def test_silent_aliases(self):
        """Various enable/disable aliases work."""
        sid = "test_f026_slash_10"
        data = self.sm.get_or_create_session_data(sid)

        for alias in ("true", "1", "enable"):
            result = self.sm._slash_silent(alias, data, sid)
            self.assertIn("enabled", result.lower(), f"alias '{alias}' failed")
            updated = self.sm.load_session_data(sid)
            self.assertTrue(
                updated.get("silent_mode"), f"alias '{alias}' didn't set True"
            )

        for alias in ("false", "0", "disable"):
            result = self.sm._slash_silent(alias, data, sid)
            self.assertIn("disabled", result.lower(), f"alias '{alias}' failed")
            updated = self.sm.load_session_data(sid)
            self.assertFalse(
                updated.get("silent_mode"), f"alias '{alias}' didn't set False"
            )

    def test_silent_persists_to_session(self):
        """Silent mode preference persists in session data."""
        sid = "test_f026_persist_11"
        self.sm.get_or_create_session_data(sid)
        self.sm._slash_silent("on", {"silent_mode": False}, sid)
        reloaded = self.sm.load_session_data(sid)
        self.assertTrue(reloaded.get("silent_mode"))


class TestSilentModeContextInjection(unittest.TestCase):
    """Test that silent mode appears in the context injection."""

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

    def test_context_contains_silent_mode_on(self):
        """Context injection includes silent mode note when enabled."""
        sid = "telegram_test_f026_ctx_12"
        data = self.sm.get_or_create_session_data(sid)
        self.assertTrue(data.get("silent_mode"))
        ctx = self.sm.build_agent_context_prompt(
            agent=data.get("agent", "orchestrator"),
            prompt="test",
            n8n_session_id=sid,
            channel="telegram",
        )
        self.assertIn("[Silent Mode: ON]", ctx)
        self.assertIn("hidden from the user", ctx)

    def test_context_no_silent_mode_off(self):
        """Context injection does not include silent note when disabled."""
        sid = "test_f026_ctx_13"
        data = self.sm.get_or_create_session_data(sid)
        self.assertFalse(data.get("silent_mode"))
        ctx = self.sm.build_agent_context_prompt(
            agent=data.get("agent", "orchestrator"),
            prompt="test",
            n8n_session_id=sid,
            channel="webui",
        )
        self.assertNotIn("[Silent Mode: ON]", ctx)


class TestGetSlashCommands(unittest.TestCase):
    """Test that /silent appears in get_slash_commands()."""

    @classmethod
    def setUpClass(cls):
        import agent_manager

        cls.sm = agent_manager.SessionManager(
            config_file=os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "agents.json",
            ),
            app_env="DEV",
        )

    def test_silent_in_slash_commands(self):
        """/silent should appear in the public slash commands list."""
        cmds = self.sm.get_slash_commands()
        self.assertIn("/silent", cmds)
        self.assertIn("silent", cmds["/silent"].lower())


if __name__ == "__main__":
    unittest.main()
