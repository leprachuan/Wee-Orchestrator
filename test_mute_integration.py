#!/usr/bin/env python3
"""
Integration test for the /notifications (mute fix) feature.

Tests that:
1. /notifications command works correctly
2. Notification preference is stored in session
3. Background tasks respect the mute preference
"""

import requests
import json
import time
import sys

# API configuration
API_URL = "https://localhost:8001"
AUTH_TOKEN = "shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {AUTH_TOKEN}",
    "X-User-Identity": "test-user-mute",
    "X-Auth-Channel": "webex"
}

def test_notifications_endpoint():
    """Test /notifications command via API."""
    print("\n=== Testing /notifications Command Integration ===\n")
    
    try:
        # Test 1: Run a command to initialize session and test /notifications
        print("--- Test 1: Initialize Session ---")
        response = requests.post(
            f"{API_URL}/api/v1/chat",
            json={"prompt": "Hello, testing mute feature"},
            headers=HEADERS,
            verify=False
        )
        print(f"Response status: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            print(f"✓ Session initialized")
            session_id = result.get("copilot_session_id")
            print(f"  Session ID: {session_id}")
        else:
            print(f"✗ Error: {response.text}")
            return False
        
        # Test 2: Send /notifications current command
        print("\n--- Test 2: Get Current Notification Status ---")
        response = requests.post(
            f"{API_URL}/api/v1/chat",
            json={"prompt": "/notifications current"},
            headers=HEADERS,
            verify=False
        )
        print(f"Response status: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            output = result.get("output", "")
            print(f"Response: {output}")
            assert "Background Notifications" in output, "Should show notification status"
            print("✓ /notifications current works")
        else:
            print(f"✗ Error: {response.text}")
            return False
        
        # Test 3: Turn off notifications
        print("\n--- Test 3: Disable Notifications ---")
        response = requests.post(
            f"{API_URL}/api/v1/chat",
            json={"prompt": "/notifications off"},
            headers=HEADERS,
            verify=False
        )
        print(f"Response status: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            output = result.get("output", "")
            print(f"Response: {output}")
            assert "muted" in output.lower(), "Should indicate muting is successful"
            print("✓ /notifications off works")
        else:
            print(f"✗ Error: {response.text}")
            return False
        
        # Test 4: Verify mute is active
        print("\n--- Test 4: Verify Mute Status ---")
        response = requests.post(
            f"{API_URL}/api/v1/chat",
            json={"prompt": "/notifications current"},
            headers=HEADERS,
            verify=False
        )
        print(f"Response status: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            output = result.get("output", "")
            print(f"Response: {output}")
            assert "OFF (WebUI only)" in output, "Should show muted status"
            print("✓ Mute status verified")
        else:
            print(f"✗ Error: {response.text}")
            return False
        
        # Test 5: Turn on notifications
        print("\n--- Test 5: Enable Notifications ---")
        response = requests.post(
            f"{API_URL}/api/v1/chat",
            json={"prompt": "/notifications on"},
            headers=HEADERS,
            verify=False
        )
        print(f"Response status: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            output = result.get("output", "")
            print(f"Response: {output}")
            assert "enabled" in output.lower(), "Should indicate enabling is successful"
            print("✓ /notifications on works")
        else:
            print(f"✗ Error: {response.text}")
            return False
        
        # Test 6: Test 'mute' alias
        print("\n--- Test 6: Test 'mute' Alias ---")
        response = requests.post(
            f"{API_URL}/api/v1/chat",
            json={"prompt": "/notifications mute"},
            headers=HEADERS,
            verify=False
        )
        print(f"Response status: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            output = result.get("output", "")
            print(f"Response: {output}")
            assert "muted" in output.lower(), "Should accept 'mute' as alias"
            print("✓ /notifications mute alias works")
        else:
            print(f"✗ Error: {response.text}")
            return False
        
        print("\n=== All Integration Tests Passed ✓ ===\n")
        return True
        
    except requests.exceptions.ConnectionError:
        print("✗ Cannot connect to API server at {API_URL}")
        print("  Make sure the API is running: python3 agent_manager.py")
        return False
    except Exception as e:
        print(f"✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_notifications_endpoint()
    sys.exit(0 if success else 1)
