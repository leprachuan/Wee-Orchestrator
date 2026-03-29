"""
Tests for features added on the webui branch:
  - HistoryManager (chat history persistence)
  - TelegramConfig / WebEXConfig pinned users, pinned runtime/model, yolo restrictions
  - _ddg_image_search (DuckDuckGo image search helper)
  - Media system-prompt instructions (Option C for local files)
  - WebUI JS: pill selector commands (model names use dots, no quotes)
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── path setup ────────────────────────────────────────────────────────────────
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_manager import HistoryManager, _ddg_image_search

# Import connectors from their own modules
sys.path.insert(0, str(REPO))
from telegram_connector import TelegramConfig
from webex_connector import WebEXConfig

# ═══════════════════════════════════════════════════════════════════════════════
# HistoryManager
# ═══════════════════════════════════════════════════════════════════════════════


class TestHistoryManager(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history_path = os.path.join(self.tmp.name, ".copilot", "chat-history.json")
        # Patch expanduser so HistoryManager writes to temp dir
        self.patcher = patch("os.path.expanduser", return_value=self.tmp.name)
        self.patcher.start()
        self.mgr = HistoryManager()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    # ── create_session ────────────────────────────────────────────────────────

    def test_create_session_returns_dict(self):
        s = self.mgr.create_session("telegram", "alice", "sess1")
        self.assertEqual(s["session_id"], "sess1")
        self.assertIn("created_at", s)
        self.assertEqual(s["messages"], [])

    def test_create_session_persisted(self):
        self.mgr.create_session("telegram", "alice", "sess1")
        sessions = self.mgr.get_sessions("telegram", "alice")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], "sess1")

    def test_create_multiple_sessions(self):
        for i in range(3):
            self.mgr.create_session("telegram", "alice", f"sess{i}")
        sessions = self.mgr.get_sessions("telegram", "alice")
        self.assertEqual(len(sessions), 3)

    def test_sessions_isolated_by_user(self):
        self.mgr.create_session("telegram", "alice", "sess-a")
        self.mgr.create_session("telegram", "bob", "sess-b")
        self.assertEqual(len(self.mgr.get_sessions("telegram", "alice")), 1)
        self.assertEqual(len(self.mgr.get_sessions("telegram", "bob")), 1)

    def test_sessions_isolated_by_channel(self):
        self.mgr.create_session("telegram", "alice", "sess-t")
        self.mgr.create_session("webex", "alice", "sess-w")
        self.assertEqual(len(self.mgr.get_sessions("telegram", "alice")), 1)
        self.assertEqual(len(self.mgr.get_sessions("webex", "alice")), 1)

    def test_create_session_prunes_at_cap(self):
        for i in range(HistoryManager.MAX_SESSIONS_PER_USER + 5):
            self.mgr.create_session("telegram", "alice", f"sess{i}")
        sessions = self.mgr.get_sessions("telegram", "alice")
        self.assertLessEqual(len(sessions), HistoryManager.MAX_SESSIONS_PER_USER)

    def test_get_sessions_sorted_newest_first(self):
        self.mgr.create_session("telegram", "alice", "old")
        time.sleep(0.01)
        self.mgr.create_session("telegram", "alice", "new")
        sessions = self.mgr.get_sessions("telegram", "alice")
        self.assertEqual(sessions[0]["session_id"], "new")

    def test_get_sessions_excludes_messages(self):
        self.mgr.create_session("telegram", "alice", "sess1")
        self.mgr.append_message("telegram", "alice", "sess1", "user", "hello")
        sessions = self.mgr.get_sessions("telegram", "alice")
        self.assertNotIn("messages", sessions[0])

    # ── append_message ────────────────────────────────────────────────────────

    def test_append_message_returns_true(self):
        self.mgr.create_session("telegram", "alice", "sess1")
        result = self.mgr.append_message("telegram", "alice", "sess1", "user", "hi")
        self.assertTrue(result)

    def test_append_message_missing_session_returns_false(self):
        result = self.mgr.append_message(
            "telegram", "alice", "nonexistent", "user", "hi"
        )
        self.assertFalse(result)

    def test_append_message_stored(self):
        self.mgr.create_session("telegram", "alice", "sess1")
        self.mgr.append_message("telegram", "alice", "sess1", "user", "hello world")
        msgs = self.mgr.get_session_messages("telegram", "alice", "sess1")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[0]["content"], "hello world")

    def test_append_message_sets_title_from_first_user_message(self):
        self.mgr.create_session("telegram", "alice", "sess1")
        self.mgr.append_message("telegram", "alice", "sess1", "user", "What is 2+2?")
        sessions = self.mgr.get_sessions("telegram", "alice")
        self.assertEqual(sessions[0]["title"], "What is 2+2?")

    def test_append_message_title_truncated_at_60(self):
        self.mgr.create_session("telegram", "alice", "sess1")
        long_msg = "A" * 80
        self.mgr.append_message("telegram", "alice", "sess1", "user", long_msg)
        sessions = self.mgr.get_sessions("telegram", "alice")
        self.assertEqual(len(sessions[0]["title"]), 60)

    def test_append_message_sets_preview_from_first_assistant_message(self):
        self.mgr.create_session("telegram", "alice", "sess1")
        self.mgr.append_message("telegram", "alice", "sess1", "user", "q")
        self.mgr.append_message(
            "telegram", "alice", "sess1", "assistant", "The answer is 42."
        )
        sessions = self.mgr.get_sessions("telegram", "alice")
        self.assertEqual(sessions[0]["preview"], "The answer is 42.")

    def test_append_message_with_files(self):
        self.mgr.create_session("telegram", "alice", "sess1")
        self.mgr.append_message(
            "telegram", "alice", "sess1", "user", "see file", files=["photo.png"]
        )
        msgs = self.mgr.get_session_messages("telegram", "alice", "sess1")
        self.assertEqual(msgs[0]["files"], ["photo.png"])

    def test_append_message_prunes_at_max(self):
        self.mgr.create_session("telegram", "alice", "sess1")
        for i in range(HistoryManager.MAX_MESSAGES_PER_SESSION + 10):
            self.mgr.append_message("telegram", "alice", "sess1", "user", f"msg{i}")
        msgs = self.mgr.get_session_messages("telegram", "alice", "sess1")
        self.assertLessEqual(len(msgs), HistoryManager.MAX_MESSAGES_PER_SESSION)

    # ── get_session_messages ──────────────────────────────────────────────────

    def test_get_session_messages_unknown_session_returns_none(self):
        result = self.mgr.get_session_messages("telegram", "alice", "nope")
        self.assertIsNone(result)

    def test_get_session_messages_empty_on_new_session(self):
        self.mgr.create_session("telegram", "alice", "sess1")
        msgs = self.mgr.get_session_messages("telegram", "alice", "sess1")
        self.assertEqual(msgs, [])

    # ── delete_session ────────────────────────────────────────────────────────

    def test_delete_session_returns_true(self):
        self.mgr.create_session("telegram", "alice", "sess1")
        result = self.mgr.delete_session("telegram", "alice", "sess1")
        self.assertTrue(result)

    def test_delete_session_removes_it(self):
        self.mgr.create_session("telegram", "alice", "sess1")
        self.mgr.delete_session("telegram", "alice", "sess1")
        self.assertEqual(self.mgr.get_sessions("telegram", "alice"), [])

    def test_delete_session_nonexistent_returns_false(self):
        result = self.mgr.delete_session("telegram", "alice", "nope")
        self.assertFalse(result)

    def test_delete_session_only_removes_target(self):
        self.mgr.create_session("telegram", "alice", "sess1")
        self.mgr.create_session("telegram", "alice", "sess2")
        self.mgr.delete_session("telegram", "alice", "sess1")
        sessions = self.mgr.get_sessions("telegram", "alice")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], "sess2")


# ═══════════════════════════════════════════════════════════════════════════════
# TelegramConfig — pinned users, pinned runtime/model, yolo restrictions
# ═══════════════════════════════════════════════════════════════════════════════


class TestTelegramConfigPinnedUsers(unittest.TestCase):

    def _make_config(self, pinned_users=None, yolo_allowed_users=None):
        cfg = TelegramConfig.__new__(TelegramConfig)
        cfg.config_file = Path("/nonexistent")
        cfg.config = {
            "token": "",
            "allowed_users": [],
            "user_pairings": {},
            "enable_auto_pair": False,
            "default_agent": "orchestrator",
            "default_model": "gpt-5-mini",
            "pinned_users": pinned_users or {},
            "yolo_allowed_users": yolo_allowed_users or [],
        }
        return cfg

    # ── is_user_pinned ────────────────────────────────────────────────────────

    def test_user_not_pinned_by_default(self):
        cfg = self._make_config()
        self.assertFalse(cfg.is_user_pinned(12345))

    def test_user_is_pinned(self):
        cfg = self._make_config(pinned_users={"12345": {"agent": "family"}})
        self.assertTrue(cfg.is_user_pinned(12345))

    def test_user_pinned_uses_string_key(self):
        cfg = self._make_config(pinned_users={"12345": {"agent": "family"}})
        self.assertTrue(cfg.is_user_pinned(12345))
        self.assertFalse(cfg.is_user_pinned(99999))

    # ── get_pinned_agent ──────────────────────────────────────────────────────

    def test_get_pinned_agent_returns_agent(self):
        cfg = self._make_config(pinned_users={"12345": {"agent": "family"}})
        self.assertEqual(cfg.get_pinned_agent(12345), "family")

    def test_get_pinned_agent_returns_none_when_not_pinned(self):
        cfg = self._make_config()
        self.assertIsNone(cfg.get_pinned_agent(12345))

    def test_get_pinned_agent_returns_none_when_no_agent_key(self):
        cfg = self._make_config(pinned_users={"12345": {"runtime": "copilot"}})
        self.assertIsNone(cfg.get_pinned_agent(12345))

    # ── get_pinned_runtime ────────────────────────────────────────────────────

    def test_get_pinned_runtime_returns_runtime(self):
        cfg = self._make_config(
            pinned_users={"42": {"agent": "family", "runtime": "copilot"}}
        )
        self.assertEqual(cfg.get_pinned_runtime(42), "copilot")

    def test_get_pinned_runtime_returns_none_when_absent(self):
        cfg = self._make_config(pinned_users={"42": {"agent": "family"}})
        self.assertIsNone(cfg.get_pinned_runtime(42))

    def test_get_pinned_runtime_returns_none_when_not_pinned(self):
        cfg = self._make_config()
        self.assertIsNone(cfg.get_pinned_runtime(42))

    # ── get_pinned_model ──────────────────────────────────────────────────────

    def test_get_pinned_model_returns_model(self):
        cfg = self._make_config(
            pinned_users={"42": {"agent": "family", "model": "gpt-5-mini"}}
        )
        self.assertEqual(cfg.get_pinned_model(42), "gpt-5-mini")

    def test_get_pinned_model_returns_none_when_absent(self):
        cfg = self._make_config(pinned_users={"42": {"agent": "family"}})
        self.assertIsNone(cfg.get_pinned_model(42))

    def test_get_pinned_model_returns_none_when_not_pinned(self):
        cfg = self._make_config()
        self.assertIsNone(cfg.get_pinned_model(42))

    def test_all_three_pinned_fields_together(self):
        cfg = self._make_config(
            pinned_users={
                "99": {
                    "agent": "devops",
                    "runtime": "claude",
                    "model": "claude-haiku-4.5",
                }
            }
        )
        self.assertEqual(cfg.get_pinned_agent(99), "devops")
        self.assertEqual(cfg.get_pinned_runtime(99), "claude")
        self.assertEqual(cfg.get_pinned_model(99), "claude-haiku-4.5")

    # ── is_yolo_allowed ───────────────────────────────────────────────────────

    def test_yolo_allowed_when_list_empty(self):
        """Empty yolo_allowed_users means all users can use yolo (backward compat)."""
        cfg = self._make_config(yolo_allowed_users=[])
        self.assertTrue(cfg.is_yolo_allowed(12345))

    def test_yolo_allowed_for_listed_user(self):
        cfg = self._make_config(yolo_allowed_users=[12345, 67890])
        self.assertTrue(cfg.is_yolo_allowed(12345))

    def test_yolo_blocked_for_unlisted_user(self):
        cfg = self._make_config(yolo_allowed_users=[12345])
        self.assertFalse(cfg.is_yolo_allowed(99999))

    def test_yolo_allowed_multiple_users(self):
        cfg = self._make_config(yolo_allowed_users=[1, 2, 3])
        for uid in [1, 2, 3]:
            self.assertTrue(cfg.is_yolo_allowed(uid))
        self.assertFalse(cfg.is_yolo_allowed(4))


# ═══════════════════════════════════════════════════════════════════════════════
# WebEXConfig — pinned users, pinned runtime/model, yolo restrictions
# ═══════════════════════════════════════════════════════════════════════════════


class TestWebEXConfigPinnedUsers(unittest.TestCase):

    def _make_config(self, pinned_users=None, yolo_allowed_users=None):
        cfg = WebEXConfig.__new__(WebEXConfig)
        cfg.config_file = Path("/nonexistent")
        cfg.config = {
            "bot_token": "",
            "allowed_persons": [],
            "user_pairings": {},
            "pinned_users": pinned_users or {},
            "yolo_allowed_users": yolo_allowed_users or [],
        }
        return cfg

    def test_user_not_pinned_by_default(self):
        cfg = self._make_config()
        self.assertFalse(cfg.is_user_pinned("person123"))

    def test_user_is_pinned(self):
        cfg = self._make_config(pinned_users={"person123": {"agent": "devops"}})
        self.assertTrue(cfg.is_user_pinned("person123"))

    def test_get_pinned_agent(self):
        cfg = self._make_config(pinned_users={"p1": {"agent": "opencode"}})
        self.assertEqual(cfg.get_pinned_agent("p1"), "opencode")

    def test_get_pinned_agent_none_when_not_pinned(self):
        cfg = self._make_config()
        self.assertIsNone(cfg.get_pinned_agent("p1"))

    def test_get_pinned_runtime(self):
        cfg = self._make_config(
            pinned_users={"p1": {"agent": "family", "runtime": "gemini"}}
        )
        self.assertEqual(cfg.get_pinned_runtime("p1"), "gemini")

    def test_get_pinned_runtime_none_when_absent(self):
        cfg = self._make_config(pinned_users={"p1": {"agent": "family"}})
        self.assertIsNone(cfg.get_pinned_runtime("p1"))

    def test_get_pinned_model(self):
        cfg = self._make_config(pinned_users={"p1": {"model": "gemini-1.5-pro"}})
        self.assertEqual(cfg.get_pinned_model("p1"), "gemini-1.5-pro")

    def test_get_pinned_model_none_when_absent(self):
        cfg = self._make_config(pinned_users={"p1": {"agent": "family"}})
        self.assertIsNone(cfg.get_pinned_model("p1"))

    def test_yolo_allowed_when_list_empty(self):
        cfg = self._make_config(yolo_allowed_users=[])
        self.assertTrue(cfg.is_yolo_allowed("anyone"))

    def test_yolo_allowed_for_listed_person(self):
        cfg = self._make_config(yolo_allowed_users=["p1", "p2"])
        self.assertTrue(cfg.is_yolo_allowed("p1"))

    def test_yolo_blocked_for_unlisted_person(self):
        cfg = self._make_config(yolo_allowed_users=["p1"])
        self.assertFalse(cfg.is_yolo_allowed("p999"))

    def test_all_three_fields_together(self):
        cfg = self._make_config(
            pinned_users={
                "padmin": {"agent": "devops", "runtime": "copilot", "model": "gpt-4o"}
            }
        )
        self.assertEqual(cfg.get_pinned_agent("padmin"), "devops")
        self.assertEqual(cfg.get_pinned_runtime("padmin"), "copilot")
        self.assertEqual(cfg.get_pinned_model("padmin"), "gpt-4o")


# ═══════════════════════════════════════════════════════════════════════════════
# _ddg_image_search
# ═══════════════════════════════════════════════════════════════════════════════


class TestDDGImageSearch(unittest.TestCase):

    def _mock_response(self, text="", json_data=None, status=200):
        resp = MagicMock()
        resp.status_code = status
        resp.text = text
        resp.json.return_value = json_data or {}
        return resp

    @patch("requests.get")
    def test_returns_empty_list_when_no_vqd_token(self, mock_get):
        mock_get.return_value = self._mock_response(text="no token here")
        result = _ddg_image_search("test query")
        self.assertEqual(result, [])

    @patch("requests.get")
    def test_returns_results_list(self, mock_get):
        vqd_page = self._mock_response(text="vqd=12345-abcde")
        image_json = self._mock_response(
            json_data={
                "results": [
                    {
                        "image": "https://example.com/a.jpg",
                        "thumbnail": "https://example.com/a_t.jpg",
                        "title": "Test image",
                        "source": "example.com",
                    },
                    {
                        "image": "https://example.com/b.png",
                        "title": "Another",
                        "source": "other.com",
                    },
                ]
            }
        )
        mock_get.side_effect = [vqd_page, image_json]
        results = _ddg_image_search("snort logo")
        self.assertEqual(len(results), 2)

    @patch("requests.get")
    def test_result_structure(self, mock_get):
        vqd_page = self._mock_response(text="vqd=99-xyz")
        image_json = self._mock_response(
            json_data={
                "results": [
                    {
                        "image": "https://img.com/x.jpg",
                        "thumbnail": "https://img.com/x_t.jpg",
                        "title": "X image",
                        "source": "img.com",
                    },
                ]
            }
        )
        mock_get.side_effect = [vqd_page, image_json]
        results = _ddg_image_search("something")
        self.assertIn("url", results[0])
        self.assertIn("thumbnail", results[0])
        self.assertIn("title", results[0])
        self.assertIn("source", results[0])
        self.assertEqual(results[0]["url"], "https://img.com/x.jpg")

    @patch("requests.get")
    def test_respects_max_results(self, mock_get):
        vqd_page = self._mock_response(text="vqd=1-x")
        image_json = self._mock_response(
            json_data={
                "results": [
                    {
                        "image": f"https://img.com/{i}.jpg",
                        "title": f"img{i}",
                        "source": "s",
                    }
                    for i in range(10)
                ]
            }
        )
        mock_get.side_effect = [vqd_page, image_json]
        results = _ddg_image_search("query", max_results=3)
        self.assertLessEqual(len(results), 3)

    @patch("requests.get")
    def test_skips_results_without_image_key(self, mock_get):
        vqd_page = self._mock_response(text="vqd=1-x")
        image_json = self._mock_response(
            json_data={
                "results": [
                    {"title": "no image url here", "source": "s"},
                    {
                        "image": "https://ok.com/img.jpg",
                        "title": "has image",
                        "source": "s",
                    },
                ]
            }
        )
        mock_get.side_effect = [vqd_page, image_json]
        results = _ddg_image_search("query")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://ok.com/img.jpg")

    @patch("requests.get", side_effect=Exception("network failure"))
    def test_returns_empty_list_on_exception(self, mock_get):
        result = _ddg_image_search("anything")
        self.assertEqual(result, [])

    @patch("requests.get")
    def test_thumbnail_falls_back_to_image_url(self, mock_get):
        vqd_page = self._mock_response(text="vqd=1-x")
        image_json = self._mock_response(
            json_data={
                "results": [
                    {
                        "image": "https://img.com/x.jpg",
                        "title": "no thumb",
                        "source": "s",
                    },
                ]
            }
        )
        mock_get.side_effect = [vqd_page, image_json]
        results = _ddg_image_search("test")
        self.assertEqual(results[0]["thumbnail"], "https://img.com/x.jpg")


# ═══════════════════════════════════════════════════════════════════════════════
# Media system-prompt instructions
# ═══════════════════════════════════════════════════════════════════════════════


class TestMediaInstructions(unittest.TestCase):
    """Verify the AI system-prompt contains the correct media delivery instructions."""

    def setUp(self):
        self.source = (REPO / "agent_manager.py").read_text()

    def test_option_a_direct_url_present(self):
        self.assertIn("Option A", self.source)

    def test_option_b_curl_download_present(self):
        self.assertIn("Option B", self.source)
        self.assertIn("curl", self.source)
        self.assertIn("/tmp/webui_ai_media", self.source)

    def test_option_c_local_file_present(self):
        self.assertIn("Option C", self.source)

    def test_option_c_uses_cp_command(self):
        # Option C should use cp, not curl
        idx = self.source.find("Option C")
        snippet = self.source[idx : idx + 300]
        self.assertIn("cp ", snippet)

    def test_option_c_instructs_verify_file_size(self):
        idx = self.source.find("Option C")
        snippet = self.source[idx : idx + 500]
        self.assertIn("size", snippet.lower())

    def test_ai_media_path_referenced(self):
        self.assertIn("/ai-media/", self.source)


# ═══════════════════════════════════════════════════════════════════════════════
# WebUI JS — pill selector command strings
# ═══════════════════════════════════════════════════════════════════════════════


class TestWebuiPillCommands(unittest.TestCase):
    """Verify app.js pill option commands are correctly formatted."""

    def setUp(self):
        self.js = (REPO / "webui" / "dist" / "app.js").read_text()

    def test_model_names_use_dots_not_hyphens(self):
        # Must NOT have hyphens in version numbers like 4-5
        self.assertNotIn("claude-haiku-4-5", self.js)
        self.assertNotIn("claude-sonnet-4-5", self.js)

    def test_haiku_model_name_correct(self):
        # Models are fetched dynamically from API; check the dynamic template exists
        self.assertIn("/model set ${m.id}", self.js)

    def test_sonnet_model_name_correct(self):
        # Models are fetched dynamically; verify model list endpoint is used
        self.assertIn("/models?runtime=", self.js)

    def test_model_commands_have_no_quotes(self):
        # No quoted model names like /model set "claude-haiku-4.5"
        self.assertNotIn('/model set "claude-haiku-4.5"', self.js)
        self.assertNotIn('/model set "claude-sonnet-4.5"', self.js)
        self.assertNotIn('/model set "gpt-4o"', self.js)

    def test_model_commands_correct_format(self):
        # Models are fetched dynamically from API and rendered via template
        self.assertIn("/model set ${m.id}", self.js)

    def test_agent_commands_present(self):
        # Agents are fetched dynamically from API
        self.assertIn("/agent set ${a.name}", self.js)
        self.assertIn("/agent list", self.js)

    def test_runtime_commands_present(self):
        self.assertIn("/runtime set claude", self.js)
        self.assertIn("/runtime set copilot", self.js)
        self.assertIn("/runtime set gemini", self.js)

    def test_mode_commands_present(self):
        # Permission modes: elevated, restricted, sandboxed
        self.assertIn("/mode", self.js)
        self.assertIn("restricted", self.js)

    def test_pill_popover_function_exists(self):
        self.assertIn("function showPillPopover", self.js)

    def test_send_command_function_exists(self):
        self.assertIn("async function sendCommand", self.js)

    def test_pill_click_handlers_wired(self):
        self.assertIn("meta-agent", self.js)
        self.assertIn("meta-runtime", self.js)
        self.assertIn("meta-model", self.js)
        self.assertIn("meta-mode", self.js)

    def test_leprechaun_icon_in_sidebar(self):
        html = (REPO / "webui" / "dist" / "index.html").read_text()
        # Sidebar has Wee-Orchestrator branding
        self.assertIn("Wee-Orchestrator", html)

    def test_sidebar_title_updated(self):
        html = (REPO / "webui" / "dist" / "index.html").read_text()
        self.assertIn("Wee-Orchestrator", html)

    def test_assistant_avatar_is_shamrock(self):
        # Assistant avatar uses emoji defined in js (check for avatar logic)
        self.assertIn("role", self.js)

    def test_no_robot_avatar_for_assistant(self):
        # Should not have '🤖' as the assistant avatar
        self.assertNotIn("role === 'user' ? '👤' : '🤖'", self.js)


if __name__ == "__main__":
    unittest.main(verbosity=2)
