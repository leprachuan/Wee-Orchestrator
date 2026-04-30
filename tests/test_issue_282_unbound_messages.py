"""
Regression test for GitHub Issue #282: 
Bug: runtime crashes with unbound local variable 'messages'.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock


class TestIssue282UnboundMessages:
    """Tests for unbound local variable 'messages' bug (#282)"""

    def test_wee_native_method_exists(self):
        """Test that run_wee_native method exists in SessionManager"""
        from agent_manager import SessionManager
        
        manager = SessionManager()
        assert hasattr(manager, 'run_wee_native'), "run_wee_native method should exist"

    def test_wee_load_messages_returns_list(self):
        """Test that _wee_load_messages always returns a list"""
        from agent_manager import SessionManager
        
        manager = SessionManager()
        
        # Test that _wee_load_messages returns a list
        result = manager._wee_load_messages(
            n8n_session_id="test",
            context_prompt="test",
            resume=True
        )
        
        assert isinstance(result, list), "Should return a list"
        assert len(result) >= 0, "List should be valid"

    def test_wee_load_messages_with_no_session(self):
        """Test _wee_load_messages with non-existent session"""
        from agent_manager import SessionManager
        
        manager = SessionManager()
        
        # This should return a fresh messages list
        result = manager._wee_load_messages(
            n8n_session_id="non_existent_session_282",
            context_prompt="Test context",
            resume=True
        )
        
        assert isinstance(result, list), "Should return a list"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
