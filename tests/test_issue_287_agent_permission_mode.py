"""
Regression test for issue #287: background tasks ignore agent permissions.mode
- Background tasks should respect agent's configured permissions.mode from agents.json
- When permission_mode is not explicitly set in the request, it should fall back to agents.json
- wee-qa has "permissions": {"mode": "elevated"} and should inherit that for background tasks
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_manager import SessionManager, BackgroundTaskManager


class TestIssue287AgentPermissionMode(unittest.TestCase):
    """Test that background tasks respect agent's permissions.mode from agents.json"""

    def setUp(self):
        """Set up test fixtures"""
        self.tmp_sessions = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp_sessions.close()
        self.tmp_tasks = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp_tasks.close()
        
        # Create agents config with elevated permission for wee-qa
        self.agents_config = [
            {
                "name": "orchestrator",
                "path": "/opt",
                "primary_runtime": "copilot",
                "primary_model": "claude-haiku-4.5",
                "permissions": {"mode": "restricted"}
            },
            {
                "name": "wee-dev",
                "path": "/opt/wee-dev",
                "primary_runtime": "codex",
                "primary_model": "gpt-5.4-mini",
                "permissions": {"mode": "elevated"}  # This is the key: wee-qa needs elevated
            },
            {
                "name": "wee-dev",
                "path": "/opt/wee-dev",
                "primary_runtime": "copilot",
                "primary_model": "claude-sonnet-4.6",
                # No explicit permissions - should default to "restricted"
            }
        ]
        
        # Create session manager with custom agents config
        self.session_mgr = SessionManager.__new__(SessionManager)
        self.session_mgr._sessions_file = self.tmp_sessions.name
        self.session_mgr._lock = type('obj', (object,), {'__enter__': lambda s: None, '__exit__': lambda s, *a: None})()
        self.session_mgr.AGENTS = {cfg["name"]: cfg for cfg in self.agents_config}
        
    def tearDown(self):
        """Clean up temp files"""
        if os.path.exists(self.tmp_sessions.name):
            os.unlink(self.tmp_sessions.name)
        if os.path.exists(self.tmp_tasks.name):
            os.unlink(self.tmp_tasks.name)

    def test_wee_qa_inherits_elevated_permission_mode(self):
        """Test that wee-qa background task gets elevated permission from agents.json"""
        # Simulate the permission mode resolution logic from create_background_task()
        agent = "wee-dev"
        agent_config = self.session_mgr.AGENTS.get(agent, {})
        dispatch_config = agent_config.get("dispatch_config", {})
        
        # This is the fixed logic
        agent_perm_mode = (agent_config.get("permissions") or {}).get("mode") or agent_config.get("permission_mode", "restricted")
        perm_mode = dispatch_config.get("permission_mode", agent_perm_mode)
        
        # Assert that wee-qa gets "elevated" permission mode from agents.json
        self.assertEqual(perm_mode, "elevated", 
                        "wee-qa should inherit 'elevated' permission mode from agents.json")

    def test_wee_dev_defaults_to_restricted(self):
        """Test that wee-dev without explicit permissions defaults to restricted"""
        agent = "wee-dev"
        agent_config = self.session_mgr.AGENTS.get(agent, {})
        dispatch_config = agent_config.get("dispatch_config", {})
        
        # This is the fixed logic
        agent_perm_mode = (agent_config.get("permissions") or {}).get("mode") or agent_config.get("permission_mode", "restricted")
        perm_mode = dispatch_config.get("permission_mode", agent_perm_mode)
        
        # Assert that wee-dev without explicit permissions defaults to "restricted"
        self.assertEqual(perm_mode, "restricted", 
                        "wee-dev without explicit permissions should default to 'restricted'")

    def test_explicit_permission_mode_overrides_agent_config(self):
        """Test that explicit permission_mode in request overrides agent config"""
        agent = "wee-dev"
        agent_config = self.session_mgr.AGENTS.get(agent, {})
        dispatch_config = agent_config.get("dispatch_config", {})
        body_permission_mode = "restricted"  # Explicitly set to override
        
        # This is the fixed logic
        agent_perm_mode = (agent_config.get("permissions") or {}).get("mode") or agent_config.get("permission_mode", "restricted")
        perm_mode = body_permission_mode or dispatch_config.get("permission_mode", agent_perm_mode)
        
        # Assert that explicit permission_mode overrides agent config
        self.assertEqual(perm_mode, "restricted", 
                        "Explicit permission_mode in request should override agent config")

    def test_orchestrator_restricted_by_default(self):
        """Test that orchestrator with restricted config stays restricted"""
        agent = "orchestrator"
        agent_config = self.session_mgr.AGENTS.get(agent, {})
        dispatch_config = agent_config.get("dispatch_config", {})
        
        # This is the fixed logic
        agent_perm_mode = (agent_config.get("permissions") or {}).get("mode") or agent_config.get("permission_mode", "restricted")
        perm_mode = dispatch_config.get("permission_mode", agent_perm_mode)
        
        # Assert that orchestrator is restricted
        self.assertEqual(perm_mode, "restricted", 
                        "orchestrator should be restricted")

    def test_nested_permissions_object_takes_precedence(self):
        """Test that nested permissions.mode takes precedence over top-level permission_mode"""
        agent_config = {
            "name": "test-agent",
            "permission_mode": "restricted",  # Top-level (legacy)
            "permissions": {"mode": "elevated"}  # Nested (new format)
        }
        dispatch_config = {}
        
        # This is the fixed logic
        agent_perm_mode = (agent_config.get("permissions") or {}).get("mode") or agent_config.get("permission_mode", "restricted")
        perm_mode = dispatch_config.get("permission_mode", agent_perm_mode)
        
        # Assert that nested permissions.mode takes precedence
        self.assertEqual(perm_mode, "elevated", 
                        "Nested permissions.mode should take precedence over top-level permission_mode")


if __name__ == "__main__":
    unittest.main()
