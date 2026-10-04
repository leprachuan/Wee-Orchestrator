"""HTTP contract regressions for router settings used by the macOS client."""
import json

import pytest
from fastapi.testclient import TestClient

import agent_manager
import llm_router


@pytest.fixture
def router_client(monkeypatch, tmp_path):
    monkeypatch.setenv("API_SHARED_KEY", "router-api-test")
    monkeypatch.setenv("APP_ENV", "DEV")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    config = tmp_path / "agents.json"
    config.write_text(json.dumps({"agents": []}))
    monkeypatch.setenv("AGENT_CONFIG_FILE", str(config))
    monkeypatch.setattr(agent_manager, "_router_config", llm_router.RouterConfig(tmp_path / "router.json"))
    monkeypatch.setattr(agent_manager, "_llm_router", None)
    client = TestClient(agent_manager.create_api_app())
    yield client
    client.close()


def test_issue_506_router_config_requires_auth(router_client):
    assert router_client.get("/api/v1/router-config").status_code == 401


def test_issue_506_router_settings_round_trip(router_client):
    headers = {"Authorization": "Bearer shared_router-api-test"}
    response = router_client.get("/api/v1/router-config", headers=headers)
    assert response.status_code == 200
    config = response.json()["config"]
    config["enabled"] = False
    config["allowlist"] = [{"runtime": "copilot", "model": "auto", "hint": "General tasks"}]
    response = router_client.put("/api/v1/router-config", headers=headers, json={"config": config})
    assert response.status_code == 200, response.text
    assert response.json()["saved"] is True
    assert router_client.get("/api/v1/router-config", headers=headers).json()["config"] == config
    assert router_client.get("/api/v1/router/status", headers=headers).status_code == 200


def test_issue_506_router_settings_reject_invalid_config(router_client):
    response = router_client.put("/api/v1/router-config", headers={"Authorization": "Bearer shared_router-api-test"}, json={"config": {"enabled": True, "brain": {"runtime": "router"}}})
    assert response.status_code == 422
