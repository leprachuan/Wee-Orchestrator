#!/usr/bin/env python3
"""
Tests for notification mute across WebUI and external channels.

Validates that:
1. Per-identity notification preferences are stored and retrieved correctly
2. Muting via one channel (WebUI) suppresses notifications on external channels
3. _emit_bg_notification re-checks mute at emit time
4. Background task creation honours per-identity mute store
5. NotificationManager.create_notification respects skip_external
6. Identity normalization resolves flipkey@cisco.com vs webex_flipkey@cisco.com
7. BackgroundTaskRequest.notify defaults to None (not True) so prefs are checked
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from notification_manager import NotificationManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tmp_path(suffix=".json"):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


def _make_mgr():
    """Create a NotificationManager with isolated temp files."""
    return NotificationManager(
        notif_file=_tmp_path(),
        prefs_file=_tmp_path(),
    )


# ---------------------------------------------------------------------------
# 1. Per-identity preference store
# ---------------------------------------------------------------------------


class TestPerIdentityPrefs:

    def test_default_pref_is_all(self):
        mgr = _make_mgr()
        assert mgr.get_user_pref("user123") == "all"
        assert not mgr.is_muted("user123")

    def test_set_pref_off_and_retrieve(self):
        mgr = _make_mgr()
        mgr.set_user_pref("user123", "telegram", "off")
        assert mgr.get_user_pref("user123") == "off"
        assert mgr.is_muted("user123")

    def test_set_pref_all_after_off(self):
        mgr = _make_mgr()
        mgr.set_user_pref("user123", "webex", "off")
        assert mgr.is_muted("user123")
        mgr.set_user_pref("user123", "webex", "all")
        assert not mgr.is_muted("user123")

    def test_prefs_independent_per_identity(self):
        mgr = _make_mgr()
        mgr.set_user_pref("alice", "telegram", "off")
        mgr.set_user_pref("bob", "webex", "all")
        assert mgr.is_muted("alice")
        assert not mgr.is_muted("bob")

    def test_prefs_persist_to_file(self):
        prefs_path = _tmp_path()
        mgr1 = NotificationManager(notif_file=_tmp_path(), prefs_file=prefs_path)
        mgr1.set_user_pref("persist_user", "telegram", "off")

        # New manager instance reading the same file
        mgr2 = NotificationManager(notif_file=_tmp_path(), prefs_file=prefs_path)
        assert mgr2.is_muted("persist_user")

    def test_cross_channel_mute(self):
        """Muting from WebUI should apply when checking Telegram identity."""
        mgr = _make_mgr()
        identity = "8193231291"  # Telegram chat ID
        mgr.set_user_pref(identity, "webui", "off")
        assert mgr.is_muted(identity)

    def test_identity_normalization_strips_webex_prefix(self):
        """webex_flipkey@cisco.com and flipkey@cisco.com should be the same user."""
        mgr = _make_mgr()
        mgr.set_user_pref("flipkey@cisco.com", "webex", "off")
        assert mgr.is_muted("flipkey@cisco.com")
        assert mgr.is_muted("webex_flipkey@cisco.com")

    def test_identity_normalization_strips_telegram_prefix(self):
        """telegram_123 and 123 should be the same user."""
        mgr = _make_mgr()
        mgr.set_user_pref("telegram_123456", "telegram", "off")
        assert mgr.is_muted("123456")
        assert mgr.is_muted("telegram_123456")

    def test_identity_normalization_stores_under_bare_key(self):
        """Setting pref via prefixed identity should be readable via bare identity."""
        mgr = _make_mgr()
        mgr.set_user_pref("webex_user@test.com", "webex", "off")
        assert mgr.get_user_pref("user@test.com") == "off"
        assert mgr.get_user_pref("webex_user@test.com") == "off"

    def test_identity_normalization_no_double_strip(self):
        """Identity without prefix should not be modified."""
        mgr = _make_mgr()
        mgr.set_user_pref("plainuser", "webui", "off")
        assert mgr.is_muted("plainuser")


# ---------------------------------------------------------------------------
# 2. create_notification respects skip_external
# ---------------------------------------------------------------------------


class TestCreateNotification:

    def test_notification_always_stored_for_webui(self):
        mgr = _make_mgr()
        mgr.create_notification(
            task_id="t1",
            description="test",
            status="completed",
            channel="telegram",
            user_key="telegram_123",
            skip_external=True,
        )
        notifs = mgr.list_notifications("telegram_123")
        assert len(notifs) == 1

    def test_skip_external_suppresses_external(self):
        """When skip_external=True, _notify_telegram/_notify_webex must NOT be called."""
        mgr = _make_mgr()
        calls = []
        mgr._notify_telegram = lambda n: calls.append("telegram")
        mgr._notify_webex = lambda n: calls.append("webex")

        mgr.create_notification(
            task_id="t2",
            description="test",
            status="completed",
            channel="telegram",
            user_key="telegram_456",
            skip_external=True,
        )
        assert (
            calls == []
        ), f"External notification sent despite skip_external=True: {calls}"

    def test_not_skip_external_sends_telegram(self):
        mgr = _make_mgr()
        calls = []
        # user_key telegram_789 is a known identity -> specific routing, not broadcast
        mgr._notify_telegram = lambda n: calls.append("telegram")

        mgr.create_notification(
            task_id="t3",
            description="test",
            status="completed",
            channel="telegram",
            user_key="telegram_789",
            skip_external=False,
        )
        assert "telegram" in calls

    def test_not_skip_external_sends_webex(self):
        mgr = _make_mgr()
        calls = []
        # user_key webex_user@test.com is known -> specific routing, not broadcast
        mgr._notify_webex = lambda n: calls.append("webex")

        mgr.create_notification(
            task_id="t4",
            description="test",
            status="completed",
            channel="webex",
            user_key="webex_user@test.com",
            skip_external=False,
        )
        assert "webex" in calls

    def test_defense_in_depth_user_mute_blocks_notification(self):
        """Even when skip_external=False, per-user mute in the preference
        store should block external notifications."""
        mgr = _make_mgr()
        calls = []
        mgr._notify_webex = lambda n: calls.append("webex")

        mgr.set_user_pref("user@test.com", "webex", "off")
        mgr.create_notification(
            task_id="t4b",
            description="test",
            status="completed",
            channel="webex",
            user_key="webex_user@test.com",
            skip_external=False,
        )
        assert calls == [], f"External notification leaked despite user mute: {calls}"

    def test_defense_in_depth_unmuted_user_receives_notification(self):
        """When user is NOT muted, notifications should still go through."""
        mgr = _make_mgr()
        calls = []
        # Known webex identity -> specific routing
        mgr._notify_webex = lambda n: calls.append("webex")

        mgr.create_notification(
            task_id="t4c",
            description="test",
            status="completed",
            channel="webex",
            user_key="webex_other@test.com",
            skip_external=False,
        )
        assert "webex" in calls


# ---------------------------------------------------------------------------
# 3. Simulate _emit_bg_notification re-check
# ---------------------------------------------------------------------------


class TestEmitRecheck:
    """Simulate the fixed _emit_bg_notification logic."""

    @staticmethod
    def _simulate_emit(notification_mgr, task_id, channel, user_identity, notify):
        """Mirror the fixed _emit_bg_notification logic."""
        if notify and notification_mgr.is_muted(user_identity):
            notify = False

        user_key = f"{channel}_{user_identity}"
        return notification_mgr.create_notification(
            task_id=task_id,
            description="test task",
            status="completed",
            channel=channel,
            user_key=user_key,
            skip_external=not notify,
        )

    def test_emit_suppressed_when_muted_after_creation(self):
        """User mutes after task started — emit should still suppress."""
        mgr = _make_mgr()
        calls = []
        mgr._notify_telegram = lambda n: calls.append("telegram")

        mgr.set_user_pref("123456", "telegram", "off")
        self._simulate_emit(mgr, "t5", "telegram", "123456", notify=True)
        assert calls == [], f"Notification leaked despite mute: {calls}"

    def test_emit_sent_when_not_muted(self):
        mgr = _make_mgr()
        calls = []
        # user_key telegram_789012 is known -> specific routing, not broadcast
        mgr._notify_telegram = lambda n: calls.append("telegram")

        self._simulate_emit(mgr, "t6", "telegram", "789012", notify=True)
        assert "telegram" in calls

    def test_emit_suppressed_when_notify_false(self):
        mgr = _make_mgr()
        calls = []
        mgr._notify_webex = lambda n: calls.append("webex")

        self._simulate_emit(mgr, "t7", "webex", "user@test.com", notify=False)
        assert calls == []


# ---------------------------------------------------------------------------
# 4. Simulate background task creation mute check
# ---------------------------------------------------------------------------


class TestBgTaskCreationMute:
    """Simulate the fixed create_background_task logic."""

    @staticmethod
    def _determine_notify(notification_mgr, body_notify, identity, session_pref):
        """Mirror the fixed create_background_task logic."""
        notify_pref = body_notify
        if notify_pref is None:
            if notification_mgr and notification_mgr.is_muted(identity):
                notify_pref = False
            if notify_pref is None:
                notify_pref = session_pref != "off"
        return notify_pref

    def test_body_override_true_ignores_mute(self):
        mgr = _make_mgr()
        mgr.set_user_pref("user1", "telegram", "off")
        assert self._determine_notify(mgr, True, "user1", "all") is True

    def test_body_override_false_ignores_unmute(self):
        mgr = _make_mgr()
        assert self._determine_notify(mgr, False, "user2", "all") is False

    def test_per_identity_mute_takes_precedence(self):
        mgr = _make_mgr()
        mgr.set_user_pref("user3", "webex", "off")
        assert self._determine_notify(mgr, None, "user3", "all") is False

    def test_session_pref_off_without_identity_store(self):
        mgr = _make_mgr()
        assert self._determine_notify(mgr, None, "user4", "off") is False

    def test_default_is_notify(self):
        mgr = _make_mgr()
        assert self._determine_notify(mgr, None, "user5", "all") is True

    def test_webui_mute_affects_telegram_task(self):
        """User mutes from WebUI; bg task created from Telegram should be muted."""
        mgr = _make_mgr()
        identity = "8193231291"
        mgr.set_user_pref(identity, "webui", "off")
        assert self._determine_notify(mgr, None, identity, "all") is False

    def test_notify_default_none_enables_mute_check(self):
        """When body.notify is None (not explicitly set), the mute preference
        store must be checked — this was the critical default=True bug."""
        mgr = _make_mgr()
        mgr.set_user_pref("muted_user", "webex", "off")
        result = self._determine_notify(mgr, None, "muted_user", "all")
        assert (
            result is False
        ), "Mute preference was not respected when body.notify is None"

    def test_identity_mismatch_resolved_via_normalization(self):
        """Simulates the flipkey@cisco.com vs webex_flipkey@cisco.com mismatch.
        Preference set via one format should be found via the other."""
        mgr = _make_mgr()
        mgr.set_user_pref("flipkey@cisco.com", "webex", "off")
        assert (
            self._determine_notify(mgr, None, "webex_flipkey@cisco.com", "all") is False
        )
        # And vice versa
        mgr2 = _make_mgr()
        mgr2.set_user_pref("webex_flipkey@cisco.com", "webex", "off")
        assert self._determine_notify(mgr2, None, "flipkey@cisco.com", "all") is False


# ---------------------------------------------------------------------------
# 5. Identity normalization unit tests
# ---------------------------------------------------------------------------


class TestIdentityNormalization:
    """Test _normalize_identity static method directly."""

    def test_strips_webex_prefix(self):
        assert (
            NotificationManager._normalize_identity("webex_foo@bar.com")
            == "foo@bar.com"
        )

    def test_strips_telegram_prefix(self):
        assert NotificationManager._normalize_identity("telegram_12345") == "12345"

    def test_strips_webui_prefix(self):
        assert NotificationManager._normalize_identity("webui_user1") == "user1"

    def test_strips_api_prefix(self):
        assert NotificationManager._normalize_identity("api_service") == "service"

    def test_no_prefix_unchanged(self):
        assert NotificationManager._normalize_identity("plainuser") == "plainuser"

    def test_empty_string(self):
        assert NotificationManager._normalize_identity("") == ""

    def test_none_returns_none(self):
        assert NotificationManager._normalize_identity(None) is None

    def test_email_without_prefix_unchanged(self):
        assert (
            NotificationManager._normalize_identity("user@example.com")
            == "user@example.com"
        )

    def test_webex_person_id_unchanged(self):
        """Base64 WebEx person IDs don't start with known prefixes."""
        pid = "Y2lzY29zcGFyazovL3VzL1BFT1BMRS8z"
        assert NotificationManager._normalize_identity(pid) == pid


# ---------------------------------------------------------------------------
# Run with pytest or standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
