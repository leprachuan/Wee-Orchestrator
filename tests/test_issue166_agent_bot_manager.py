#!/usr/bin/env python3
"""Tests for issue #166: Per-agent Telegram/WebEx bots."""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure the module can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_bot_manager import TelegramAgentBot  # noqa: E402
from agent_bot_manager import (  # noqa: E402
    AgentBotManager,
    WebExAgentBot,
    resolve_secret,
)

# ---------------------------------------------------------------------------
# resolve_secret
# ---------------------------------------------------------------------------


class TestResolveSecret:
    """Tests for secret resolution via secret_tool.py."""

    @patch("agent_bot_manager.SECRET_TOOL_PATH")
    @patch("subprocess.run")
    def test_resolve_secret_success(self, mock_run, mock_path):
        mock_path.exists.return_value = True
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"status": "success", "value": "tok123"}),
            stderr="",
        )
        assert resolve_secret("MY_SECRET") == "tok123"

    @patch("agent_bot_manager.SECRET_TOOL_PATH")
    @patch("subprocess.run")
    def test_resolve_secret_plain_text_output(self, mock_run, mock_path):
        mock_path.exists.return_value = True
        mock_run.return_value = MagicMock(
            returncode=0, stdout="plain_token_value\n", stderr=""
        )
        assert resolve_secret("MY_SECRET") == "plain_token_value"

    @patch("agent_bot_manager.SECRET_TOOL_PATH")
    @patch("subprocess.run")
    def test_resolve_secret_failure(self, mock_run, mock_path):
        mock_path.exists.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
        assert resolve_secret("MISSING") is None

    @patch("agent_bot_manager.SECRET_TOOL_PATH")
    def test_resolve_secret_no_secret_tool(self, mock_path):
        mock_path.exists.return_value = False
        assert resolve_secret("ANY") is None

    @patch("agent_bot_manager.SECRET_TOOL_PATH")
    @patch("subprocess.run", side_effect=Exception("timeout"))
    def test_resolve_secret_exception(self, mock_run, mock_path):
        mock_path.exists.return_value = True
        assert resolve_secret("TIMEOUT") is None


# ---------------------------------------------------------------------------
# TelegramAgentBot
# ---------------------------------------------------------------------------


