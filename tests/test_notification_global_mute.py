#!/usr/bin/env python3
"""
Test: Notification mute global preference fix.

Validates that /notifications off works across channels by using
the _global key in notification_prefs.json, and that the
NotificationManager.create_notification() enforces the mute
even when skip_external=False.

Tests 1-4: Unit tests (NotificationManager in isolation)
Tests 5-6: Integration tests via production API (port 8000)
"""

import json
import pytest
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from notification_manager import NotificationManager  # noqa: E402

# --- API helpers for integration tests ---

API_BASE = "https://127.0.0.1:8001/api/v1"
API_TOKEN = "shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU"


def api_post(path, data=None, identity="test-mute-user", channel="webui"):
    """POST to the production API."""
    import subprocess

    cmd = [
        "curl",
        "-sk",
        "-X",
        "POST",
        f"{API_BASE}{path}",
        "-H",
        "Content-Type: application/json",
        "-H",
        f"Authorization: Bearer {API_TOKEN}",
        "-H",
        f"X-User-Identity: {identity}",
        "-H",
        f"X-Auth-Channel: {channel}",
    ]
    if data is not None:
        cmd.extend(["-d", json.dumps(data)])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw": result.stdout, "error": result.stderr}


def api_get(path, identity="test-mute-user", channel="webui"):
    """GET from the production API."""
    import subprocess

    cmd = [
        "curl",
        "-sk",
        "-X",
        "GET",
        f"{API_BASE}{path}",
        "-H",
        f"Authorization: Bearer {API_TOKEN}",
        "-H",
        f"X-User-Identity: {identity}",
        "-H",
        f"X-Auth-Channel: {channel}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw": result.stdout, "error": result.stderr}


# ---- Unit Tests (NotificationManager in isolation) ----


def test_global_mute_in_notification_manager():
    """NotificationManager respects _global mute in create_notification."""
    print("\n=== Test 1: Global mute in NotificationManager ===\n")

    with tempfile.TemporaryDirectory() as tmp:
        notif_file = os.path.join(tmp, "notifications.json")
        prefs_file = os.path.join(tmp, "prefs.json")
        mgr = NotificationManager(notif_file=notif_file, prefs_file=prefs_file)

        assert not mgr.is_muted("_global"), "Global should not be muted by default"
        assert not mgr.is_muted("some_user_id"), "Arbitrary user should not be muted"
        print("✓ Default state: nothing muted")

        # Set global mute
        mgr.set_user_pref("_global", "webui", "off")
        assert mgr.is_muted("_global"), "Global should be muted after set_user_pref"
        print("✓ _global mute stored correctly")

        # Monkeypatch to detect external calls (both specific and broadcast)
        telegram_called = []
        webex_called = []
        mgr._notify_telegram = lambda n: telegram_called.append(n)
        mgr._notify_telegram_broadcast = lambda n: telegram_called.append(n)
        mgr._notify_webex = lambda n: webex_called.append(n)
        mgr._notify_webex_broadcast = lambda n: webex_called.append(n)

        # Create notification for telegram with skip_external=False
        mgr.create_notification(
            task_id="test-task-1",
            description="test",
            status="completed",
            channel="telegram",
            user_key="telegram_123456",
            skip_external=False,
        )
        assert (
            len(telegram_called) == 0
        ), f"Telegram should NOT be called (global mute), got {len(telegram_called)}"
        print("✓ Telegram suppressed by global mute")

        mgr.create_notification(
            task_id="test-task-2",
            description="test",
            status="completed",
            channel="webex",
            user_key="webex_person123",
            skip_external=False,
        )
        assert (
            len(webex_called) == 0
        ), f"WebEx should NOT be called (global mute), got {len(webex_called)}"
        print("✓ WebEx suppressed by global mute")

        # Notification still stored for WebUI polling
        notifs = mgr.list_notifications("telegram_123456")
        assert len(notifs) == 1, f"Expected 1 stored notification, got {len(notifs)}"
        print("✓ Notification stored for WebUI polling")

        # Un-mute globally
        mgr.set_user_pref("_global", "webui", "all")
        assert not mgr.is_muted("_global")

        mgr.create_notification(
            task_id="test-task-3",
            description="test",
            status="completed",
            channel="telegram",
            user_key="telegram_123456",
            skip_external=False,
        )
        assert (
            len(telegram_called) == 1
        ), f"Telegram should be called after un-mute, got {len(telegram_called)}"
        print("✓ Telegram sent after global un-mute")

    print("\n=== Test 1 Passed ✓ ===\n")


