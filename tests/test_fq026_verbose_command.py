"""Tests for FEATURE_QUEUE F026: /verbose slash command.

The /verbose command is the inverse of /silent — it toggles tool call
visibility using the same silent_mode session field but with inverted
semantics (/verbose on = silent_mode off).

Also tests _resolve_silent_default() with WEE_VERBOSE env var support.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_123")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "8099")

import agent_manager  # noqa: E402


class TestVerboseSlashCommand(unittest.TestCase):
    """Tests for /verbose slash command."""

    @classmethod
    def setUpClass(cls):
        cls.am = agent_manager
        cls.sm = agent_manager.SessionManager(
            config_file=os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "agents.json",
            ),
            app_env="DEV",
        )

    def test_verbose_registered(self):
        """/verbose should be in the slash command registry."""
        entry = self.sm._slash_command_registry.get("/verbose")
        self.assertIsNotNone(entry, "/verbose not found in registry")
        self.assertIsNotNone(entry["handler"])
        self.assertIn("verbose", entry["description"].lower())

    def test_verbose_current_status_on(self):
        """Show verbose ON when silent_mode is off."""
        sid = "test_fq026_status_on_1"
        data = self.sm.get_or_create_session_data(sid)
        self.sm.update_session_field(sid, "silent_mode", False)
        data = self.sm.load_session_data(sid)
        result = self.sm._slash_verbose(None, data, sid)
        self.assertIn("ON", result)
        self.assertIn("shown", result)

    def test_verbose_current_status_off(self):
        """Show verbose OFF when silent_mode is on."""
        sid = "test_fq026_status_off_2"
        data = self.sm.get_or_create_session_data(sid)
        self.sm.update_session_field(sid, "silent_mode", True)
        data = self.sm.load_session_data(sid)
        result = self.sm._slash_verbose(None, data, sid)
        self.assertIn("OFF", result)
        self.assertIn("hidden", result)

    def test_verbose_enable(self):
        """'/verbose on' sets silent_mode to False (show tool calls)."""
        sid = "test_fq026_enable_3"
        self.sm.get_or_create_session_data(sid)
        self.sm.update_session_field(sid, "silent_mode", True)
        data = self.sm.load_session_data(sid)
        result = self.sm._slash_verbose("on", data, sid)
        self.assertIn("enabled", result.lower())
        self.assertIn("visible", result.lower())
        updated = self.sm.load_session_data(sid)
        self.assertFalse(updated.get("silent_mode"))

    def test_verbose_disable(self):
        """'/verbose off' sets silent_mode to True (hide tool calls)."""
        sid = "test_fq026_disable_4"
        self.sm.get_or_create_session_data(sid)
        self.sm.update_session_field(sid, "silent_mode", False)
        data = self.sm.load_session_data(sid)
        result = self.sm._slash_verbose("off", data, sid)
        self.assertIn("disabled", result.lower())
        self.assertIn("hidden", result.lower())
        updated = self.sm.load_session_data(sid)
        self.assertTrue(updated.get("silent_mode"))

    def test_verbose_invalid_argument(self):
        """Invalid argument shows usage."""
        sid = "test_fq026_invalid_5"
        data = self.sm.get_or_create_session_data(sid)
        result = self.sm._slash_verbose("banana", data, sid)
        self.assertIn("Usage", result)

    def test_verbose_aliases_on(self):
        """Various enable aliases: true, 1, enable."""
        sid = "test_fq026_aliases_6"
        data = self.sm.get_or_create_session_data(sid)
        for alias in ("true", "1", "enable"):
            self.sm.update_session_field(sid, "silent_mode", True)
            data = self.sm.load_session_data(sid)
            result = self.sm._slash_verbose(alias, data, sid)
            self.assertIn("enabled", result.lower(), f"alias '{alias}' failed")
            updated = self.sm.load_session_data(sid)
            self.assertFalse(
                updated.get("silent_mode"),
                f"alias '{alias}' should set silent_mode=False",
            )

    def test_verbose_aliases_off(self):
        """Various disable aliases: false, 0, disable."""
        sid = "test_fq026_aliases_7"
        data = self.sm.get_or_create_session_data(sid)
        for alias in ("false", "0", "disable"):
            self.sm.update_session_field(sid, "silent_mode", False)
            data = self.sm.load_session_data(sid)
            result = self.sm._slash_verbose(alias, data, sid)
            self.assertIn("disabled", result.lower(), f"alias '{alias}' failed")
            updated = self.sm.load_session_data(sid)
            self.assertTrue(
                updated.get("silent_mode"),
                f"alias '{alias}' should set silent_mode=True",
            )

    def test_verbose_persists_to_session(self):
        """Verbose toggle persists in session data."""
        sid = "test_fq026_persist_8"
        self.sm.get_or_create_session_data(sid)
        self.sm._slash_verbose("on", {"silent_mode": True}, sid)
        reloaded = self.sm.load_session_data(sid)
        self.assertFalse(reloaded.get("silent_mode"))

    def test_verbose_in_slash_commands(self):
        """/verbose should appear in the help list."""
        cmds = self.sm.get_slash_commands()
        self.assertIn("/verbose", cmds)
        self.assertIn("verbose", cmds["/verbose"].lower())

    def test_verbose_inverse_of_silent(self):
        """'/verbose on' and '/silent off' produce same session state."""
        sid_v = "test_fq026_inverse_v_9"
        sid_s = "test_fq026_inverse_s_10"
        self.sm.get_or_create_session_data(sid_v)
        self.sm.get_or_create_session_data(sid_s)
        self.sm.update_session_field(sid_v, "silent_mode", True)
        self.sm.update_session_field(sid_s, "silent_mode", True)
        dv = self.sm.load_session_data(sid_v)
        ds = self.sm.load_session_data(sid_s)
        self.sm._slash_verbose("on", dv, sid_v)
        self.sm._slash_silent("off", ds, sid_s)
        v_data = self.sm.load_session_data(sid_v)
        s_data = self.sm.load_session_data(sid_s)
        self.assertEqual(
            v_data.get("silent_mode"),
            s_data.get("silent_mode"),
            "/verbose on should equal /silent off",
        )


class TestResolveSilentDefault(unittest.TestCase):
    """Tests for _resolve_silent_default() with WEE_VERBOSE env var."""

    def setUp(self):
        self._orig = os.environ.get("WEE_VERBOSE")
        os.environ.pop("WEE_VERBOSE", None)

    def tearDown(self):
        if self._orig is not None:
            os.environ["WEE_VERBOSE"] = self._orig
        else:
            os.environ.pop("WEE_VERBOSE", None)

    def test_channel_default_telegram(self):
        """Telegram defaults to silent (True)."""
        result = agent_manager._resolve_silent_default("telegram")
        self.assertTrue(result)

    def test_channel_default_webex(self):
        """WebEx defaults to silent (True)."""
        result = agent_manager._resolve_silent_default("webex")
        self.assertTrue(result)

    def test_channel_default_webui(self):
        """WebUI defaults to not silent (False)."""
        result = agent_manager._resolve_silent_default("webui")
        self.assertFalse(result)

    def test_channel_default_api(self):
        """API channel defaults to not silent (False)."""
        result = agent_manager._resolve_silent_default("api")
        self.assertFalse(result)

    def test_wee_verbose_true_overrides_telegram(self):
        """WEE_VERBOSE=true overrides telegram channel to not silent."""
        os.environ["WEE_VERBOSE"] = "true"
        result = agent_manager._resolve_silent_default("telegram")
        self.assertFalse(result)

    def test_wee_verbose_false_overrides_webui(self):
        """WEE_VERBOSE=false overrides webui channel to silent."""
        os.environ["WEE_VERBOSE"] = "false"
        result = agent_manager._resolve_silent_default("webui")
        self.assertTrue(result)

    def test_wee_verbose_1_means_verbose(self):
        """WEE_VERBOSE=1 means verbose (not silent)."""
        os.environ["WEE_VERBOSE"] = "1"
        result = agent_manager._resolve_silent_default("telegram")
        self.assertFalse(result)

    def test_wee_verbose_0_means_not_verbose(self):
        """WEE_VERBOSE=0 means not verbose (silent)."""
        os.environ["WEE_VERBOSE"] = "0"
        result = agent_manager._resolve_silent_default("webui")
        self.assertTrue(result)

    def test_wee_verbose_empty_uses_channel(self):
        """Empty WEE_VERBOSE falls through to channel default."""
        os.environ["WEE_VERBOSE"] = ""
        self.assertTrue(agent_manager._resolve_silent_default("telegram"))
        self.assertFalse(agent_manager._resolve_silent_default("webui"))

    def test_wee_verbose_on_off(self):
        """WEE_VERBOSE on/off string values."""
        os.environ["WEE_VERBOSE"] = "on"
        self.assertFalse(agent_manager._resolve_silent_default("telegram"))
        os.environ["WEE_VERBOSE"] = "off"
        self.assertTrue(agent_manager._resolve_silent_default("webui"))


if __name__ == "__main__":
    unittest.main()
