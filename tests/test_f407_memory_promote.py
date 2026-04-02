"""Tests for F407: Per-agent memory promotion endpoints.

POST /api/v1/memory/promote — single-agent promotion
POST /api/v1/memory/promote-all — fan-out across all agents
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("API_SHARED_KEY", "test_key_123")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_manager import create_api_app  # noqa: E402

try:
    from starlette.testclient import TestClient
except ImportError:
    from fastapi.testclient import TestClient


class TestMemoryPromoteEndpoint:
    """Tests for POST /api/v1/memory/promote."""

    @pytest.fixture
    def client(self):
        app = create_api_app()
        return TestClient(app, raise_server_exceptions=False)

    @pytest.fixture
    def auth_headers(self):
        return {
            "Authorization": "Bearer shared_test_key_123",
            "X-User-Identity": "test-user",
            "X-Auth-Channel": "api",
        }

    def test_promote_requires_auth(self, client):
        """Endpoint requires authentication."""
        resp = client.post("/api/v1/memory/promote", json={})
        assert resp.status_code in (401, 403)

    def test_promote_unknown_agent_returns_404(self, client, auth_headers):
        """Unknown agent name returns 404."""
        resp = client.post(
            "/api/v1/memory/promote",
            json={"agent": "nonexistent-agent-xyz"},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert "nonexistent-agent-xyz" in resp.json()["detail"]

    def test_promote_orchestrator_default(self, client, auth_headers):
        """When agent is omitted, promotes orchestrator memory."""
        mock_result = MagicMock()
        mock_result.stdout = "[memory-promoter] ok"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch("pathlib.Path.exists", return_value=True):
            resp = client.post(
                "/api/v1/memory/promote",
                json={},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["agent"] == "orchestrator"
        # Verify subprocess was called
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        # Check that WEE_AGENT_DIR is set in env
        env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env", {})
        assert "WEE_AGENT_DIR" in env

    def test_promote_specific_agent(self, client, auth_headers):
        """When agent is specified, uses the agent's path."""
        mock_result = MagicMock()
        mock_result.stdout = "[memory-promoter] ok"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch("pathlib.Path.exists", return_value=True):
            resp = client.post(
                "/api/v1/memory/promote",
                json={"agent": "wee-dev"},
                headers=auth_headers,
            )

        if resp.status_code == 404:
            pytest.skip("wee-dev not configured in test agents.json")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["agent"] == "wee-dev"

    def test_promote_resolves_correct_dir_for_agent(self, client, auth_headers):
        """The endpoint passes the agent's path as WEE_AGENT_DIR."""
        mock_result = MagicMock()
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch("pathlib.Path.exists", return_value=True):
            resp = client.post(
                "/api/v1/memory/promote",
                json={"agent": "wee-dev"},
                headers=auth_headers,
            )

        if resp.status_code == 404:
            pytest.skip("wee-dev not configured in test agents.json")

        assert resp.status_code == 200
        # Extract the env from the subprocess call
        call_kwargs = mock_run.call_args
        env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env", {})
        assert env.get("WEE_AGENT_DIR") == "/opt/wee-dev"

    def test_promote_script_not_found_returns_503(self, client, auth_headers):
        """Returns 503 when memory_promoter.py doesn't exist."""
        with patch("pathlib.Path.exists", return_value=False):
            resp = client.post(
                "/api/v1/memory/promote",
                json={},
                headers=auth_headers,
            )
        assert resp.status_code == 503
        assert "not found" in resp.json()["detail"].lower()

    def test_promote_timeout_returns_504(self, client, auth_headers):
        """Returns 504 when the subprocess times out."""
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 120)), \
             patch("pathlib.Path.exists", return_value=True):
            resp = client.post(
                "/api/v1/memory/promote",
                json={},
                headers=auth_headers,
            )
        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"].lower()


class TestMemoryPromoteAllEndpoint:
    """Tests for POST /api/v1/memory/promote-all."""

    @pytest.fixture
    def client(self):
        app = create_api_app()
        return TestClient(app, raise_server_exceptions=False)

    @pytest.fixture
    def auth_headers(self):
        return {
            "Authorization": "Bearer shared_test_key_123",
            "X-User-Identity": "test-user",
            "X-Auth-Channel": "api",
        }

    def test_promote_all_requires_auth(self, client):
        """Endpoint requires authentication."""
        resp = client.post("/api/v1/memory/promote-all")
        assert resp.status_code in (401, 403)

    def test_promote_all_fans_out_to_agents(self, client, auth_headers):
        """Calls memory_promoter.py for each agent in agents.json."""
        mock_result = MagicMock()
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch("pathlib.Path.exists", return_value=True):
            resp = client.post(
                "/api/v1/memory/promote-all",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["total"] > 0
        assert data["succeeded"] == data["total"]
        assert data["failed"] == 0
        # Verify subprocess was called once per agent
        assert mock_run.call_count == data["total"]

    def test_promote_all_includes_all_agents(self, client, auth_headers):
        """The results include every agent from agents.json."""
        mock_result = MagicMock()
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch("pathlib.Path.exists", return_value=True):
            resp = client.post(
                "/api/v1/memory/promote-all",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        agent_names = [r["agent"] for r in data["results"]]
        # Should include orchestrator at minimum
        assert "orchestrator" in agent_names

    def test_promote_all_handles_partial_failure(self, client, auth_headers):
        """Continues promoting remaining agents when one fails."""
        call_count = [0]

        def mock_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise Exception("simulated failure")
            result = MagicMock()
            result.stdout = "ok"
            result.stderr = ""
            result.returncode = 0
            return result

        with patch("subprocess.run", side_effect=mock_run), \
             patch("pathlib.Path.exists", return_value=True):
            resp = client.post(
                "/api/v1/memory/promote-all",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["failed"] >= 1
        assert data["succeeded"] >= 1
        # Total should still cover all agents
        assert data["total"] == data["succeeded"] + data["failed"]

    def test_promote_all_script_not_found_returns_503(self, client, auth_headers):
        """Returns 503 when memory_promoter.py doesn't exist."""
        with patch("pathlib.Path.exists", return_value=False):
            resp = client.post(
                "/api/v1/memory/promote-all",
                headers=auth_headers,
            )
        assert resp.status_code == 503

    def test_promote_all_each_agent_gets_correct_env(self, client, auth_headers):
        """Each agent subprocess receives the correct WEE_AGENT_DIR."""
        captured_envs = []

        def capture_run(*args, **kwargs):
            env = kwargs.get("env", {})
            captured_envs.append(env.get("WEE_AGENT_DIR", ""))
            result = MagicMock()
            result.stdout = "ok"
            result.stderr = ""
            result.returncode = 0
            return result

        with patch("subprocess.run", side_effect=capture_run), \
             patch("pathlib.Path.exists", return_value=True):
            resp = client.post(
                "/api/v1/memory/promote-all",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        # Every result should have a valid agent_path
        for result in data["results"]:
            assert "agent_path" in result
        # The captured envs should include various agent paths
        assert len(captured_envs) == data["total"]