def test_per_identity_mute():
    """Per-identity mute still works alongside global."""
    print("\n=== Test 2: Per-identity mute ===\n")

    with tempfile.TemporaryDirectory() as tmp:
        notif_file = os.path.join(tmp, "notifications.json")
        prefs_file = os.path.join(tmp, "prefs.json")
        mgr = NotificationManager(notif_file=notif_file, prefs_file=prefs_file)

        mgr.set_user_pref("user_abc", "telegram", "off")
        assert mgr.is_muted("user_abc")
        assert not mgr.is_muted("user_xyz")
        print("✓ Per-identity mute works correctly")

    print("\n=== Test 2 Passed ✓ ===\n")


def test_emit_notification_respects_global_mute():
    """create_notification enforces global mute even when skip_external=False."""
    print("\n=== Test 3: create_notification global mute enforcement ===\n")

    with tempfile.TemporaryDirectory() as tmp:
        prefs_file = os.path.join(tmp, "prefs.json")
        notif_file = os.path.join(tmp, "notifications.json")
        nmgr = NotificationManager(notif_file=notif_file, prefs_file=prefs_file)

        nmgr.set_user_pref("_global", "webui", "off")

        telegram_calls = []
        webex_calls = []
        nmgr._notify_telegram_broadcast = lambda n: telegram_calls.append(n)
        nmgr._notify_webex_broadcast = lambda n: webex_calls.append(n)

        nmgr.create_notification(
            task_id="bg_test123",
            description="test prompt",
            status="completed",
            channel="telegram",
            user_key="telegram_8193231291",
            skip_external=False,
        )

        assert (
            len(telegram_calls) == 0
        ), f"Telegram blocked by global mute, got {len(telegram_calls)}"
        print("✓ Telegram blocked (global mute)")

        all_notifs = nmgr.list_notifications("telegram_8193231291")
        assert len(all_notifs) == 1
        print("✓ Notification stored for WebUI polling")

    print("\n=== Test 3 Passed ✓ ===\n")


def test_prefs_file_persistence():
    """Preferences survive NotificationManager reconstruction."""
    print("\n=== Test 4: Prefs file persistence ===\n")

    with tempfile.TemporaryDirectory() as tmp:
        prefs_file = os.path.join(tmp, "prefs.json")
        notif_file = os.path.join(tmp, "notifications.json")

        # Set preference with first instance
        mgr1 = NotificationManager(notif_file=notif_file, prefs_file=prefs_file)
        mgr1.set_user_pref("_global", "webui", "off")
        assert mgr1.is_muted("_global")
        print("✓ Preference set with instance 1")

        # Verify with new instance (simulates API restart)
        mgr2 = NotificationManager(notif_file=notif_file, prefs_file=prefs_file)
        assert mgr2.is_muted("_global"), "Global mute should persist across instances"
        print("✓ Preference survived reconstruction")

        # Un-mute and verify persistence
        mgr2.set_user_pref("_global", "webui", "all")
        mgr3 = NotificationManager(notif_file=notif_file, prefs_file=prefs_file)
        assert not mgr3.is_muted("_global"), "Un-mute should persist"
        print("✓ Un-mute persisted correctly")

    print("\n=== Test 4 Passed ✓ ===\n")


# ---- Integration Tests (via production API on port 8000) ----


