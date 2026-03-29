#!/usr/bin/env python3
"""
Regression tests for background task identity propagation and notification routing.

Validates:
1. build_agent_context_prompt() uses explicit bg_identity param over shared _bg_identity
2. bg_identity=None falls back to _bg_identity, then "unknown"
3. NotificationManager routes Telegram tasks to specific user, not broadcast
4. NotificationManager routes WebEx tasks to specific user, not broadcast
5. Tasks with identity "unknown" fall back to broadcast
6. Non-Telegram/Webex channels (webui, api) do not trigger external push
"""

import os
import sys
import threading
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from notification_manager import NotificationManager


def _make_notif_mgr():
    fd1, p1 = tempfile.mkstemp(suffix=".json")
    os.close(fd1)
    fd2, p2 = tempfile.mkstemp(suffix=".json")
    os.close(fd2)
    return NotificationManager(notif_file=p1, prefs_file=p2), p1, p2


class TestBgIdentityResolution(unittest.TestCase):
    """
    Test identity resolution in build_agent_context_prompt.
    Uses real SessionManager with all external/filesystem calls patched.
    """

    def _make_mgr(self, shared_bg_identity=None):
        from agent_manager import SessionManager
        mgr = SessionManager.__new__(SessionManager)
        mgr._bg_identity = shared_bg_identity
        mgr._bg_task_mgr = None
        mgr._notification_mgr = None
        mgr._session_map_lock = threading.Lock()
        mgr._map_path = "/dev/null"
        mgr._streams = {}
        mgr.AGENTS = {
            "orchestrator": {
                "path": tempfile.mkdtemp(),
                "description": "Test orchestrator",
            }
        }
        return mgr

    def _get_prompt(self, mgr, channel="telegram", bg_identity=None):
        env_overrides = {
            "API_PORT": "8001",
            "API_SHARED_KEY": "testkey",
            "SSL_CERTFILE": "",
        }
        with (
            patch.object(mgr, "_format_repository_info", return_value=""),
            patch.object(mgr, "load_agent_skills", return_value=""),
            patch.object(mgr, "get_or_create_session_data", return_value={}),
            patch.object(mgr, "load_session_data", return_value=None),
            patch.dict(os.environ, env_overrides),
        ):
            return mgr.build_agent_context_prompt(
                agent="orchestrator",
                prompt="do something",
                n8n_session_id="sess-001",
                channel=channel,
                bg_identity=bg_identity,
            )

    def test_explicit_bg_identity_overrides_shared(self):
        mgr = self._make_mgr(shared_bg_identity="stale_identity")
        prompt = self._get_prompt(mgr, bg_identity="8193231291")
        self.assertIn("X-User-Identity: 8193231291", prompt)
        self.assertNotIn("stale_identity", prompt)

    def test_fallback_to_shared_bg_identity_when_param_absent(self):
        mgr = self._make_mgr(shared_bg_identity="fallback_user")
        prompt = self._get_prompt(mgr)
        self.assertIn("X-User-Identity: fallback_user", prompt)

    def test_unknown_fallback_when_neither_identity_set(self):
        mgr = self._make_mgr(shared_bg_identity=None)
        prompt = self._get_prompt(mgr)
        self.assertIn("X-User-Identity: unknown", prompt)

    def test_channel_propagated_to_curl_command(self):
        mgr = self._make_mgr(shared_bg_identity="someuser")
        for chan in ("telegram", "webex", "webui"):
            with self.subTest(channel=chan):
                prompt = self._get_prompt(mgr, channel=chan, bg_identity="identity123")
                self.assertIn("X-Auth-Channel: " + chan, prompt)


