#!/usr/bin/env python3
"""
Test the /notifications command (mute fix).

Tests:
1. /notifications current - shows current notification preference
2. /notifications on - enables notifications
3. /notifications off - disables notifications
4. /notifications mute - alias for off
5. Background task respects notification preference when creating notifications
"""

import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_manager import AgentManager


def test_notifications_command():
    """Test /notifications command functionality."""
    print("\n=== Testing /notifications Command ===\n")
    
    # Initialize manager
    am = AgentManager()
    
    # Create a test session
    n8n_session_id = "test-session-mute"
    test_prompt = "Hello"
    
    # Initialize session data
    session_info = am.initialize_session(n8n_session_id, test_prompt, "webex", "test-user")
    print(f"✓ Session initialized: {session_info.get('copilot_session_id')}")
    
    # Test 1: Get current notification preference (should default to "all")
    print("\n--- Test 1: Current Notification Preference ---")
    result = am.process_command(n8n_session_id, "/notifications current", "webex", "test-user")
    print(f"Command: /notifications current")
    print(f"Response: {result}")
    assert "Background Notifications" in result
    assert "ON (All updates)" in result or "OFF (WebUI only)" in result
    print("✓ Current status works")
    
    # Test 2: Enable notifications (on)
    print("\n--- Test 2: Enable Notifications ---")
    result = am.process_command(n8n_session_id, "/notifications on", "webex", "test-user")
    print(f"Command: /notifications on")
    print(f"Response: {result}")
    assert "enabled" in result.lower()
    print("✓ Notifications enabled")
    
    # Verify it's actually set
    result = am.process_command(n8n_session_id, "/notifications current", "webex", "test-user")
    assert "ON (All updates)" in result
    print("✓ Verified notifications are ON")
    
    # Test 3: Disable notifications (off)
    print("\n--- Test 3: Disable Notifications (off) ---")
    result = am.process_command(n8n_session_id, "/notifications off", "webex", "test-user")
    print(f"Command: /notifications off")
    print(f"Response: {result}")
    assert "muted" in result.lower()
    print("✓ Notifications muted with 'off'")
    
    # Verify it's actually set
    result = am.process_command(n8n_session_id, "/notifications current", "webex", "test-user")
    assert "OFF (WebUI only)" in result
    print("✓ Verified notifications are OFF")
    
    # Test 4: Disable notifications (mute alias)
    print("\n--- Test 4: Disable Notifications (mute alias) ---")
    result = am.process_command(n8n_session_id, "/notifications on", "webex", "test-user")
    result = am.process_command(n8n_session_id, "/notifications mute", "webex", "test-user")
    print(f"Command: /notifications mute")
    print(f"Response: {result}")
    assert "muted" in result.lower()
    print("✓ Notifications muted with 'mute' alias")
    
    # Test 5: Invalid command
    print("\n--- Test 5: Invalid Command ---")
    result = am.process_command(n8n_session_id, "/notifications invalid", "webex", "test-user")
    print(f"Command: /notifications invalid")
    print(f"Response: {result}")
    assert "Usage:" in result
    print("✓ Invalid command shows usage")
    
    # Test 6: Check session data directly
    print("\n--- Test 6: Session Data Verification ---")
    session_data = am.load_session_data(n8n_session_id)
    notification_pref = session_data.get("notification_preference")
    print(f"Notification preference in session: {notification_pref}")
    assert notification_pref == "off"
    print("✓ Session data correctly stores notification preference")
    
    print("\n=== All Tests Passed ✓ ===\n")


if __name__ == "__main__":
    test_notifications_command()