class TestTelegramAgentBot:
    """Tests for the per-agent Telegram bot."""

    def _make_bot(self, **kwargs):
        defaults = dict(
            agent_name="smarthome",
            token="fake_token",
            allowed_users=["8193231291"],
            api_url="https://127.0.0.1:8000",
            api_shared_key="test_key",
        )
        defaults.update(kwargs)
        return TelegramAgentBot(**defaults)

    def test_init_basic(self):
        bot = self._make_bot()
        assert bot.agent_name == "smarthome"
        assert bot.allowed_users == [8193231291]
        assert bot.running is False

    def test_is_user_allowed_with_list(self):
        bot = self._make_bot(allowed_users=["123", "456"])
        assert bot._is_user_allowed(123) is True
        assert bot._is_user_allowed(789) is False

    def test_is_user_allowed_empty_list(self):
        bot = self._make_bot(allowed_users=None)
        assert bot._is_user_allowed(999) is True

    def test_sanitize_html_basic(self):
        result = TelegramAgentBot._sanitize_html("<b>bold</b> & <script>bad</script>")
        assert "<b>bold</b>" in result
        assert "<script>" not in result
        assert "&amp;" in result or "&" in result

    def test_sanitize_html_empty(self):
        assert TelegramAgentBot._sanitize_html("") == "No response"
        assert TelegramAgentBot._sanitize_html(None) == "No response"

    def test_handle_slash_start(self):
        bot = self._make_bot()
        bot.bot_info = {"first_name": "SmartHome Bot"}
        resp = bot._handle_slash_command("/start", 123, 456)
        assert resp is not None
        assert "smarthome" in resp

    def test_handle_slash_help(self):
        bot = self._make_bot()
        resp = bot._handle_slash_command("/help", 123, 456)
        assert resp is not None
        assert "smarthome" in resp

    def test_handle_slash_status(self):
        bot = self._make_bot()
        resp = bot._handle_slash_command("/status", 123, 456)
        assert "smarthome" in resp
        assert "online" in resp.lower()

    def test_handle_slash_agent_blocked(self):
        bot = self._make_bot()
        resp = bot._handle_slash_command("/agent set devops", 123, 456)
        assert "disabled" in resp.lower()

    def test_handle_slash_agent_set_blocked(self):
        bot = self._make_bot()
        resp = bot._handle_slash_command("/agent_set devops", 123, 456)
        assert "disabled" in resp.lower()

    def test_handle_slash_model_passes_through(self):
        bot = self._make_bot()
        resp = bot._handle_slash_command("/model gpt-4", 123, 456)
        assert resp is None  # Should pass through to API

    def test_handle_slash_with_bot_mention(self):
        bot = self._make_bot()
        resp = bot._handle_slash_command("/help@SmartHomeBot", 123, 456)
        assert resp is not None
        assert "smarthome" in resp

    def test_handle_regular_message_passes_through(self):
        bot = self._make_bot()
        resp = bot._handle_slash_command("hello world", 123, 456)
        assert resp is None

    @patch("requests.post")
    @patch("requests.get")
    def test_get_me(self, mock_get, mock_post):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"ok": True, "result": {"id": 123, "username": "test_bot"}},
        )
        mock_get.return_value.raise_for_status = MagicMock()
        bot = self._make_bot()
        info = bot._get_me()
        assert info["username"] == "test_bot"

    @patch("requests.get")
    def test_get_updates(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "ok": True,
                "result": [{"update_id": 100, "message": {"text": "hi"}}],
            },
        )
        mock_get.return_value.raise_for_status = MagicMock()
        bot = self._make_bot()
        updates = bot._get_updates(timeout=1)
        assert len(updates) == 1
        assert bot.offset == 101

    @patch("requests.post")
    def test_send_message(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"ok": True, "result": {"message_id": 42}},
        )
        bot = self._make_bot()
        msg_id = bot._send_message(123, "Hello!")
        assert msg_id == 42

    @patch("requests.post")
    def test_send_message_html_fallback(self, mock_post):
        """Test that failed HTML send falls back to plain text."""
        mock_post.side_effect = [
            MagicMock(status_code=400),  # HTML fails
            MagicMock(
                status_code=200,
                json=lambda: {"ok": True, "result": {"message_id": 43}},
            ),  # Plain succeeds
        ]
        bot = self._make_bot()
        msg_id = bot._send_message(123, "Hello!")
        assert msg_id == 43

    @patch("requests.post")
    def test_edit_message(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        bot = self._make_bot()
        assert bot._edit_message(123, 42, "Updated!") is True

    @patch("requests.post")
    def test_execute_via_api(self, mock_post):
        mock_post.side_effect = [
            MagicMock(status_code=200),  # session create
            MagicMock(
                status_code=200,
                json=lambda: {"response": "Agent says hi"},
            ),  # execute
        ]
        bot = self._make_bot()
        resp = bot._execute_via_api("hello", "sess_1", "user_123")
        assert resp == "Agent says hi"
        # Verify agent was pinned in the session create call
        create_call = mock_post.call_args_list[0]
        assert create_call[1]["json"]["agent"] == "smarthome"

    @patch("requests.post")
    def test_execute_via_api_failure(self, mock_post):
        mock_post.side_effect = [
            MagicMock(status_code=200),
            MagicMock(status_code=500, text="Internal error"),
        ]
        bot = self._make_bot()
        resp = bot._execute_via_api("hello", "sess_1", "user_123")
        assert "Error" in resp or "⚠️" in resp

    @patch("requests.post")
    def test_execute_via_api_exception(self, mock_post):
        mock_post.side_effect = Exception("Connection refused")
        bot = self._make_bot()
        resp = bot._execute_via_api("hello", "sess_1", "user_123")
        assert "unavailable" in resp.lower()

    @patch.object(TelegramAgentBot, "_execute_via_api", return_value="Bot response")
    @patch.object(TelegramAgentBot, "_edit_message", return_value=True)
    @patch.object(TelegramAgentBot, "_send_message", return_value=99)
    @patch("requests.post")
    def test_handle_message_full_flow(
        self, mock_req_post, mock_send, mock_edit, mock_api
    ):
        bot = self._make_bot()
        update = {
            "message": {
                "text": "turn on the lights",
                "from": {"id": 8193231291, "is_bot": False, "username": "foster"},
                "chat": {"id": 8193231291},
            }
        }
        bot._handle_message(update)
        mock_send.assert_called()
        mock_api.assert_called_once()
        mock_edit.assert_called_once()

    @patch.object(TelegramAgentBot, "_send_message")
    def test_handle_message_unauthorized(self, mock_send):
        bot = self._make_bot(allowed_users=["111"])
        update = {
            "message": {
                "text": "hello",
                "from": {"id": 999, "is_bot": False},
                "chat": {"id": 999},
            }
        }
        bot._handle_message(update)
        mock_send.assert_called_once()
        assert "not authorized" in mock_send.call_args[0][1].lower()

    def test_handle_message_ignores_bots(self):
        bot = self._make_bot()
        update = {
            "message": {
                "text": "hello",
                "from": {"id": 123, "is_bot": True},
                "chat": {"id": 123},
            }
        }
        # Should not raise
        bot._handle_message(update)

    @patch.object(TelegramAgentBot, "_send_message")
    def test_handle_message_slash_command(self, mock_send):
        bot = self._make_bot()
        update = {
            "message": {
                "text": "/help",
                "from": {"id": 8193231291, "is_bot": False},
                "chat": {"id": 8193231291},
            }
        }
        bot._handle_message(update)
        mock_send.assert_called_once()
        assert "smarthome" in mock_send.call_args[0][1]

    def test_handle_message_empty_text(self):
        bot = self._make_bot()
        update = {
            "message": {"from": {"id": 123, "is_bot": False}, "chat": {"id": 123}}
        }
        bot._handle_message(update)  # Should not raise

    @patch.object(TelegramAgentBot, "_get_me", return_value={"username": "test"})
    @patch.object(TelegramAgentBot, "_get_updates", return_value=[])
    def test_start_stop(self, mock_updates, mock_me):
        bot = self._make_bot()
        bot.start()
        assert bot.running is True
        assert bot._thread is not None
        assert bot._thread.name == "telegram-smarthome"
        time.sleep(0.5)
        bot.stop()
        assert bot.running is False

    def test_thread_naming(self):
        bot = self._make_bot(agent_name="devops")
        bot.running = True
        bot._thread = threading.Thread(
            target=lambda: None,
            name=f"telegram-{bot.agent_name}",
            daemon=True,
        )
        assert bot._thread.name == "telegram-devops"
        bot.running = False


# ---------------------------------------------------------------------------
# WebExAgentBot
# ---------------------------------------------------------------------------


class TestWebExAgentBot:
    """Tests for the per-agent WebEx bot."""

    def _make_bot(self, **kwargs):
        defaults = dict(
            agent_name="devops",
            token="fake_webex_token",
            api_url="https://127.0.0.1:8000",
            api_shared_key="test_key",
        )
        defaults.update(kwargs)
        return WebExAgentBot(**defaults)

    def test_init_basic(self):
        bot = self._make_bot()
        assert bot.agent_name == "devops"
        assert bot.queue_name == "webex-agent-devops"
        assert bot.running is False

    def test_init_custom_queue(self):
        bot = self._make_bot(queue_name="custom-queue")
        assert bot.queue_name == "custom-queue"

    @patch("requests.post")
    def test_send_webex_message(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"id": "msg_123"}
        )
        bot = self._make_bot()
        msg_id = bot._send_webex_message("room_1", "Hello!")
        assert msg_id == "msg_123"

    @patch("requests.post")
    def test_execute_via_api_pinned_agent(self, mock_post):
        mock_post.side_effect = [
            MagicMock(status_code=200),
            MagicMock(
                status_code=200,
                json=lambda: {"response": "DevOps says hi"},
            ),
        ]
        bot = self._make_bot()
        resp = bot._execute_via_api("deploy app", "sess_1", "person_1")
        assert resp == "DevOps says hi"
        create_call = mock_post.call_args_list[0]
        assert create_call[1]["json"]["agent"] == "devops"

    @patch.object(WebExAgentBot, "_send_webex_message")
    def test_handle_message_agent_command_blocked(self, mock_send):
        bot = self._make_bot()
        msg = {"roomId": "r1", "personId": "p1", "text": "/agent set research"}
        bot._handle_message(msg)
        mock_send.assert_called_once()
        assert "disabled" in mock_send.call_args[0][1].lower()

    @patch.object(WebExAgentBot, "_execute_via_api", return_value="Done")
    @patch.object(WebExAgentBot, "_send_webex_message")
    def test_handle_message_normal(self, mock_send, mock_api):
        bot = self._make_bot()
        bot._handle_message({"roomId": "r1", "personId": "p1", "text": "check status"})
        mock_api.assert_called_once()
        mock_send.assert_called_with("r1", "Done")

    def test_handle_message_empty(self):
        bot = self._make_bot()
        bot._handle_message({"roomId": "r1", "personId": "p1"})  # No text

    def test_handle_message_no_room(self):
        bot = self._make_bot()
        bot._handle_message({"text": "hello"})  # No roomId

    def test_thread_naming(self):
        bot = self._make_bot()
        bot.running = True
        bot._thread = threading.Thread(
            target=lambda: None,
            name=f"webex-{bot.agent_name}",
            daemon=True,
        )
        assert bot._thread.name == "webex-devops"
        bot.running = False