@pytest.mark.skipif(
    os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true",
    reason="Integration test requires running API server"
)
def test_api_notifications_off_sets_global_mute():
    """Sending /notifications off via API sets _global mute in prefs file."""
    print("\n=== Test 5: API /notifications off → global mute ===\n")

    test_sid = f"test-mute-{int(time.time())}"
    identity = "test-mute-integration"

    # Step 1: Create session
    resp = api_post(
        "/sessions/create",
        data={"session_id": test_sid},
        identity=identity,
        channel="webui",
    )
    print(f"  session create: {json.dumps(resp)[:200]}")
    assert "session_id" in resp, f"Failed to create session: {resp}"

    # Step 2: Execute /notifications off
    resp = api_post(
        f"/sessions/{test_sid}/execute",
        data={"query": "/notifications off"},
        identity=identity,
        channel="webui",
    )
    print(f"  /notifications off response: {json.dumps(resp)[:200]}")
    response_text = str(resp)
    assert (
        "muted" in response_text.lower() or "off" in response_text.lower()
    ), f"Expected 'muted' or 'off' in response: {resp}"
    print("✓ API accepted /notifications off")

    # Step 3: Verify the prefs file has _global muted
    prefs_path = "/home/n8n/.copilot/notification_prefs.json"  # server runs as n8n user
    if os.path.exists(prefs_path):
        with open(prefs_path) as f:
            prefs = json.load(f)
        global_entry = prefs.get("_global", {})
        global_pref = (
            global_entry.get("preference", "all")
            if isinstance(global_entry, dict)
            else "all"
        )
        assert (
            global_pref == "off"
        ), f"Expected _global preference 'off', got '{global_pref}'. Full prefs: {json.dumps(prefs)}"  # noqa: E501
        print("✓ _global preference is 'off' in notification_prefs.json")
    else:
        raise AssertionError(f"notification_prefs.json not found at {prefs_path}")

    # Step 4: Turn notifications back on (cleanup)
    resp = api_post(
        f"/sessions/{test_sid}/execute",
        data={"query": "/notifications on"},
        identity=identity,
        channel="webui",
    )
    print(f"  /notifications on response: {json.dumps(resp)[:200]}")

    # Verify un-mute
    with open(prefs_path) as f:
        prefs = json.load(f)
    global_entry = prefs.get("_global", {})
    global_pref = (
        global_entry.get("preference", "all")
        if isinstance(global_entry, dict)
        else "all"
    )
    assert (
        global_pref == "all"
    ), f"Expected _global preference 'all' after on, got '{global_pref}'"
    print("✓ _global preference is 'all' after re-enabling")

    print("\n=== Test 5 Passed ✓ ===\n")


@pytest.mark.skipif(
    os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true",
    reason="Integration test requires running API server"
)
def test_api_cross_channel_mute_blocks_notification():
    """Mute from WebUI blocks notifications for Telegram-originated bg tasks."""
    print("\n=== Test 6: Cross-channel mute blocks external notifications ===\n")

    test_sid = f"test-cross-{int(time.time())}"
    identity = "test-cross-channel"

    # Step 1: Create session and mute from WebUI
    api_post(
        "/sessions/create",
        data={"session_id": test_sid},
        identity=identity,
        channel="webui",
    )
    resp = api_post(
        f"/sessions/{test_sid}/execute",
        data={"query": "/notifications off"},
        identity=identity,
        channel="webui",
    )
    assert (
        "muted" in str(resp).lower() or "off" in str(resp).lower()
    ), f"Expected muted confirmation: {resp}"
    print("✓ Muted from WebUI")

    # Step 2: Verify that NotificationManager would block a Telegram notification
    real_mgr = NotificationManager(
        prefs_file="/home/n8n/.copilot/notification_prefs.json"
    )  # server runs as n8n
    assert real_mgr.is_muted(
        "_global"
    ), f"Expected global mute. Prefs: {json.dumps(real_mgr._load_prefs())}"
    print("✓ _global is muted (covers all channels)")

    # Step 3: Simulate Telegram bg task completion
    telegram_calls = []
    real_mgr._notify_telegram_broadcast = lambda n: telegram_calls.append(n)

    real_mgr.create_notification(
        task_id="test-cross-bg",
        description="simulated telegram task",
        status="completed",
        channel="telegram",
        user_key="telegram_8193231291",
        skip_external=False,
    )

    assert (
        len(telegram_calls) == 0
    ), f"Telegram should be BLOCKED by global mute, got {len(telegram_calls)} calls"
    print("✓ Telegram notification BLOCKED by cross-channel global mute")

    # Step 4: Cleanup — re-enable
    resp = api_post(
        f"/sessions/{test_sid}/execute",
        data={"query": "/notifications on"},
        identity=identity,
        channel="webui",
    )

    real_mgr2 = NotificationManager(
        prefs_file="/home/n8n/.copilot/notification_prefs.json"
    )  # server runs as n8n
    assert not real_mgr2.is_muted(
        "_global"
    ), "Global mute should be cleared after /notifications on"
    print("✓ Global mute cleared after re-enabling")

    print("\n=== Test 6 Passed ✓ ===\n")


if __name__ == "__main__":
    passed = 0
    failed = 0
    tests = [
        # Unit tests (no API dependency)
        test_global_mute_in_notification_manager,
        test_per_identity_mute,
        test_emit_notification_respects_global_mute,
        test_prefs_file_persistence,
        # Integration tests (require production API on port 8000)
        test_api_notifications_off_sets_global_mute,
        test_api_cross_channel_mute_blocks_notification,
    ]

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n❌ FAILED: {test_fn.__name__}: {e}\n")
            import traceback

            traceback.print_exc()

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    print(f"{'='*50}")
    sys.exit(1 if failed else 0)
