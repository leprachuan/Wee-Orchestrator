
# Fixture for issue #249 tests
import pytest

@pytest.fixture
def mock_agents_config():
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