# ---------------------------------------------------------------------------
# AgentBotManager
# ---------------------------------------------------------------------------


class TestAgentBotManager:
    """Tests for the manager lifecycle and hot-reload."""

    def _make_agents_json(self, agents, tmpdir):
        path = os.path.join(tmpdir, "agents.json")
        with open(path, "w") as f:
            json.dump({"agents": agents}, f)
        return path

    @patch("agent_bot_manager.resolve_secret", return_value="fake_token")
    def test_load_and_start_telegram_bot(self, mock_secret):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_agents_json(
                [
                    {
                        "name": "smarthome",
                        "path": "/opt/smarthome",
                        "bots": {
                            "telegram": {
                                "token_secret": "SH_TG_TOKEN",
                                "allowed_users": ["8193231291"],
                            }
                        },
                    }
                ],
                tmpdir,
            )
            mgr = AgentBotManager(agents_json_path=path)
            with patch.object(TelegramAgentBot, "start") as mock_start:
                mgr._last_mtime = 0.0
                mgr._check_reload()
                assert "smarthome" in mgr._telegram_bots
                mock_start.assert_called_once()

    @patch("agent_bot_manager.resolve_secret", return_value="fake_token")
    def test_load_and_start_webex_bot(self, mock_secret):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_agents_json(
                [
                    {
                        "name": "devops",
                        "path": "/opt/devops",
                        "bots": {"webex": {"token_secret": "DO_WX_TOKEN"}},
                    }
                ],
                tmpdir,
            )
            mgr = AgentBotManager(agents_json_path=path)
            with patch.object(WebExAgentBot, "start") as mock_start:
                mgr._last_mtime = 0.0
                mgr._check_reload()
                assert "devops" in mgr._webex_bots
                mock_start.assert_called_once()

    @patch("agent_bot_manager.resolve_secret", return_value=None)
    def test_skip_bot_on_secret_failure(self, mock_secret):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_agents_json(
                [
                    {
                        "name": "bad",
                        "path": "/opt/bad",
                        "bots": {"telegram": {"token_secret": "MISSING_TOKEN"}},
                    }
                ],
                tmpdir,
            )
            mgr = AgentBotManager(agents_json_path=path)
            mgr._last_mtime = 0.0
            mgr._check_reload()
            assert "bad" not in mgr._telegram_bots

    def test_agents_without_bots_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_agents_json(
                [
                    {"name": "orchestrator", "path": "/opt/"},
                    {"name": "research", "path": "/opt/research"},
                ],
                tmpdir,
            )
            mgr = AgentBotManager(agents_json_path=path)
            mgr._last_mtime = 0.0
            mgr._check_reload()
            assert len(mgr._telegram_bots) == 0
            assert len(mgr._webex_bots) == 0

    @patch("agent_bot_manager.resolve_secret", return_value="fake_token")
    def test_hot_reload_adds_bot(self, mock_secret):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initial: no bots
            path = self._make_agents_json(
                [{"name": "devops", "path": "/opt/devops"}], tmpdir
            )
            mgr = AgentBotManager(agents_json_path=path)
            mgr._last_mtime = 0.0
            mgr._check_reload()
            assert len(mgr._telegram_bots) == 0

            # Update: add telegram bot
            time.sleep(0.1)  # Ensure mtime changes
            with open(path, "w") as f:
                json.dump(
                    {
                        "agents": [
                            {
                                "name": "devops",
                                "path": "/opt/devops",
                                "bots": {"telegram": {"token_secret": "DO_TG"}},
                            }
                        ]
                    },
                    f,
                )

            with patch.object(TelegramAgentBot, "start"):
                mgr._check_reload()
                assert "devops" in mgr._telegram_bots

    @patch("agent_bot_manager.resolve_secret", return_value="fake_token")
    def test_hot_reload_removes_bot(self, mock_secret):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_agents_json(
                [
                    {
                        "name": "smarthome",
                        "path": "/opt/smarthome",
                        "bots": {"telegram": {"token_secret": "SH_TG"}},
                    }
                ],
                tmpdir,
            )
            mgr = AgentBotManager(agents_json_path=path)
            with patch.object(TelegramAgentBot, "start"):
                mgr._last_mtime = 0.0
                mgr._check_reload()
                assert "smarthome" in mgr._telegram_bots

            # Remove bots
            time.sleep(0.1)
            with open(path, "w") as f:
                json.dump(
                    {"agents": [{"name": "smarthome", "path": "/opt/smarthome"}]},
                    f,
                )
            with patch.object(TelegramAgentBot, "stop"):
                mgr._check_reload()
                assert "smarthome" not in mgr._telegram_bots

    @patch("agent_bot_manager.resolve_secret", return_value="fake_token")
    def test_get_status(self, mock_secret):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_agents_json(
                [
                    {
                        "name": "smarthome",
                        "path": "/opt/smarthome",
                        "bots": {"telegram": {"token_secret": "SH_TG"}},
                    }
                ],
                tmpdir,
            )
            mgr = AgentBotManager(agents_json_path=path)
            with patch.object(TelegramAgentBot, "start"):
                mgr._last_mtime = 0.0
                mgr._check_reload()
            status = mgr.get_status()
            assert "telegram_bots" in status
            assert "smarthome" in status["telegram_bots"]

    def test_missing_agents_json(self):
        mgr = AgentBotManager(agents_json_path="/nonexistent/agents.json")
        mgr._last_mtime = 0.0
        mgr._check_reload()
        assert len(mgr._telegram_bots) == 0

    @patch("agent_bot_manager.resolve_secret", return_value="fake_token")
    def test_stop_cleans_up(self, mock_secret):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_agents_json(
                [
                    {
                        "name": "sh",
                        "path": "/opt/sh",
                        "bots": {
                            "telegram": {"token_secret": "SH_TG"},
                            "webex": {"token_secret": "SH_WX"},
                        },
                    }
                ],
                tmpdir,
            )
            mgr = AgentBotManager(agents_json_path=path)
            with (
                patch.object(TelegramAgentBot, "start"),
                patch.object(WebExAgentBot, "start"),
            ):
                mgr._last_mtime = 0.0
                mgr._check_reload()

            with (
                patch.object(TelegramAgentBot, "stop") as tg_stop,
                patch.object(WebExAgentBot, "stop") as wx_stop,
            ):
                mgr.stop()
                tg_stop.assert_called_once()
                wx_stop.assert_called_once()
            assert len(mgr._telegram_bots) == 0
            assert len(mgr._webex_bots) == 0

    def test_bot_config_no_token_secret(self):
        """Bot entry with missing token_secret is skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_agents_json(
                [
                    {
                        "name": "test",
                        "path": "/opt/test",
                        "bots": {"telegram": {}},
                    }
                ],
                tmpdir,
            )
            mgr = AgentBotManager(agents_json_path=path)
            mgr._last_mtime = 0.0
            mgr._check_reload()
            assert "test" not in mgr._telegram_bots

    @patch("agent_bot_manager.resolve_secret", return_value="fake_token")
    def test_multiple_agents_multiple_bots(self, mock_secret):
        """Multiple agents each with their own bots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_agents_json(
                [
                    {
                        "name": "smarthome",
                        "path": "/opt/smarthome",
                        "bots": {
                            "telegram": {"token_secret": "SH_TG"},
                        },
                    },
                    {
                        "name": "devops",
                        "path": "/opt/devops",
                        "bots": {
                            "telegram": {"token_secret": "DO_TG"},
                            "webex": {"token_secret": "DO_WX"},
                        },
                    },
                    {"name": "research", "path": "/opt/research"},
                ],
                tmpdir,
            )
            mgr = AgentBotManager(agents_json_path=path)
            with (
                patch.object(TelegramAgentBot, "start"),
                patch.object(WebExAgentBot, "start"),
            ):
                mgr._last_mtime = 0.0
                mgr._check_reload()
            assert len(mgr._telegram_bots) == 2
            assert len(mgr._webex_bots) == 1
            assert "smarthome" in mgr._telegram_bots
            assert "devops" in mgr._telegram_bots
            assert "devops" in mgr._webex_bots


