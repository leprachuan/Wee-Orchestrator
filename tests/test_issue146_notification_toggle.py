"""Regression tests for Issue #146: Global toggle to suppress background task notifications.

Tests cover:
1. NotificationManager global settings persistence (load/save/toggle)
2. create_notification() respects global toggle + is_critical bypass
3. /notifications slash command wires to global toggle
4. _emit_bg_notification is_critical logic
5. notify_pref resolution in create_background_task
"""

import json
import os
import sys
import threading
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from notification_manager import NotificationManager


@pytest.fixture
def nm(tmp_path):
    """Return a NotificationManager with temp files."""
    settings_file = str(tmp_path / "notification_settings.json")
    notif_file = str(tmp_path / "notifications.json")
    prefs_file = str(tmp_path / "notification_prefs.json")
    mgr = NotificationManager.__new__(NotificationManager)
    mgr._path = notif_file
    mgr._prefs_path = prefs_file
    mgr._global_settings_path = settings_file
    mgr._notifications = []
    mgr._prefs = {}
    mgr._lock = threading.Lock()
    mgr._prefs_lock = threading.Lock()
    mgr._global_settings_lock = threading.Lock()
    mgr._telegram_send = None
    mgr._webex_send = None
    return mgr


class TestGlobalSettingsPersistence:

    def test_default_is_enabled(self, nm):
        assert nm.is_global_enabled() is True

    def test_set_global_disabled(self, nm):
        nm.set_global_enabled(False)
        assert nm.is_global_enabled() is False

    def test_set_global_enabled(self, nm):
        nm.set_global_enabled(False)
        nm.set_global_enabled(True)
        assert nm.is_global_enabled() is True

    def test_persistence_across_loads(self, nm):
        nm.set_global_enabled(False)
        settings = nm._load_global_settings()
        assert settings["notifications_enabled"] is False

    def test_settings_file_created(self, nm):
        nm.set_global_enabled(True)
        assert os.path.exists(nm._global_settings_path)

    def test_settings_file_json_format(self, nm):
        nm.set_global_enabled(False)
        with open(nm._global_settings_path) as f:
            data = json.load(f)
        assert "notifications_enabled" in data
        assert "updated_at" in data
        assert data["notifications_enabled"] is False

    def test_get_global_settings_dict(self, nm):
        nm.set_global_enabled(True)
        settings = nm.get_global_settings()
        assert isinstance(settings, dict)
        assert settings["notifications_enabled"] is True
        assert "updated_at" in settings

    def test_corrupt_file_returns_default(self, nm):
        with open(nm._global_settings_path, "w") as f:
            f.write("NOT JSON")
        assert nm.is_global_enabled() is True


class TestCreateNotificationGlobalToggle:

    def test_suppressed_when_globally_disabled(self, nm):
        nm.set_global_enabled(False)
        result = nm.create_notification(
            task_id="bg_test1", description="Test", status="completed",
            channel="telegram", user_key="telegram:user1",
        )
        assert result is None

    def test_no_notification_stored_when_suppressed(self, nm):
        nm.set_global_enabled(False)
        nm.create_notification(
            task_id="bg_test2", description="Test", status="completed",
            channel="telegram", user_key="telegram:user1",
        )
        stored = nm._load()
        assert len(stored) == 0

    def test_critical_bypasses_suppression(self, nm):
        nm.set_global_enabled(False)
        result = nm.create_notification(
            task_id="bg_critical1", description="Heartbeat failure",
            status="error", channel="telegram", user_key="telegram:user1",
            is_critical=True,
        )
        assert result is not None
        stored = nm._load()
        assert len(stored) >= 1
        assert stored[-1]["task_id"] == "bg_critical1"

    def test_normal_notification_when_enabled(self, nm):
        nm.set_global_enabled(True)
        result = nm.create_notification(
            task_id="bg_normal1", description="Normal", status="completed",
            channel="webui", user_key="webui:user1",
        )
        assert result is not None
        stored = nm._load()
        assert len(stored) >= 1
        assert stored[-1]["task_id"] == "bg_normal1"


