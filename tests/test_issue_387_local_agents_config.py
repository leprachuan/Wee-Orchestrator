"""Regression test for issue #387: API config must honor AGENT_CONFIG_FILE."""

from pathlib import Path

from agent_manager import _resolved_agents_config_path


class _SessionManagerStub:
    def __init__(self, path):
        self._agents_config_path = path


def test_api_agents_config_uses_session_manager_config_path(tmp_path):
    """A local API must not fall back to the repository's bundled agents.json."""
    local_config = tmp_path / "local-agents.json"
    manager = _SessionManagerStub(local_config)

    assert _resolved_agents_config_path(manager) == local_config


def test_api_agents_config_falls_back_to_repository_config_when_unset():
    """Existing deployments retain the script-local config default."""
    manager = _SessionManagerStub(None)

    assert _resolved_agents_config_path(manager).name == "agents.json"
    assert isinstance(_resolved_agents_config_path(manager), Path)
