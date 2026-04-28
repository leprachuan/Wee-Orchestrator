"""Tests for wee-tui components - Issue #272"""

from datetime import datetime

import pytest

from tui.api.client import WeeAPIClient
from tui.config import config
from tui.theme import AGENT_COLORS, STATUS_COLORS, get_agent_color, get_status_color


def test_api_client_initialization():
    """Test API client initialization"""
    client = WeeAPIClient(
        base_url="https://127.0.0.1:8001", auth_token="test_token", user_id="12345"
    )
    assert client.base_url == "https://127.0.0.1:8001"
    assert client.auth_token == "test_token"
    assert client.user_id == "12345"


def test_api_client_headers():
    """Test API client headers"""
    client = WeeAPIClient(
        base_url="https://127.0.0.1:8001", auth_token="test_token", user_id="12345", channel="tui"
    )

    assert client.headers["Authorization"] == "Bearer test_token"
    assert client.headers["X-User-Identity"] == "12345"
    assert client.headers["X-Auth-Channel"] == "tui"
    assert "Content-Type" in client.headers


def test_config_defaults():
    """Test configuration defaults"""
    assert config.api_url == "https://127.0.0.1:8001"
    assert config.verify_ssl == False
    assert config.user_identity == "8193231291"
    assert config.update_interval == 1.0
    assert config.max_history_lines == 10000


def test_theme_agent_colors():
    """Test agent color mapping"""
    assert get_agent_color("orchestrator") == "green"
    assert get_agent_color("wee-dev") == "blue"
    assert get_agent_color("wee-qa") == "yellow"
    assert get_agent_color("wee-doc") == "cyan"
    assert get_agent_color("email-triage") == "magenta"
    assert get_agent_color("research") == "white"

    # Test unknown agent defaults to white
    assert get_agent_color("unknown-agent") == "white"


def test_theme_status_colors():
    """Test status color mapping"""
    assert get_status_color("running") == "green"
    assert get_status_color("idle") == "yellow"
    assert get_status_color("completed") == "blue"
    assert get_status_color("error") == "red"
    assert get_status_color("queued") == "white"
    assert get_status_color("stopped") == "red"

    # Test unknown status defaults to white
    assert get_status_color("unknown-status") == "white"


def test_agent_colors_mapping_complete():
    """Test all agent colors are properly defined"""
    expected_agents = [
        "orchestrator",
        "wee-dev",
        "wee-qa",
        "wee-doc",
        "email-triage",
        "family-knowledge",
        "research",
        "devops",
        "smarthome",
    ]
    for agent in expected_agents:
        color = AGENT_COLORS.get(agent)
        assert color is not None, f"Agent {agent} missing from color map"
        assert isinstance(color, str)


def test_status_colors_mapping_complete():
    """Test all status colors are properly defined"""
    expected_statuses = ["running", "idle", "completed", "error", "queued", "stopped"]
    for status in expected_statuses:
        color = STATUS_COLORS.get(status)
        assert color is not None, f"Status {status} missing from color map"
        assert isinstance(color, str)


def test_api_client_context_manager():
    """Test API client context manager interface"""
    client = WeeAPIClient(
        base_url="https://127.0.0.1:8001", auth_token="test_token", user_id="12345"
    )

    # Check that context manager methods exist
    assert hasattr(client, "__aenter__")
    assert hasattr(client, "__aexit__")


def test_api_client_methods_exist():
    """Test that all expected API methods exist"""
    client = WeeAPIClient(
        base_url="https://127.0.0.1:8001", auth_token="test_token", user_id="12345"
    )

    # Check session methods
    assert hasattr(client, "get_sessions")
    assert hasattr(client, "get_session_messages")
    assert hasattr(client, "create_session")

    # Check task methods
    assert hasattr(client, "get_background_tasks")
    assert hasattr(client, "get_background_task")
    assert hasattr(client, "create_background_task")

    # Check config methods
    assert hasattr(client, "get_agents")
    assert hasattr(client, "get_runtimes")
    assert hasattr(client, "get_models")
    assert hasattr(client, "get_service_status")
    assert hasattr(client, "get_health")


def test_api_client_verify_ssl_setting():
    """Test SSL verification setting"""
    client_verify = WeeAPIClient(
        base_url="https://127.0.0.1:8001", auth_token="test_token", user_id="12345", verify_ssl=True
    )
    assert client_verify.verify_ssl == True

    client_no_verify = WeeAPIClient(
        base_url="https://127.0.0.1:8001",
        auth_token="test_token",
        user_id="12345",
        verify_ssl=False,
    )
    assert client_no_verify.verify_ssl == False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
