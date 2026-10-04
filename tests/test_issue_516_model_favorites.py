"""Favorites survive restarts and sort across providers without duplication."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agent_manager
from model_favorites import ModelFavorites


def test_issue_516_persist_order_and_deduplicate(tmp_path):
    path = tmp_path / "config" / "model_favorites.json"
    store = ModelFavorites(path)
    assert store.load()["models"] == []
    store.save([" openrouter/openai/gpt-4.1-mini ", "ollama/qwen3:8b", "ollama/qwen3:8b"])
    assert ModelFavorites(path).load() == {"version": 1, "models": ["openrouter/openai/gpt-4.1-mini", "ollama/qwen3:8b"]}
    store.save([])
    assert ModelFavorites(path).load()["models"] == []


@pytest.mark.parametrize("invalid", [None, "ollama/x", [1], ["unqualified"], ["openrouter/"], ["other/x"], ["ollama/a b"], ["ollama/x"] * 101])
def test_issue_516_reject_bad_model_ids(invalid, tmp_path):
    store = ModelFavorites(tmp_path / "favorites.json")
    with pytest.raises(ValueError):
        store.save(invalid)
    assert not store.path.exists()


def test_issue_516_unavailable_favorite_is_retained(tmp_path):
    store = ModelFavorites(tmp_path / "favorites.json")
    store.save(["ollama/offline-model"])
    assert store.prioritize([{"id": "openrouter/free", "group": "Cloud"}])[0]["favorite"] is False
    assert store.load()["models"] == ["ollama/offline-model"]


def test_issue_516_cross_provider_priority_and_stable_rest(tmp_path):
    store = ModelFavorites(tmp_path / "favorites.json")
    store.save(["openrouter/cloud", "ollama/local"])
    catalog = [{"id": "ollama/other", "group": "Local"}, {"id": "ollama/local", "group": "Local"}, {"id": "openrouter/cloud", "group": "Cloud"}, {"id": "openrouter/other", "group": "Cloud"}, {"id": "openrouter/cloud", "group": "Other"}]
    result = store.prioritize(catalog)
    assert [entry["id"] for entry in result] == ["openrouter/cloud", "ollama/local", "ollama/other", "openrouter/other"]
    assert [entry["group"] for entry in result[:2]] == ["Favorites", "Favorites"]
    assert result[0]["provider_group"] == "Cloud"
    assert catalog[1]["group"] == "Local"


def test_issue_516_failed_atomic_save_keeps_previous_file(monkeypatch, tmp_path):
    store = ModelFavorites(tmp_path / "favorites.json")
    store.save(["ollama/old"])
    monkeypatch.setattr("model_favorites.os.replace", lambda *args: (_ for _ in ()).throw(OSError("write failed")))
    with pytest.raises(OSError):
        store.save(["openrouter/new"])
    assert store.load()["models"] == ["ollama/old"]
    assert list(tmp_path.iterdir()) == [store.path]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("API_SHARED_KEY", "favorites-test")
    monkeypatch.setenv("APP_ENV", "DEV")
    config = tmp_path / "agents.json"
    config.write_text(json.dumps({"agents": []}))
    monkeypatch.setenv("AGENT_CONFIG_FILE", str(config))
    store = ModelFavorites(tmp_path / "favorites.json")
    monkeypatch.setattr(agent_manager, "get_model_favorites", lambda: store)
    monkeypatch.setattr(agent_manager.SessionManager, "get_models_for_runtime", lambda self, runtime: {"Ollama": ["ollama/local"], "OpenRouter": ["openrouter/cloud"]})
    api = TestClient(agent_manager.create_api_app())
    yield api
    api.close()


HEADERS = {"Authorization": "Bearer shared_favorites-test"}


def test_issue_516_http_auth_roundtrip_and_catalog_order(client):
    assert client.get("/api/v1/model-favorites").status_code == 401
    assert client.put("/api/v1/model-favorites", json={"models": []}).status_code == 401
    models = ["openrouter/cloud", "ollama/local"]
    response = client.put("/api/v1/model-favorites", headers=HEADERS, json={"models": models})
    assert response.status_code == 200
    assert client.get("/api/v1/model-favorites", headers=HEADERS).json()["models"] == models
    catalog = client.get("/api/v1/models?runtime=wee", headers=HEADERS).json()["models"]
    assert [entry["id"] for entry in catalog] == models
    assert all(entry["group"] == "Favorites" for entry in catalog)
    # Other runtime catalogs retain their provider groups.
    assert client.get("/api/v1/models?runtime=copilot", headers=HEADERS).json()["models"][0]["group"] == "Ollama"


@pytest.mark.parametrize("body", [[], {"models": ["bad"]}, {"models": None}])
def test_issue_516_http_invalid_update_is_rejected(client, body):
    assert client.put("/api/v1/model-favorites", headers=HEADERS, json=body).status_code == 422


def test_issue_516_corrupt_preferences_do_not_hide_models(client):
    store = agent_manager.get_model_favorites()
    store.path.write_text("[]")
    assert client.get("/api/v1/model-favorites", headers=HEADERS).status_code == 500
    response = client.get("/api/v1/models?runtime=wee", headers=HEADERS)
    assert response.status_code == 200
    assert len(response.json()["models"]) == 2
    assert "favorites_error" in response.json()
    assert store.path.read_text() == "[]"