# ---------------------------------------------------------------------------
# Regression: agents.json tolerates missing bots key
# ---------------------------------------------------------------------------


class TestIssue166AgentsJsonCompat:
    """Verify agents.json without bots key works fine (backward compat)."""

    def test_existing_agents_no_bots_key(self):
        """Existing agents without a 'bots' key must not break."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "agents.json")
            with open(path, "w") as f:
                json.dump(
                    {
                        "agents": [
                            {"name": "orchestrator", "path": "/opt/"},
                            {"name": "email-triage", "path": "/opt/email_triage"},
                            {"name": "research", "path": "/opt/research"},
                        ]
                    },
                    f,
                )
            mgr = AgentBotManager(agents_json_path=path)
            mgr._last_mtime = 0.0
            mgr._check_reload()
            assert len(mgr._telegram_bots) == 0
            assert len(mgr._webex_bots) == 0

    def test_bots_key_not_dict_ignored(self):
        """If bots is not a dict, it should be ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "agents.json")
            with open(path, "w") as f:
                json.dump(
                    {"agents": [{"name": "test", "path": "/opt/", "bots": "invalid"}]},
                    f,
                )
            mgr = AgentBotManager(agents_json_path=path)
            mgr._last_mtime = 0.0
            mgr._check_reload()
            assert len(mgr._telegram_bots) == 0


