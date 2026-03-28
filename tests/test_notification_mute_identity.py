#!/usr/bin/env python3
"""
Tests for notification mute identity normalization and cross-channel scenarios.

Validates that:
- _normalize_identity handles compound Telegram identities
- Muting via any identity format applies to all equivalent forms
- create_notification checks per-identity mute at delivery time
- Global and per-identity mute coexist correctly
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from notification_manager import NotificationManager


@pytest.fixture
def mgr(tmp_path):
    """Create a NotificationManager with isolated temp files."""
    return NotificationManager(
        notif_file=str(tmp_path / "notifications.json"),
        prefs_file=str(tmp_path / "prefs.json"),
    )


# ---- _normalize_identity unit tests ----


class TestNormalizeIdentity:
    """Verify _normalize_identity extracts bare user IDs correctly."""

    def test_bare_numeric_id(self):
        assert NotificationManager._normalize_identity("8193231291") == "8193231291"

    def test_telegram_simple(self):
        assert (
            NotificationManager._normalize_identity("telegram_8193231291")
            == "8193231291"
        )

    def test_telegram_compound(self):
        """Compound telegram_<botid>_<userid> → bare userid."""
        assert (
            NotificationManager._normalize_identity("telegram_8405010413_8193231291")
            == "8193231291"
        )

    def test_webex_prefix(self):
        assert NotificationManager._normalize_identity("webex_person123") == "person123"

    def test_webui_prefix(self):
        assert NotificationManager._normalize_identity("webui_user456") == "user456"

    def test_api_prefix(self):
        assert NotificationManager._normalize_identity("api_svc789") == "svc789"

    def test_global_key_passthrough(self):
        assert NotificationManager._normalize_identity("_global") == "_global"

    def test_empty_string(self):
        assert NotificationManager._normalize_identity("") == ""

    def test_none_passthrough(self):
        assert NotificationManager._normalize_identity(None) is None

    def test_unknown_prefix(self):
        """Unknown prefixes are left unchanged."""
        assert NotificationManager._normalize_identity("slack_U12345") == "slack_U12345"

    def test_triple_segment_telegram(self):
        """Edge case: extra underscores in Telegram identity."""
        assert (
            NotificationManager._normalize_identity("telegram_bot_extra_999") == "999"
        )


# ---- Per-identity preference tests with normalization ----


class TestPerIdentityPrefsNormalized:
    """Setting prefs via any identity format matches all equivalents."""

    def test_set_via_compound_get_via_bare(self, mgr):
        mgr.set_user_pref("telegram_8405010413_8193231291", "telegram", "off")
        assert mgr.is_muted("8193231291")

    def test_set_via_bare_get_via_compound(self, mgr):
        mgr.set_user_pref("8193231291", "telegram", "off")
        assert mgr.is_muted("telegram_8405010413_8193231291")

    def test_set_via_simple_telegram_get_via_bare(self, mgr):
        mgr.set_user_pref("telegram_8193231291", "telegram", "off")
        assert mgr.is_muted("8193231291")

    def test_set_via_bare_get_via_simple_telegram(self, mgr):
        mgr.set_user_pref("8193231291", "webui", "off")
        assert mgr.is_muted("telegram_8193231291")

    def test_unmute_via_different_format(self, mgr):
        """Mute via compound, unmute via bare — unmute takes effect."""
        mgr.set_user_pref("telegram_8405010413_8193231291", "telegram", "off")
        assert mgr.is_muted("8193231291")
        mgr.set_user_pref("8193231291", "webui", "all")
        assert not mgr.is_muted("telegram_8405010413_8193231291")

    def test_different_users_isolated(self, mgr):
        """Muting one user doesn't affect another."""
        mgr.set_user_pref("111111", "telegram", "off")
        assert mgr.is_muted("111111")
        assert not mgr.is_muted("222222")

    def test_global_mute_independent(self, mgr):
        """Global mute is independent of per-identity mutes."""
        mgr.set_user_pref("_global", "webui", "off")
        assert mgr.is_muted("_global")
        assert not mgr.is_muted("8193231291")

    def test_persistence_across_instances(self, tmp_path):
        """Prefs survive reconstruction of NotificationManager."""
        prefs = str(tmp_path / "prefs.json")
        notif = str(tmp_path / "notif.json")
        mgr1 = NotificationManager(notif_file=notif, prefs_file=prefs)
        mgr1.set_user_pref("telegram_8405010413_123", "telegram", "off")
        mgr2 = NotificationManager(notif_file=notif, prefs_file=prefs)
        assert mgr2.is_muted("123")


# ---- create_notification delivery-time mute checks ----


class TestCreateNotificationMuteChecks:
    """create_notification suppresses external routing when muted."""

    def test_global_mute_blocks_telegram(self, mgr):
        calls = []
        mgr._notify_telegram = lambda n: calls.append(n)
        mgr.set_user_pref("_global", "webui", "off")
        mgr.create_notification(
            task_id="t1",
            description="test",
            status="completed",
            channel="telegram",
            user_key="telegram_123",
            skip_external=False,
        )
        assert len(calls) == 0

    def test_global_mute_blocks_webex(self, mgr):
        calls = []
        mgr._notify_webex = lambda n: calls.append(n)
        mgr.set_user_pref("_global", "webui", "off")
        mgr.create_notification(
            task_id="t2",
            description="test",
            status="completed",
            channel="webex",
            user_key="webex_person1",
            skip_external=False,
        )
        assert len(calls) == 0

    def test_per_identity_mute_blocks_telegram(self, mgr):
        """Per-identity mute (via compound identity) blocks telegram delivery."""
        calls = []
        mgr._notify_telegram = lambda n: calls.append(n)
        mgr.set_user_pref("telegram_8405010413_12345", "telegram", "off")
        mgr.create_notification(
            task_id="t3",
            description="test",
            status="completed",
            channel="telegram",
            user_key="12345",
            skip_external=False,
        )
        assert len(calls) == 0

    def test_unmuted_user_receives_telegram(self, mgr):
        calls = []
        mgr._notify_telegram_broadcast = lambda n: calls.append(n)
        mgr.create_notification(
            task_id="t4",
            description="test",
            status="completed",
            channel="telegram",
            user_key="telegram_999",
            skip_external=False,
        )
        assert len(calls) == 1

    def test_skip_external_true_always_blocks(self, mgr):
        calls = []
        mgr._notify_telegram = lambda n: calls.append(n)
        mgr.create_notification(
            task_id="t5",
            description="test",
            status="completed",
            channel="telegram",
            user_key="telegram_999",
            skip_external=True,
        )
        assert len(calls) == 0

    def test_webui_stored_even_when_muted(self, mgr):
        """Notifications are stored for WebUI polling regardless of mute."""
        mgr.set_user_pref("_global", "webui", "off")
        mgr._notify_telegram = lambda n: None
        mgr.create_notification(
            task_id="t6",
            description="test",
            status="completed",
            channel="telegram",
            user_key="telegram_123",
            skip_external=False,
        )
        notifs = mgr.list_notifications("telegram_123")
        assert len(notifs) == 1

    def test_per_identity_mute_uses_normalized_user_key(self, mgr):
        """Muting bare ID blocks delivery when user_key has telegram_ prefix."""
        calls = []
        mgr._notify_telegram = lambda n: calls.append(n)
        mgr.set_user_pref("77777", "webui", "off")
        mgr.create_notification(
            task_id="t7",
            description="test",
            status="completed",
            channel="telegram",
            user_key="telegram_77777",
            skip_external=False,
        )
        assert (
            len(calls) == 0
        ), "Bare-ID mute should block telegram-prefixed user_key delivery"