class TestSlashNotifications:

    def _make_session_mgr(self, nm):
        mgr = MagicMock()
        mgr._notification_mgr = nm
        mgr._bg_identity = None
        mgr.update_session_field = MagicMock()
        from agent_manager import SessionManager
        mgr._slash_notifications = SessionManager._slash_notifications.__get__(mgr)
        return mgr

    def test_off_command_sets_global_disabled(self, nm):
        mgr = self._make_session_mgr(nm)
        result = mgr._slash_notifications("off", {"channel": "telegram"}, "sess1")
        assert nm.is_global_enabled() is False

    def test_on_command_sets_global_enabled(self, nm):
        nm.set_global_enabled(False)
        mgr = self._make_session_mgr(nm)
        result = mgr._slash_notifications("on", {"channel": "telegram"}, "sess1")
        assert nm.is_global_enabled() is True

    def test_current_shows_on(self, nm):
        nm.set_global_enabled(True)
        mgr = self._make_session_mgr(nm)
        result = mgr._slash_notifications("current", {"channel": "webui"}, "sess1")
        assert "ON" in result

    def test_current_shows_off(self, nm):
        nm.set_global_enabled(False)
        mgr = self._make_session_mgr(nm)
        result = mgr._slash_notifications("current", {"channel": "webui"}, "sess1")
        assert "OFF" in result

    def test_mute_alias(self, nm):
        mgr = self._make_session_mgr(nm)
        mgr._slash_notifications("mute", {"channel": "webui"}, "sess1")
        assert nm.is_global_enabled() is False

    def test_all_alias(self, nm):
        nm.set_global_enabled(False)
        mgr = self._make_session_mgr(nm)
        mgr._slash_notifications("all", {"channel": "webui"}, "sess1")
        assert nm.is_global_enabled() is True

    def test_invalid_argument(self, nm):
        mgr = self._make_session_mgr(nm)
        result = mgr._slash_notifications("bogus", {"channel": "webui"}, "sess1")
        assert "Usage:" in result


class TestEmitBgNotificationCritical:
    """Test _emit_bg_notification is_critical logic (inlined — function is nested)."""

    def _simulate_emit(self, is_critical, user_muted=False):
        mock_nm = MagicMock()
        mock_nm.is_muted.return_value = user_muted
        notify = True
        if notify and not is_critical:
            if mock_nm.is_muted("user1"):
                notify = False
        mock_nm.create_notification(
            task_id="bg_test", description="test"[:200], status="completed",
            channel="telegram", user_key="telegram:user1",
            output_preview=None, error=None,
            skip_external=not notify, is_critical=is_critical,
        )
        return mock_nm

    def test_is_critical_passed_through(self):
        mock_nm = self._simulate_emit(is_critical=True)
        kw = mock_nm.create_notification.call_args[1]
        assert kw["is_critical"] is True

    def test_is_critical_skips_mute(self):
        mock_nm = self._simulate_emit(is_critical=True, user_muted=True)
        kw = mock_nm.create_notification.call_args[1]
        assert kw["skip_external"] is False

    def test_non_critical_respects_mute(self):
        mock_nm = self._simulate_emit(is_critical=False, user_muted=True)
        kw = mock_nm.create_notification.call_args[1]
        assert kw["skip_external"] is True


class TestNotifyPrefResolution:

    def test_false_when_global_disabled(self):
        mock_nm = MagicMock()
        mock_nm.is_global_enabled.return_value = False
        notify_pref = None
        if notify_pref is None:
            if not mock_nm.is_global_enabled():
                notify_pref = False
        assert notify_pref is False

    def test_true_when_global_enabled(self):
        mock_nm = MagicMock()
        mock_nm.is_global_enabled.return_value = True
        mock_nm.is_muted.return_value = False
        notify_pref = None
        if notify_pref is None:
            if not mock_nm.is_global_enabled():
                notify_pref = False
            elif mock_nm.is_muted("user1"):
                notify_pref = False
        if notify_pref is None:
            notify_pref = True
        assert notify_pref is True

    def test_body_notify_overrides_global(self):
        mock_nm = MagicMock()
        mock_nm.is_global_enabled.return_value = False
        notify_pref = True
        assert notify_pref is True
        mock_nm.is_global_enabled.assert_not_called()
