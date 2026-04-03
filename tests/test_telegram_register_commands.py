#!/usr/bin/env python3
"""Tests for F016: Register slash commands with Telegram BotFather.

Validates that register_bot_commands() builds the correct payload,
calls the Telegram Bot API setMyCommands endpoint, and handles
errors gracefully.
"""

import os
import sys
from unittest.mock import MagicMock, patch

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connector():
    """Create a minimal TelegramConnector with mocked network calls."""
    with patch("telegram_connector.requests") as mock_req:
        # Mock getMe so __init__ doesn't hit the network
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True,
            "result": {"id": 123456, "is_bot": True},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_req.get.return_value = mock_resp

        from telegram_connector import TelegramConnector

        connector = TelegramConnector(
            token="fake_token_1234567890",
            config_file="/dev/null",
        )
    return connector


def _mock_session_manager_commands():
    """Return a realistic slash command dict matching the current registry."""
    return {
        "/help": "Show available commands",
        "/status": "Check status of running query",
        "/cancel": "Cancel running query",
        "/capabilities": "Show agent capabilities",
        "/runtime": "Manage runtime (list/set/current)",
        "/agent": "Manage agent (list/set/current/invoke)",
        "/model": "Manage model (list/set/current)",
        "/session": "Manage session (list/reset/info)",
        "/timeout": "Get/set execution timeout",
        "/render": "Get/set output render format",
        "/notifications": "Toggle background notifications",
        "/mode": "Set permission mode",
        "/schedule": "Manage scheduled jobs",
        "/background": "Manage background tasks",
        "/update": "Pull latest and restart",
        "/upgrade": "Pull latest and restart",
        "/pull": "Pull latest and restart",
        "/secret": "Manage secrets (set/delete/list)",
    }


# ---------------------------------------------------------------------------
# 1. register_bot_commands — payload construction
# ---------------------------------------------------------------------------


class TestRegisterBotCommandsPayload:
    """Verify the BotCommand payload sent to setMyCommands."""

    def test_strips_leading_slash(self):
        """Command names must NOT have leading slash for Telegram API."""
        connector = _make_connector()
        cmds = _mock_session_manager_commands()

        with patch("telegram_connector.agent_manager.SessionManager") as MockSM:
            MockSM.return_value.get_slash_commands.return_value = cmds
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"ok": True}
            mock_resp.raise_for_status = MagicMock()
            with patch(
                "telegram_connector.requests.post", return_value=mock_resp
            ) as mock_post:
                connector.register_bot_commands()
                call_args = mock_post.call_args
                payload = (
                    call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
                )
                for cmd in payload["commands"]:
                    assert not cmd["command"].startswith(
                        "/"
                    ), f"Command {cmd['command']} should not start with /"

    def test_all_commands_lowercase(self):
        """Telegram requires command names to be lowercase."""
        connector = _make_connector()
        cmds = _mock_session_manager_commands()

        with patch("telegram_connector.agent_manager.SessionManager") as MockSM:
            MockSM.return_value.get_slash_commands.return_value = cmds
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"ok": True}
            mock_resp.raise_for_status = MagicMock()
            with patch(
                "telegram_connector.requests.post", return_value=mock_resp
            ) as mock_post:
                connector.register_bot_commands()
                payload = mock_post.call_args[1]["json"]
                for cmd in payload["commands"]:
                    assert cmd["command"] == cmd["command"].lower()

    def test_includes_registercommands(self):
        """The /registercommands refresh command must be in the list."""
        connector = _make_connector()
        cmds = _mock_session_manager_commands()

        with patch("telegram_connector.agent_manager.SessionManager") as MockSM:
            MockSM.return_value.get_slash_commands.return_value = cmds
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"ok": True}
            mock_resp.raise_for_status = MagicMock()
            with patch(
                "telegram_connector.requests.post", return_value=mock_resp
            ) as mock_post:
                connector.register_bot_commands()
                payload = mock_post.call_args[1]["json"]
                names = [c["command"] for c in payload["commands"]]
                assert "registercommands" in names

    def test_commands_sorted_alphabetically(self):
        """Commands should be sorted for consistent ordering."""
        connector = _make_connector()
        cmds = _mock_session_manager_commands()

        with patch("telegram_connector.agent_manager.SessionManager") as MockSM:
            MockSM.return_value.get_slash_commands.return_value = cmds
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"ok": True}
            mock_resp.raise_for_status = MagicMock()
            with patch(
                "telegram_connector.requests.post", return_value=mock_resp
            ) as mock_post:
                connector.register_bot_commands()
                payload = mock_post.call_args[1]["json"]
                names = [c["command"] for c in payload["commands"]]
                assert names == sorted(names)

    def test_descriptions_min_3_chars(self):
        """Telegram requires descriptions to be at least 3 characters."""
        connector = _make_connector()
        cmds = {"/x": "Hi"}  # short description

        with patch("telegram_connector.agent_manager.SessionManager") as MockSM:
            MockSM.return_value.get_slash_commands.return_value = cmds
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"ok": True}
            mock_resp.raise_for_status = MagicMock()
            with patch(
                "telegram_connector.requests.post", return_value=mock_resp
            ) as mock_post:
                connector.register_bot_commands()
                payload = mock_post.call_args[1]["json"]
                for cmd in payload["commands"]:
                    assert len(cmd["description"]) >= 3

    def test_descriptions_max_256_chars(self):
        """Telegram truncates descriptions at 256 characters."""
        connector = _make_connector()
        cmds = {"/longdesc": "A" * 500}

        with patch("telegram_connector.agent_manager.SessionManager") as MockSM:
            MockSM.return_value.get_slash_commands.return_value = cmds
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"ok": True}
            mock_resp.raise_for_status = MagicMock()
            with patch(
                "telegram_connector.requests.post", return_value=mock_resp
            ) as mock_post:
                connector.register_bot_commands()
                payload = mock_post.call_args[1]["json"]
                for cmd in payload["commands"]:
                    assert len(cmd["description"]) <= 256

    def test_skips_invalid_command_names(self):
        """Commands with hyphens or invalid chars should be skipped."""
        connector = _make_connector()
        cmds = {
            "/valid": "Good command",
            "/bad-name": "Has hyphen",
            "/good_name": "Has underscore",
        }

        with patch("telegram_connector.agent_manager.SessionManager") as MockSM:
            MockSM.return_value.get_slash_commands.return_value = cmds
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"ok": True}
            mock_resp.raise_for_status = MagicMock()
            with patch(
                "telegram_connector.requests.post", return_value=mock_resp
            ) as mock_post:
                connector.register_bot_commands()
                payload = mock_post.call_args[1]["json"]
                names = [c["command"] for c in payload["commands"]]
                assert "valid" in names
                assert "good_name" in names
                assert "bad-name" not in names
                assert "badname" not in names

    def test_no_duplicate_command_names(self):
        """Each command name should appear only once."""
        connector = _make_connector()
        cmds = _mock_session_manager_commands()

        with patch("telegram_connector.agent_manager.SessionManager") as MockSM:
            MockSM.return_value.get_slash_commands.return_value = cmds
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"ok": True}
            mock_resp.raise_for_status = MagicMock()
            with patch(
                "telegram_connector.requests.post", return_value=mock_resp
            ) as mock_post:
                connector.register_bot_commands()
                payload = mock_post.call_args[1]["json"]
                names = [c["command"] for c in payload["commands"]]
                assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# 2. register_bot_commands — API call
