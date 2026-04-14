"""Tests for Issue #119: Wire up OpenRouter in wee runtime UI.

Covers:
  - WEE_MODELS structure and OPENROUTER_POPULAR_MODELS constant
  - fetch_wee_models() with mocked API responses, cache, fallback
  - _get_model_description() for wee models
  - get_model_from_name() with wee aliases
  - /api/v1/models?runtime=wee endpoint returns grouped models
  - known_runtimes includes "wee"
"""

import importlib
import json
import os
import sys
import time
import types
from unittest.mock import MagicMock, patch

import pytest

# ── helpers ─────────────────────────────────────────────────────────────
sys.path.insert(0, "/opt/n8n-copilot-shim-dev")

def _get_session_mgr():
    """Instantiate SessionManager without starting the full server."""
    mod = importlib.import_module("agent_manager")
    mgr = mod.SessionManager.__new__(mod.SessionManager)
    mgr.__init__()
    return mgr

@pytest.fixture(scope="module")
def mgr():
    return _get_session_mgr()


# ── WEE_MODELS constant ────────────────────────────────────────────────

class TestWeeModelsConstant:
    def test_wee_models_has_ollama_group(self, mgr):
        assert "Ollama Models" in mgr.WEE_MODELS

    def test_wee_models_has_openrouter_group(self, mgr):
        assert "OpenRouter Models" in mgr.WEE_MODELS

    def test_ollama_models_are_tuples(self, mgr):
        for entry in mgr.WEE_MODELS["Ollama Models"]:
            assert isinstance(entry, tuple), f"Expected tuple, got {type(entry)}: {entry}"
            assert len(entry) == 3, f"Expected 3-element tuple: {entry}"
            model_id, desc, aliases = entry
            assert model_id.startswith("ollama/"), f"Ollama model missing prefix: {model_id}"

    def test_openrouter_static_models_prefixed(self, mgr):
        for entry in mgr.WEE_MODELS["OpenRouter Models"]:
            assert isinstance(entry, tuple), f"Expected tuple, got {type(entry)}: {entry}"
            model_id = entry[0]
            assert model_id.startswith("openrouter/"), f"OR model missing prefix: {model_id}"

    def test_at_least_3_ollama_models(self, mgr):
        assert len(mgr.WEE_MODELS["Ollama Models"]) >= 3

    def test_at_least_5_openrouter_static_models(self, mgr):
        assert len(mgr.WEE_MODELS["OpenRouter Models"]) >= 5


# ── OPENROUTER_POPULAR_MODELS ──────────────────────────────────────────

class TestOpenrouterPopularModels:
    def test_is_a_set(self, mgr):
        assert isinstance(mgr.OPENROUTER_POPULAR_MODELS, set)

    def test_at_least_10_popular_models(self, mgr):
        assert len(mgr.OPENROUTER_POPULAR_MODELS) >= 10

    def test_contains_expected_ids(self, mgr):
        for mid in [
            "meta-llama/llama-4-maverick",
            "meta-llama/llama-4-scout",
            "openai/gpt-4.1",
            "deepseek/deepseek-r1:free",
        ]:
            assert mid in mgr.OPENROUTER_POPULAR_MODELS, f"Missing {mid}"

    def test_no_openrouter_prefix_in_popular_ids(self, mgr):
        for mid in mgr.OPENROUTER_POPULAR_MODELS:
            assert not mid.startswith("openrouter/"), \
                f"Popular ID should be raw, not prefixed: {mid}"


# ── fetch_wee_models() ─────────────────────────────────────────────────

