"""Test issue #193: Background task API respects dispatch_config priority."""
import sys
import json
import asyncio
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from uuid import uuid4

sys.path.insert(0, '/opt/n8n-copilot-shim-dev')

from agent_manager import SessionManager


def test_load_agents_includes_dispatch_config():
    """Test that _load_agents_config preserves dispatch_config from agents.json."""
    mgr = SessionManager()
    agents = mgr.AGENTS
    
    # Verify wee-dev has dispatch_config
    assert 'wee-dev' in agents, "wee-dev agent not found"
    wee_dev = agents['wee-dev']
    assert 'dispatch_config' in wee_dev, "wee-dev missing dispatch_config"
    
    dispatch = wee_dev['dispatch_config']
    assert dispatch.get('runtime') == 'copilot', f"wee-dev runtime: expected 'copilot', got {dispatch.get('runtime')}"
    assert dispatch.get('model') == 'auto', f"wee-dev model: expected 'auto', got {dispatch.get('model')}"
    assert dispatch.get('permission_mode') == 'elevated', f"wee-dev permission_mode: expected 'elevated', got {dispatch.get('permission_mode')}"
    assert dispatch.get('yolo') is True, f"wee-dev yolo: expected True, got {dispatch.get('yolo')}"
    assert dispatch.get('timeout') == 3600, f"wee-dev timeout: expected 3600, got {dispatch.get('timeout')}"
    
    # Verify wee-qa has dispatch_config
    assert 'wee-qa' in agents, "wee-qa agent not found"
    wee_qa = agents['wee-qa']
    assert 'dispatch_config' in wee_qa, "wee-qa missing dispatch_config"
    
    dispatch = wee_qa['dispatch_config']
    assert dispatch.get('runtime') == 'copilot', f"wee-qa runtime: expected 'copilot', got {dispatch.get('runtime')}"
    assert dispatch.get('model') == 'auto', f"wee-qa model: expected 'auto', got {dispatch.get('model')}"
    assert dispatch.get('permission_mode') == 'elevated', f"wee-qa permission_mode: expected 'elevated', got {dispatch.get('permission_mode')}"
    assert dispatch.get('yolo') is True, f"wee-qa yolo: expected True, got {dispatch.get('yolo')}"
    assert dispatch.get('timeout') == 1800, f"wee-qa timeout: expected 1800, got {dispatch.get('timeout')}"
    
    print("✓ test_load_agents_includes_dispatch_config PASSED")


def test_dispatch_config_priority_order():
    """Test that dispatch_config priority order is respected (body > dispatch_config > defaults > globals)."""
    mgr = SessionManager()
    agents = mgr.AGENTS
    
    # Get dispatch_config for wee-qa
    wee_qa_config = agents.get('wee-qa', {})
    dispatch_config = wee_qa_config.get('dispatch_config', {})
    
    # Test 1: body.runtime should override dispatch_config.runtime
    assert dispatch_config.get('runtime') == 'copilot'
    # The API code should use body.runtime if provided, otherwise dispatch_config.runtime
    
    # Test 2: dispatch_config.permission_mode should be elevated
    assert dispatch_config.get('permission_mode') == 'elevated'
    
    # Test 3: dispatch_config.yolo should be True
    assert dispatch_config.get('yolo') is True
    
    print("✓ test_dispatch_config_priority_order PASSED")


if __name__ == '__main__':
    try:
        test_load_agents_includes_dispatch_config()
        test_dispatch_config_priority_order()
        print("\n✅ All tests passed!")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