# ---------------------------------------------------------------------------


class TestRegisterBotCommandsAPI:
    """Verify the API call to Telegram and return messages."""

    def test_success_returns_checkmark(self):
        """Successful registration returns ✅ message."""
        connector = _make_connector()
        cmds = _mock_session_manager_commands()

        with patch("telegram_connector.agent_manager.SessionManager") as MockSM:
            MockSM.return_value.get_slash_commands.return_value = cmds
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"ok": True}
            mock_resp.raise_for_status = MagicMock()
            with patch("telegram_connector.requests.post", return_value=mock_resp):
                result = connector.register_bot_commands()
                assert "\u2705" in result
                assert "Registered" in result

    def test_api_failure_returns_error(self):
        """API exception returns ❌ error message."""
        connector = _make_connector()
        cmds = _mock_session_manager_commands()

        with patch("telegram_connector.agent_manager.SessionManager") as MockSM:
            MockSM.return_value.get_slash_commands.return_value = cmds
            with patch(
                "telegram_connector.requests.post",
                side_effect=Exception("Connection refused"),
            ):
                result = connector.register_bot_commands()
                assert "\u274c" in result
                assert "Connection refused" in result

    def test_api_not_ok_returns_warning(self):
        """Non-ok response returns ⚠️ warning."""
        connector = _make_connector()
        cmds = _mock_session_manager_commands()

        with patch("telegram_connector.agent_manager.SessionManager") as MockSM:
            MockSM.return_value.get_slash_commands.return_value = cmds
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "ok": False,
                "description": "Bad Request: invalid bot token",
            }
            mock_resp.raise_for_status = MagicMock()
            with patch("telegram_connector.requests.post", return_value=mock_resp):
                result = connector.register_bot_commands()
                assert "\u26a0" in result
                assert "Bad Request" in result

    def test_empty_commands_returns_warning(self):
        """No commands → warning, no API call."""
        connector = _make_connector()

        with patch("telegram_connector.agent_manager.SessionManager") as MockSM:
            MockSM.return_value.get_slash_commands.return_value = {}
            with patch("telegram_connector.requests.post") as mock_post:
                result = connector.register_bot_commands()
                assert "\u26a0" in result
                mock_post.assert_not_called()

    def test_session_manager_init_failure(self):
        """SessionManager import failure is handled gracefully."""
        connector = _make_connector()

        with patch(
            "telegram_connector.agent_manager.SessionManager",
            side_effect=Exception("import failed"),
        ):
            result = connector.register_bot_commands()
            assert "\u26a0" in result

    def test_calls_correct_api_url(self):
        """setMyCommands should be called against the bot's API URL."""
        connector = _make_connector()
        cmds = {"/help": "Show help"}

        with patch("telegram_connector.agent_manager.SessionManager") as MockSM:
            MockSM.return_value.get_slash_commands.return_value = cmds
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"ok": True}
            mock_resp.raise_for_status = MagicMock()
            with patch(
                "telegram_connector.requests.post", return_value=mock_resp
            ) as mock_post:
                connector.register_bot_commands()
                url = mock_post.call_args[0][0]
                assert url.endswith("/setMyCommands")
                assert "fake_token_1234567890" in url

    def test_command_count_in_success_message(self):
        """Success message should include the number of commands."""
        connector = _make_connector()
        cmds = {"/help": "Show help", "/status": "Check status"}

        with patch("telegram_connector.agent_manager.SessionManager") as MockSM:
            MockSM.return_value.get_slash_commands.return_value = cmds
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"ok": True}
            mock_resp.raise_for_status = MagicMock()
            with patch("telegram_connector.requests.post", return_value=mock_resp):
                result = connector.register_bot_commands()
                # 2 from registry + 1 registercommands = 3
                assert "3" in result