# ---------------------------------------------------------------------------
# Session ID format
# ---------------------------------------------------------------------------


class TestSessionIdFormat:
    """Verify session IDs are properly scoped per agent and user."""

    @patch("requests.post")
    def test_telegram_session_id_format(self, mock_post):
        mock_post.side_effect = [
            MagicMock(status_code=200),
            MagicMock(status_code=200, json=lambda: {"response": "ok"}),
        ]
        bot = TelegramAgentBot(
            agent_name="smarthome",
            token="t",
            api_url="http://test:8000",
            api_shared_key="k",
        )
        bot._execute_via_api("hi", "tg_smarthome_123", "123")
        create_call = mock_post.call_args_list[0]
        assert create_call[1]["json"]["session_id"] == "tg_smarthome_123"

    @patch("requests.post")
    def test_webex_session_id_format(self, mock_post):
        mock_post.side_effect = [
            MagicMock(status_code=200),
            MagicMock(status_code=200, json=lambda: {"response": "ok"}),
        ]
        bot = WebExAgentBot(
            agent_name="devops",
            token="t",
            api_url="http://test:8000",
            api_shared_key="k",
        )
        bot._execute_via_api("hi", "wx_devops_p1", "p1")
        create_call = mock_post.call_args_list[0]
        assert create_call[1]["json"]["session_id"] == "wx_devops_p1"