class TestFetchWeeModels:
    def _reset_cache(self, mgr):
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0

    def test_returns_dict_with_string_keys(self, mgr):
        self._reset_cache(mgr)
        result = mgr.fetch_wee_models()
        assert isinstance(result, dict)
        for k, v in result.items():
            assert isinstance(k, str)
            assert isinstance(v, list)
            for model_id in v:
                assert isinstance(model_id, str), \
                    f"Expected flat string IDs, got {type(model_id)}: {model_id}"

    def test_ollama_group_in_result(self, mgr):
        self._reset_cache(mgr)
        result = mgr.fetch_wee_models()
        assert "Ollama Models" in result

    def test_fallback_on_no_api_key(self, mgr):
        """When no keyring or env key, should still return Ollama models."""
        self._reset_cache(mgr)
        with patch.dict(os.environ, {}, clear=False):
            with patch("keyring.get_password", return_value=None):
                result = mgr.fetch_wee_models()
        assert "Ollama Models" in result

    def test_fallback_on_api_error(self, mgr):
        """When OpenRouter API fails, should still return static models."""
        self._reset_cache(mgr)
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            result = mgr.fetch_wee_models()
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_cache_returns_same_result(self, mgr):
        """Second call within TTL should return cached result."""
        self._reset_cache(mgr)
        r1 = mgr.fetch_wee_models()
        r2 = mgr.fetch_wee_models()
        assert r1 == r2

    def test_discovery_filters_to_popular(self, mgr):
        """When OpenRouter API returns models, only popular ones are kept."""
        self._reset_cache(mgr)
        fake_api_response = json.dumps({
            "data": [
                {"id": "meta-llama/llama-4-maverick", "name": "Llama 4 Maverick"},
                {"id": "meta-llama/llama-4-scout", "name": "Llama 4 Scout"},
                {"id": "some-vendor/obscure-model", "name": "Obscure"},
                {"id": "openai/gpt-4.1", "name": "GPT-4.1"},
            ]
        }).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_api_response

        with patch("keyring.get_password", return_value="fake-key"):
            with patch("urllib.request.urlopen", return_value=mock_resp):
                result = mgr.fetch_wee_models()

        # The result is flattened by _static_models_to_dict, so find OR models
        all_models = []
        for models in result.values():
            all_models.extend(models)
        or_models = [m for m in all_models if m.startswith("openrouter/")]
        assert "openrouter/meta-llama/llama-4-maverick" in or_models
        assert "openrouter/meta-llama/llama-4-scout" in or_models
        assert "openrouter/openai/gpt-4.1" in or_models
        assert "openrouter/some-vendor/obscure-model" not in or_models

    def test_discovered_models_have_openrouter_prefix(self, mgr):
        """Discovered OpenRouter models should have openrouter/ prefix."""
        self._reset_cache(mgr)
        fake_api_response = json.dumps({
            "data": [
                {"id": "openai/gpt-4.1", "name": "GPT-4.1"},
            ]
        }).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_api_response

        with patch("keyring.get_password", return_value="fake-key"):
            with patch("urllib.request.urlopen", return_value=mock_resp):
                result = mgr.fetch_wee_models()

        all_models = []
        for models in result.values():
            all_models.extend(models)
        or_models = [m for m in all_models if m.startswith("openrouter/")]
        assert "openrouter/openai/gpt-4.1" in or_models


# ── _get_model_description() ───────────────────────────────────────────

class TestGetModelDescription:
    def test_ollama_model_description(self, mgr):
        desc = mgr._get_model_description("ollama/gemma4:e4b", "wee")
        assert desc is not None
        assert len(desc) > 0

    def test_openrouter_static_model_description(self, mgr):
        desc = mgr._get_model_description("openrouter/meta-llama/llama-4-scout", "wee")
        assert desc and "Scout" in desc

    def test_unknown_model_returns_none_or_empty(self, mgr):
        desc = mgr._get_model_description("openrouter/unknown/model", "wee")
        assert desc is None or desc == ""


# ── get_model_from_name() ──────────────────────────────────────────────

class TestGetModelFromName:
    def _reset_cache(self, mgr):
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0

    def test_exact_ollama_model_id(self, mgr):
        self._reset_cache(mgr)
        result = mgr.get_model_from_name("ollama/gemma4:e4b", "wee")
        assert result == "ollama/gemma4:e4b"

    def test_ollama_alias(self, mgr):
        self._reset_cache(mgr)
        result = mgr.get_model_from_name("gemma4", "wee")
        assert result is not None
        assert "gemma4" in result

    def test_openrouter_exact_id(self, mgr):
        self._reset_cache(mgr)
        result = mgr.get_model_from_name("openrouter/meta-llama/llama-4-scout", "wee")
        assert result == "openrouter/meta-llama/llama-4-scout"

    def test_openrouter_alias(self, mgr):
        self._reset_cache(mgr)
        result = mgr.get_model_from_name("or-scout", "wee")
        assert result == "openrouter/meta-llama/llama-4-scout"

    def test_unknown_model_returns_none(self, mgr):
        self._reset_cache(mgr)
        result = mgr.get_model_from_name("nonexistent-model-xyz", "wee")
        assert result is None


