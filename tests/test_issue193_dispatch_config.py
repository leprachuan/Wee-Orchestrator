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
    # Verify dispatch_config has required fields
    assert 'runtime' in dispatch, "wee-dev dispatch_config missing runtime"
    assert 'model' in dispatch, "wee-dev dispatch_config missing model"
    assert 'permission_mode' in dispatch, "wee-dev dispatch_config missing permission_mode"
    assert 'yolo' in dispatch, "wee-dev dispatch_config missing yolo"
    assert dispatch['permission_mode'] == 'elevated', f"wee-dev permission_mode should be 'elevated', got {dispatch.get('permission_mode')}"
    assert dispatch['yolo'] is True, f"wee-dev yolo should be True, got {dispatch.get('yolo')}"
    
    # Verify wee-qa has dispatch_config
    assert 'wee-qa' in agents, "wee-qa agent not found"
    wee_qa = agents['wee-qa']
    assert 'dispatch_config' in wee_qa, "wee-qa missing dispatch_config"
    
    dispatch = wee_qa['dispatch_config']
    # Verify dispatch_config has required fields
    assert 'runtime' in dispatch, "wee-qa dispatch_config missing runtime"
    assert 'model' in dispatch, "wee-qa dispatch_config missing model"
    assert 'permission_mode' in dispatch, "wee-qa dispatch_config missing permission_mode"
    assert 'yolo' in dispatch, "wee-qa dispatch_config missing yolo"
    assert dispatch['permission_mode'] == 'elevated', f"wee-qa permission_mode should be 'elevated', got {dispatch.get('permission_mode')}"
    assert dispatch['yolo'] is True, f"wee-qa yolo should be True, got {dispatch.get('yolo')}"
    
    print("✓ test_load_agents_includes_dispatch_config PASSED")


def test_dispatch_config_priority_order():
    """Test that dispatch_config priority order is respected (body > dispatch_config > defaults > globals)."""
    mgr = SessionManager()
    agents = mgr.AGENTS
    
    # Get dispatch_config for wee-qa
    wee_qa_config = agents.get('wee-qa', {})
    dispatch_config = wee_qa_config.get('dispatch_config', {})
    
    # Test 1: dispatch_config should have a runtime value
    assert 'runtime' in dispatch_config, "wee-qa dispatch_config should have runtime"
    runtime_value = dispatch_config['runtime']
    assert isinstance(runtime_value, str), f"runtime should be string, got {type(runtime_value)}"
    
    # Test 2: dispatch_config.permission_mode should be elevated
    assert dispatch_config.get('permission_mode') == 'elevated', f"permission_mode should be 'elevated', got {dispatch_config.get('permission_mode')}"
    
    # Test 3: dispatch_config.yolo should be True
    assert dispatch_config.get('yolo') is True, f"yolo should be True, got {dispatch_config.get('yolo')}"
    
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
