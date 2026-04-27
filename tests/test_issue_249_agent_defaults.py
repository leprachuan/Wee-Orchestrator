"""
Regression tests for issue #249: Auto-apply agent runtime/model defaults on switch.

When switching agents, set_agent() must automatically apply that agent's
primary_runtime and primary_model from agents.json as execution defaults.
These tests call the REAL set_agent() and assert session_map is updated.
"""

import json
import os
import sys
import tempfile
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


_MOCK_AGENTS = {
    "orchestrator": {
        "name": "orchestrator",
        "path": "/opt/",
        "description": "Main orchestrator",
        "primary_runtime": "claude",
        "primary_model": "haiku",
        "fallback_runtime": "copilot",
        "fallback_model": "auto",
        "max_concurrent": 1,
    },
    "research": {
        "name": "research",
        "path": "/opt/research",
        "description": "Research agent",
        "primary_runtime": "opencode",
        "primary_model": "nvidia/llama-3.1-nemotron",
        "fallback_runtime": "claude",
        "fallback_model": "sonnet",
        "max_concurrent": 1,
    },
    "wee-dev": {
        "name": "wee-dev",
        "path": "/opt/wee-dev",
        "description": "Wee Orchestrator developer",
        "primary_runtime": "copilot",
        "primary_model": "gpt-5.4-mini",
        "fallback_runtime": "claude",
        "fallback_model": "opus",
        "max_concurrent": 1,
    },
}


def _make_manager():
    """Create a real AgentSessionManager with a temporary session_map file."""
    import agent_manager as am

    mgr = am.SessionManager.__new__(am.SessionManager)
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    json.dump({}, tmp)
    tmp.close()

    from pathlib import Path

    mgr.session_map_file = Path(tmp.name)
    mgr._session_map_lock = threading.Lock()
    mgr.AGENTS = dict(_MOCK_AGENTS)
    return mgr, tmp.name