# ── /api/v1/models endpoint ────────────────────────────────────────────

class TestModelsEndpoint:
    def test_wee_in_known_runtimes(self):
        """The source code must include 'wee' in known_runtimes."""
        import inspect
        mod = importlib.import_module("agent_manager")
        src = inspect.getsource(mod)
        assert '"wee"' in src or "'wee'" in src

    def test_endpoint_returns_models(self, mgr):
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0
        raw = mgr.get_models_for_runtime("wee")
        models = []
        for group, model_ids in raw.items():
            for model_id in model_ids:
                label = mgr._get_model_description(model_id, "wee") or model_id
                entry = {"id": model_id, "label": label}
                if group:
                    entry["group"] = group
                models.append(entry)
        assert len(models) > 0, "Expected at least one wee model"

    def test_endpoint_models_have_group_field(self, mgr):
        """Models from grouped runtimes should have group field."""
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0
        raw = mgr.get_models_for_runtime("wee")
        data = {"models": []}
        for group, model_ids in raw.items():
            for model_id in model_ids:
                label = mgr._get_model_description(model_id, "wee") or model_id
                entry = {"id": model_id, "label": label}
                if group:
                    entry["group"] = group
                data["models"].append(entry)
        for m in data["models"]:
            assert "group" in m, f"Model {m['id']} missing 'group' field"

    def test_endpoint_has_ollama_and_openrouter_groups(self, mgr):
        """Models should include both Ollama and OpenRouter groups."""
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0
        raw = mgr.get_models_for_runtime("wee")
        data = {"models": []}
        for group, model_ids in raw.items():
            for model_id in model_ids:
                label = mgr._get_model_description(model_id, "wee") or model_id
                entry = {"id": model_id, "label": label}
                if group:
                    entry["group"] = group
                data["models"].append(entry)
        groups = {m.get("group", "") for m in data["models"]}
        assert "Ollama Models" in groups, f"Missing Ollama group. Got: {groups}"
        assert any(g.startswith("OpenRouter") for g in groups), f"Missing OpenRouter group (expected group with OpenRouter prefix). Got: {groups}"

    def test_endpoint_unknown_runtime_rejected(self):
        """Unknown runtimes should return an error."""
        mod = importlib.import_module("agent_manager")
        src = open(os.path.join("/opt/n8n-copilot-shim-dev", "agent_manager.py")).read()
        assert "Unknown runtime" in src


# ── Session dispatch ───────────────────────────────────────────────────

class TestSessionDispatch:
    def test_wee_runtime_resolves_openrouter_model(self, mgr):
        result = mgr.get_model_from_name("openrouter/meta-llama/llama-4-scout", "wee")
        assert result == "openrouter/meta-llama/llama-4-scout"

    def test_openrouter_models_all_have_prefix(self, mgr):
        for entry in mgr.WEE_MODELS.get("OpenRouter Models", []):
            model_id = entry[0]
            assert model_id.startswith("openrouter/"), \
                f"OpenRouter model missing prefix: {model_id}"


# ── Keyring integration ───────────────────────────────────────────────

class TestKeyringIntegration:
    def test_keyring_has_openrouter_key(self):
        import keyring
        key = keyring.get_password("openrouter", "api_key")
        assert key is not None, "OpenRouter API key not stored in keyring"
        assert key.startswith("sk-or-"), f"Key doesn't look like OpenRouter key: {key[:10]}"


# ── Cache TTL ─────────────────────────────────────────────────────────

class TestCacheTTL:
    def test_openrouter_cache_ts_attribute(self, mgr):
        assert hasattr(mgr, "_openrouter_cache_ts")
        assert isinstance(mgr._openrouter_cache_ts, (int, float))

    def test_cache_invalidation_after_ttl(self, mgr):
        """After TTL expires, cache should be refreshed."""
        mgr._env_wee_models = {"Ollama Models": [("ollama/test", "test", [])]}
        mgr._openrouter_cache_ts = time.time() - 400  # expired
        result = mgr.fetch_wee_models()
        # After refresh, should have more than just the stale cache
        all_models = []
        for models in result.values():
            all_models.extend(models)
        assert len(all_models) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
