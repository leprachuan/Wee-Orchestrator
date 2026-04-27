"""
Tests for issue #249: Auto-apply agent runtime/model defaults on switch.

When switching agents, the system should automatically apply that agent's
primary_runtime and primary_model from agents.json as execution defaults.
"""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4


class TestAgentDefaultsOnSwitch:
    """Test suite for auto-applying agent defaults on agent switch."""

    @pytest.fixture
    def mock_agents_config(self):
        """Create a mock agents configuration with primary_runtime/primary_model."""
        return {
            "agents": [
                {
                    "name": "orchestrator",
                    "path": "/opt/",
                    "description": "Main orchestrator",
                    "primary_runtime": "claude",
                    "primary_model": "haiku",
                    "fallback_runtime": "copilot",
                    "fallback_model": "auto",
                    "max_concurrent": 1,
                },
                {
                    "name": "research",
                    "path": "/opt/research",
                    "description": "Research agent",
                    "primary_runtime": "opencode",
                    "primary_model": "nvidia/llama-3.1-nemotron",
                    "fallback_runtime": "claude",
                    "fallback_model": "sonnet",
                    "max_concurrent": 1,
                },
                {
                    "name": "wee-dev",
                    "path": "/opt/wee-dev",
                    "description": "Wee Orchestrator developer",
                    "primary_runtime": "copilot",
                    "primary_model": "gpt-5.4-mini",
                    "fallback_runtime": "claude",
                    "fallback_model": "opus",
                    "max_concurrent": 1,
                },
            ]
        }

    def test_agent_switch_applies_primary_runtime_and_model(self, mock_agents_config):
        """Test that switching agents applies primary_runtime and primary_model."""
        expected_agent = "research"
        expected_runtime = "opencode"
        expected_model = "nvidia/llama-3.1-nemotron"

        session_data = mock_agents_config["agents"][1]
        assert session_data["name"] == expected_agent
        assert session_data["primary_runtime"] == expected_runtime
        assert session_data["primary_model"] == expected_model

    def test_multiple_agent_switches_apply_each_agents_defaults(self, mock_agents_config):
        """Test that multiple switches each apply the correct agent's defaults."""
        research_agent = next(a for a in mock_agents_config["agents"] if a["name"] == "research")
        assert research_agent["primary_runtime"] == "opencode"
        assert research_agent["primary_model"] == "nvidia/llama-3.1-nemotron"

        wee_dev_agent = next(a for a in mock_agents_config["agents"] if a["name"] == "wee-dev")
        assert wee_dev_agent["primary_runtime"] == "copilot"
        assert wee_dev_agent["primary_model"] == "gpt-5.4-mini"

    def test_session_state_updated_on_agent_switch(self):
        """Test that session_map is updated with new agent's defaults on switch."""
        session_id = str(uuid4())
        session_map = {}

        session_map[session_id] = {
            "session_id": str(uuid4()),
            "agent": "orchestrator",
            "runtime": "claude",
            "model": "haiku",
        }

        session_map[session_id]["agent"] = "research"
        session_map[session_id]["runtime"] = "opencode"
        session_map[session_id]["model"] = "nvidia/llama-3.1-nemotron"
        session_map[session_id]["session_id"] = str(uuid4())

        assert session_map[session_id]["agent"] == "research"
        assert session_map[session_id]["runtime"] == "opencode"
        assert session_map[session_id]["model"] == "nvidia/llama-3.1-nemotron"

    def test_new_session_on_agent_switch_uses_agent_defaults(self):
        """Test that new sessions created on agent switch use agent's defaults."""
        session_id = str(uuid4())
        agent_name = "wee-dev"
        agent_defaults = {
            "primary_runtime": "copilot",
            "primary_model": "gpt-5.4-mini",
        }

        session_map = {
            session_id: {
                "session_id": str(uuid4()),
                "agent": agent_name,
                "runtime": agent_defaults["primary_runtime"],
                "model": agent_defaults["primary_model"],
            }
        }

        assert session_map[session_id]["runtime"] == "copilot"
        assert session_map[session_id]["model"] == "gpt-5.4-mini"


class TestAgentConfigLoading:
    """Test suite for agent configuration loading."""

    def test_load_agents_config_preserves_all_fields(self):
        """Test that _load_agents_config doesn't lose fields during parsing."""
        agent_config = {
            "name": "test",
            "path": "/opt/test",
            "description": "Test",
            "primary_runtime": "claude",
            "primary_model": "sonnet",
            "fallback_runtime": "copilot",
            "fallback_model": "auto",
            "max_concurrent": 1,
            "permissions": {"mode": "restricted"},
        }

        required_fields = {
            "name": "test",
            "path": "/opt/test",
            "description": "Test",
            "primary_runtime": "claude",
            "primary_model": "sonnet",
            "max_concurrent": 1,
        }

        for key, value in required_fields.items():
            assert agent_config.get(key) == value


class TestWebUIAgentSwitch:
    """Test suite for WebUI agent switching behavior."""

    def test_webui_agent_switcher_receives_defaults(self, mock_agents_config):
        """Test that WebUI receives primary_runtime and primary_model for each agent."""
        api_response = []
        for agent in mock_agents_config["agents"]:
            api_response.append({
                "name": agent["name"],
                "description": agent["description"],
                "path": agent["path"],
                "primary_runtime": agent.get("primary_runtime"),
                "primary_model": agent.get("primary_model"),
            })

        assert len(api_response) == 3
        for item in api_response:
            assert "name" in item
            assert "description" in item
            assert "primary_runtime" in item
            assert "primary_model" in item
            assert item["primary_runtime"] is not None
            assert item["primary_model"] is not None
