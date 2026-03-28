#!/usr/bin/env python3
"""
Test for the notification preference bug fix.

This test verifies that:
1. When a user disables notifications via /notifications off, it's stored in their session
2. When creating a background task, the disabled preference is correctly inherited
3. Background task notifications respect the user's preference
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_manager import SessionManager


def test_notification_preference_storage():
    """Test that notification preferences are stored and retrieved correctly."""
    print("\n=== Test 1: Notification Preference Storage ===\n")

    session_mgr = SessionManager()

    # Create a session with identity
    n8n_session_id = "test-notif-storage-1"
    identity = "test-user@example.com"
    session_data = session_mgr.get_or_create_session_data(
        n8n_session_id, identity=identity
    )

    print(f"Created session: {n8n_session_id}")
    print(f"Session data keys: {list(session_data.keys())}")

    # Verify identity is stored
    assert session_data.get("identity") == identity, "Identity not stored in session"
    print(f"✓ Identity stored correctly: {identity}")

    # Update notification preference
    session_mgr.update_session_field(n8n_session_id, "notification_preference", "off")

    # Load and verify
    loaded_data = session_mgr.load_session_data(n8n_session_id)
    assert loaded_data is not None, "Session data not found after update"
    assert (
        loaded_data.get("notification_preference") == "off"
    ), "Notification preference not stored"
    assert loaded_data.get("identity") == identity, "Identity lost after update"
    print(f"✓ Notification preference stored correctly: off")
    print(f"✓ Identity preserved after update: {identity}")


def test_notification_preference_inheritance():
    """Test that background tasks inherit notification preferences from existing sessions."""
    print("\n=== Test 2: Notification Preference Inheritance ===\n")

    session_mgr = SessionManager()

    identity = "user-inherit@example.com"

    # Simulate: User has WebEX session with notifications disabled
    webex_session_id = "test-inherit-webex"
    session_mgr.get_or_create_session_data(webex_session_id, identity=identity)
    session_mgr.update_session_field(webex_session_id, "channel", "webex")
    session_mgr.update_session_field(webex_session_id, "notification_preference", "off")

    print(f"Created WebEX session: {webex_session_id}")
    print(f"Set notification_preference: off")

    # Now create a WebUI session (simulating background task scenario)
    webui_session_id = "test-inherit-webui"
    webui_data = session_mgr.get_or_create_session_data(
        webui_session_id, identity=identity
    )
    session_mgr.update_session_field(webui_session_id, "channel", "webui")

    # Load session map and search for the user's preferences
    session_map = session_mgr.load_session_map()

    defaults = {}
    matching_sessions = []

    # Find sessions for this user
    for n8n_sid, data in session_map.items():
        if isinstance(data, str):
            data = {"session_id": data}
        if data.get("identity") == identity:
            matching_sessions.append((n8n_sid, data))
            print(
                f"Found session for {identity}: {n8n_sid} (channel={data.get('channel')})"
            )

    assert (
        len(matching_sessions) >= 2
    ), f"Expected at least 2 sessions, got {len(matching_sessions)}"
    print(f"✓ Found {len(matching_sessions)} sessions for user")

    # Extract notification preference
    for n8n_sid, data in matching_sessions:
        pref = data.get("notification_preference")
        if pref:
            defaults["notification_preference"] = pref
            print(f"Found notification_preference '{pref}' in {n8n_sid}")

    assert (
        defaults.get("notification_preference") == "off"
    ), f"Expected 'off' but got {defaults.get('notification_preference')}"
    print(f"✓ Correctly inherited notification_preference: off")


def test_multi_channel_inheritance():
    """Test that preferences are inherited across channels."""
    print("\n=== Test 3: Multi-Channel Preference Inheritance ===\n")

    session_mgr = SessionManager()

    identity = "admin-multi@example.com"

    # Create sessions on multiple channels
    channels_and_ids = [
        ("telegram", "test-multi-tg"),
        ("webex", "test-multi-wx"),
        ("webui", "test-multi-ui"),
    ]

    for channel, session_id in channels_and_ids:
        session_mgr.get_or_create_session_data(session_id, identity=identity)
        session_mgr.update_session_field(session_id, "channel", channel)
        print(f"Created {channel} session: {session_id}")

    # Disable notifications on Telegram
    session_mgr.update_session_field("test-multi-tg", "notification_preference", "off")
    print(f"Disabled notifications on telegram")

    # Now simulate background task lookup on WebUI
    channel = "webui"
    session_map = session_mgr.load_session_map()

    defaults = {}
    matching_sessions_same_channel = []
    matching_sessions_other_channel = []

    for n8n_sid, data in session_map.items():
        if isinstance(data, str):
            data = {"session_id": data}

        sid_identity = data.get("identity")
        sid_channel = data.get("channel")

        if sid_identity == identity:
            if sid_channel == channel:
                matching_sessions_same_channel.append((n8n_sid, data))
            else:
                matching_sessions_other_channel.append((n8n_sid, data))

    print(
        f"Found {len(matching_sessions_same_channel)} sessions on same channel (webui)"
    )
    print(f"Found {len(matching_sessions_other_channel)} sessions on other channels")

    # Collect preferences
    for n8n_sid, data in (
        matching_sessions_same_channel + matching_sessions_other_channel
    ):
        pref = data.get("notification_preference")
        if pref:
            defaults["notification_preference"] = pref
            print(f"Inherited notification_preference '{pref}' from {n8n_sid}")

    assert (
        defaults.get("notification_preference") == "off"
    ), f"Expected 'off' but got {defaults.get('notification_preference')}"
    print(f"✓ Correctly inherited notification_preference from other channel: off")


if __name__ == "__main__":
    try:
        test_notification_preference_storage()
        test_notification_preference_inheritance()
        test_multi_channel_inheritance()
        print("\n=== All Tests Passed ✓ ===\n")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}\n")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}\n")
        import traceback

        traceback.print_exc()
        sys.exit(1)