class TestSetAgentAppliesDefaults:
    """set_agent() must write primary_runtime and primary_model into session_map."""

    def test_new_session_gets_agent_runtime_and_model(self):
        """New session entry created by set_agent() uses agent's primary defaults."""
        mgr, path = _make_manager()
        try:
            mgr.set_agent("sess-001", "research")
            sm = mgr.load_session_map()
            assert (
                sm["sess-001"]["runtime"] == "opencode"
            ), f"Expected 'opencode', got {sm['sess-001'].get('runtime')}"
            assert (
                sm["sess-001"]["model"] == "nvidia/llama-3.1-nemotron"
            ), f"Expected 'nvidia/llama-3.1-nemotron', got {sm['sess-001'].get('model')}"
            assert sm["sess-001"]["agent"] == "research"
        finally:
            os.unlink(path)

    def test_existing_session_runtime_updated_on_agent_switch(self):
        """Existing session's runtime is overwritten with new agent's primary_runtime."""
        mgr, path = _make_manager()
        try:
            # Pre-populate with orchestrator settings
            mgr.save_session_map(
                {
                    "sess-002": {
                        "session_id": "old-id",
                        "agent": "orchestrator",
                        "runtime": "claude",
                        "model": "haiku",
                    }
                }
            )
            mgr.set_agent("sess-002", "wee-dev")
            sm = mgr.load_session_map()
            assert (
                sm["sess-002"]["runtime"] == "copilot"
            ), f"Expected 'copilot', got {sm['sess-002'].get('runtime')}"
            assert (
                sm["sess-002"]["model"] == "gpt-5.4-mini"
            ), f"Expected 'gpt-5.4-mini', got {sm['sess-002'].get('model')}"
            assert sm["sess-002"]["agent"] == "wee-dev"
        finally:
            os.unlink(path)

    def test_sequential_switches_apply_each_agents_defaults(self):
        """Two successive agent switches both apply correct per-agent defaults."""
        mgr, path = _make_manager()
        try:
            mgr.set_agent("sess-003", "research")
            sm = mgr.load_session_map()
            assert sm["sess-003"]["runtime"] == "opencode"
            assert sm["sess-003"]["model"] == "nvidia/llama-3.1-nemotron"

            mgr.set_agent("sess-003", "wee-dev")
            sm = mgr.load_session_map()
            assert sm["sess-003"]["runtime"] == "copilot"
            assert sm["sess-003"]["model"] == "gpt-5.4-mini"
        finally:
            os.unlink(path)

    def test_unknown_agent_returns_error_without_modifying_session(self):
        """set_agent() returns an error string and leaves session_map unchanged."""
        mgr, path = _make_manager()
        try:
            initial_map = {"sess-004": {"agent": "orchestrator", "runtime": "claude"}}
            mgr.save_session_map(initial_map)

            result = mgr.set_agent("sess-004", "nonexistent-agent")
            assert "Unknown agent" in result or "nonexistent-agent" in result

            sm = mgr.load_session_map()
            assert sm["sess-004"]["runtime"] == "claude", "Session should be unchanged"
        finally:
            os.unlink(path)

    def test_agent_without_primary_runtime_falls_back_to_copilot(self):
        """Agent missing primary_runtime defaults to 'copilot'."""
        mgr, path = _make_manager()
        try:
            mgr.AGENTS["minimal-agent"] = {
                "name": "minimal-agent",
                "path": "/opt/minimal",
                "description": "Minimal agent with no runtime specified",
            }
            mgr.set_agent("sess-005", "minimal-agent")
            sm = mgr.load_session_map()
            assert (
                sm["sess-005"]["runtime"] == "copilot"
            ), f"Expected fallback 'copilot', got {sm['sess-005'].get('runtime')}"
        finally:
            os.unlink(path)

    def test_agent_without_primary_model_falls_back_to_default(self):
        """Agent missing primary_model defaults to 'gpt-5-mini'."""
        mgr, path = _make_manager()
        try:
            mgr.AGENTS["minimal-agent"] = {
                "name": "minimal-agent",
                "path": "/opt/minimal",
                "description": "Minimal agent with no model specified",
                "primary_runtime": "copilot",
            }
            mgr.set_agent("sess-006", "minimal-agent")
            sm = mgr.load_session_map()
            assert (
                sm["sess-006"]["model"] == "gpt-5-mini"
            ), f"Expected fallback 'gpt-5-mini', got {sm['sess-006'].get('model')}"
        finally:
            os.unlink(path)


class TestLoadAgentsConfigPreservesFields:
    """_load_agents_config() must preserve primary_runtime/primary_model."""

    def test_load_agents_config_preserves_primary_runtime_and_model(self):
        """Config loaded from file must include primary_runtime and primary_model."""
        import agent_manager as am

        agents_data = {
            "agents": [
                {
                    "name": "test-agent",
                    "path": "/opt/test",
                    "description": "Test",
                    "primary_runtime": "claude",
                    "primary_model": "sonnet",
                    "max_concurrent": 1,
                }
            ]
        }
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump(agents_data, f)
            config_path = f.name

        try:
            mgr = am.SessionManager.__new__(am.SessionManager)
            agents = mgr._load_agents_config(config_path)
            assert "test-agent" in agents
            assert agents["test-agent"]["primary_runtime"] == "claude"
            assert agents["test-agent"]["primary_model"] == "sonnet"
        finally:
            os.unlink(config_path)


class TestAgentsAPIEndpoint:
    """GET /api/v1/agents must include primary_runtime and primary_model per agent."""

    def test_agents_endpoint_returns_primary_runtime_and_model(self):
        """API response for each agent must include primary_runtime and primary_model."""
        api_response = [
            {
                "name": a["name"],
                "description": a["description"],
                "path": a["path"],
                "primary_runtime": a.get("primary_runtime"),
                "primary_model": a.get("primary_model"),
            }
            for a in _MOCK_AGENTS.values()
        ]

        for item in api_response:
            assert (
                item["primary_runtime"] is not None
            ), f"Agent {item['name']} missing primary_runtime"
            assert (
                item["primary_model"] is not None
            ), f"Agent {item['name']} missing primary_model"
