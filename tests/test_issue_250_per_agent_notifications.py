"""
Regression tests for Issue #250: Per-Agent Notification Preferences.

Tests the new per-agent notification preference system that replaces
the global all-or-nothing toggle.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, '/opt/n8n-copilot-shim-dev')

from notification_manager import NotificationManager


class TestPerAgentNotifications(unittest.TestCase):
    """Test per-agent notification preferences."""

    def setUp(self):
        """Create a temporary notification manager for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.prefs_file = os.path.join(self.temp_dir.name, "prefs.json")
        self.notif_mgr = NotificationManager(
            notif_file=os.path.join(self.temp_dir.name, "notif.json"),
            prefs_file=self.prefs_file,
        )

    def tearDown(self):
        """Clean up temporary files."""
        self.temp_dir.cleanup()

    def test_set_and_get_agent_pref(self):
        """Test setting and getting per-agent preferences."""
        # Set preference for wee-qa to "off"
        self.notif_mgr.set_agent_pref("user123", "wee-qa", "off")
        
        # Verify it was stored
        pref = self.notif_mgr.get_agent_pref("user123", "wee-qa")
        self.assertEqual(pref, "off")
        
        # Verify other agents default to "on"
        pref = self.notif_mgr.get_agent_pref("user123", "research")
        self.assertEqual(pref, "on")

    def test_is_agent_muted(self):
        """Test is_agent_muted convenience method."""
        self.notif_mgr.set_agent_pref("user123", "research", "off")
        
        # research should be muted
        self.assertTrue(self.notif_mgr.is_agent_muted("user123", "research"))
        
        # wee-qa should NOT be muted (default "on")
        self.assertFalse(self.notif_mgr.is_agent_muted("user123", "wee-qa"))

    def test_get_all_agent_prefs(self):
        """Test retrieving all agent preferences for a user."""
        # Set multiple preferences
        self.notif_mgr.set_agent_pref("user456", "research", "off")
        self.notif_mgr.set_agent_pref("user456", "wee-qa", "on")
        self.notif_mgr.set_agent_pref("user456", "smarthome", "off")
        
        # Get all preferences
        all_prefs = self.notif_mgr.get_all_agent_prefs("user456")
        
        # Verify all are present
        self.assertEqual(all_prefs.get("research"), "off")
        self.assertEqual(all_prefs.get("wee-qa"), "on")
        self.assertEqual(all_prefs.get("smarthome"), "off")

    def test_get_all_agent_prefs_empty(self):
        """Test get_all_agent_prefs when no preferences are set."""
        all_prefs = self.notif_mgr.get_all_agent_prefs("user789")
        self.assertEqual(all_prefs, {})

    def test_agent_prefs_persist_across_instances(self):
        """Test that agent prefs are persisted and loaded correctly."""
        # Set preference with first instance
        mgr1 = self.notif_mgr
        mgr1.set_agent_pref("persistent_user", "wee-dev", "off")
        
        # Create new instance with same file
        mgr2 = NotificationManager(
            notif_file=os.path.join(self.temp_dir.name, "notif.json"),
            prefs_file=self.prefs_file,
        )
        
        # Verify preference was loaded
        pref = mgr2.get_agent_pref("persistent_user", "wee-dev")
        self.assertEqual(pref, "off")

    def test_user_identity_normalization(self):
        """Test that different identity formats map to same preference."""
        # Set preference with raw identity
        self.notif_mgr.set_agent_pref("telegram_12345_67890", "research", "off")
        
        # Retrieve with different formats (should all normalize to same thing)
        pref1 = self.notif_mgr.get_agent_pref("telegram_67890", "research")
        pref2 = self.notif_mgr.get_agent_pref("67890", "research")
        
        # Both should get the stored preference
        self.assertEqual(pref1, "off")
        self.assertEqual(pref2, "off")

    def test_create_notification_with_agent(self):
        """Test that create_notification accepts agent parameter."""
        # Set agent preference to "off"
        self.notif_mgr.set_agent_pref("user_with_prefs", "research", "off")
        
        # Create notification for muted agent
        notif = self.notif_mgr.create_notification(
            task_id="task_123",
            description="Test task",
            status="completed",
            channel="telegram",
            user_key="user_with_prefs",
            agent="research",  # New parameter
        )
        
        # Notification should still be created
        self.assertIsNotNone(notif)
        self.assertEqual(notif["agent"], "research")
        
        # Verify it's stored in WebUI even though external notif was skipped
        notifications = self.notif_mgr.list_notifications("user_with_prefs")
        self.assertGreater(len(notifications), 0)

    def test_backward_compatibility_old_global_prefs(self):
        """Test that old global preferences don't interfere with agent prefs."""
        # Set old-style preference (user global)
        self.notif_mgr.set_user_pref("oldstyle_user", "telegram", "off")
        
        # Now set agent-specific pref
        self.notif_mgr.set_agent_pref("oldstyle_user", "wee-qa", "on")
        
        # Agent pref should take precedence
        self.assertFalse(self.notif_mgr.is_agent_muted("oldstyle_user", "wee-qa"))
        
        # User global should still be "off"
        self.assertTrue(self.notif_mgr.is_muted("oldstyle_user"))


if __name__ == "__main__":
    unittest.main()