class TestNotificationRouting(unittest.TestCase):

    def setUp(self):
        self.mgr, self.p1, self.p2 = _make_notif_mgr()

    def tearDown(self):
        try:
            os.unlink(self.p1)
            os.unlink(self.p2)
        except OSError:
            pass

    def _create(self, channel, user_key, skip_external=False):
        return self.mgr.create_notification(
            task_id="task-001",
            description="test task",
            status="completed",
            channel=channel,
            user_key=user_key,
            skip_external=skip_external,
        )

    def test_telegram_known_identity_routes_specific(self):
        with (
            patch.object(self.mgr, "_notify_telegram") as mock_specific,
            patch.object(self.mgr, "_notify_telegram_broadcast") as mock_bcast,
        ):
            self._create(channel="telegram", user_key="telegram_8193231291")
        mock_specific.assert_called_once()
        mock_bcast.assert_not_called()

    def test_telegram_unknown_identity_broadcasts(self):
        with (
            patch.object(self.mgr, "_notify_telegram") as mock_specific,
            patch.object(self.mgr, "_notify_telegram_broadcast") as mock_bcast,
        ):
            self._create(channel="telegram", user_key="telegram_unknown")
        mock_specific.assert_not_called()
        mock_bcast.assert_called_once()

    def test_webex_known_identity_routes_specific(self):
        with (
            patch.object(self.mgr, "_notify_webex") as mock_specific,
            patch.object(self.mgr, "_notify_webex_broadcast") as mock_bcast,
        ):
            self._create(channel="webex", user_key="webex_someuser@cisco.com")
        mock_specific.assert_called_once()
        mock_bcast.assert_not_called()

    def test_webex_unknown_identity_broadcasts(self):
        with (
            patch.object(self.mgr, "_notify_webex") as mock_specific,
            patch.object(self.mgr, "_notify_webex_broadcast") as mock_bcast,
        ):
            self._create(channel="webex", user_key="webex_unknown")
        mock_specific.assert_not_called()
        mock_bcast.assert_called_once()

    def test_webui_no_external_push(self):
        with (
            patch.object(self.mgr, "_notify_telegram") as mt,
            patch.object(self.mgr, "_notify_telegram_broadcast") as mtb,
            patch.object(self.mgr, "_notify_webex") as mw,
            patch.object(self.mgr, "_notify_webex_broadcast") as mwb,
        ):
            self._create(channel="webui", user_key="webui_user123")
        mt.assert_not_called()
        mtb.assert_not_called()
        mw.assert_not_called()
        mwb.assert_not_called()

    def test_api_no_external_push(self):
        with (
            patch.object(self.mgr, "_notify_telegram") as mt,
            patch.object(self.mgr, "_notify_telegram_broadcast") as mtb,
            patch.object(self.mgr, "_notify_webex") as mw,
            patch.object(self.mgr, "_notify_webex_broadcast") as mwb,
        ):
            self._create(channel="api", user_key="shared_key_user")
        mt.assert_not_called()
        mtb.assert_not_called()
        mw.assert_not_called()
        mwb.assert_not_called()

    def test_skip_external_suppresses_all(self):
        with (
            patch.object(self.mgr, "_notify_telegram") as mt,
            patch.object(self.mgr, "_notify_telegram_broadcast") as mtb,
        ):
            self._create(
                channel="telegram",
                user_key="telegram_8193231291",
                skip_external=True,
            )
        mt.assert_not_called()
        mtb.assert_not_called()

    def test_notification_always_stored_in_file(self):
        with (
            patch.object(self.mgr, "_notify_telegram"),
            patch.object(self.mgr, "_notify_telegram_broadcast"),
        ):
            self._create(channel="telegram", user_key="telegram_8193231291")
        stored = self.mgr._load()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["task_id"], "task-001")
        self.assertEqual(stored[0]["user_key"], "telegram_8193231291")

    def test_global_mute_suppresses_external(self):
        self.mgr.set_user_pref("_global", "telegram", "off")
        with (
            patch.object(self.mgr, "_notify_telegram") as mt,
            patch.object(self.mgr, "_notify_telegram_broadcast") as mtb,
        ):
            self._create(channel="telegram", user_key="telegram_8193231291")
        mt.assert_not_called()
        mtb.assert_not_called()

    def test_telegram_compound_identity_routes_specific(self):
        """Compound Telegram identity (telegram_botid_userid) is not 'unknown'."""
        with (
            patch.object(self.mgr, "_notify_telegram") as mock_specific,
            patch.object(self.mgr, "_notify_telegram_broadcast") as mock_bcast,
        ):
            self._create(channel="telegram", user_key="telegram_12345_8193231291")
        mock_specific.assert_called_once()
        mock_bcast.assert_not_called()


class TestBackgroundTaskIdentityStorage(unittest.TestCase):

    def setUp(self):
        from agent_manager import BackgroundTaskManager
        fd, self.tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
        mgr._path = self.tmp
        mgr._lock = threading.Lock()
        self.mgr = mgr

    def tearDown(self):
        os.unlink(self.tmp)

    def test_real_telegram_identity_stored(self):
        task = self.mgr.create_task(
            task_id="t-001",
            session_id="s-001",
            user_identity="8193231291",
            channel="telegram",
            agent="fosterbot",
            runtime="copilot",
            model="claude-sonnet-4.6",
            prompt="test",
        )
        self.assertEqual(task["user_identity"], "8193231291")
        self.assertEqual(task["user_key"], "telegram_8193231291")
        self.assertNotIn("unknown", task["user_key"])

    def test_unknown_identity_stored_with_unknown_key(self):
        task = self.mgr.create_task(
            task_id="t-002",
            session_id="s-002",
            user_identity="unknown",
            channel="telegram",
            agent="fosterbot",
            runtime="copilot",
            model="claude-sonnet-4.6",
            prompt="test",
        )
        self.assertEqual(task["user_key"], "telegram_unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
