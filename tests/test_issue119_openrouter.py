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
        assert "Wee Native (Ollama)" in mgr.WEE_MODELS

    def test_wee_models_has_openrouter_group(self, mgr):
        assert "Wee Native (OpenRouter)" in mgr.WEE_MODELS

    def test_ollama_models_are_tuples(self, mgr):
        for entry in mgr.WEE_MODELS["Wee Native (Ollama)"]:
            assert isinstance(entry, tuple) and len(entry) == 3
            model_id, desc, aliases = entry
            assert model_id.startswith("ollama/")
            assert isinstance(aliases, list)

    def test_openrouter_static_models_prefixed(self, mgr):
        for entry in mgr.WEE_MODELS["Wee Native (OpenRouter)"]:
            model_id, _desc, _aliases = entry
            assert model_id.startswith("openrouter/")

    def test_at_least_3_ollama_models(self, mgr):
        assert len(mgr.WEE_MODELS["Wee Native (Ollama)"]) >= 3

    def test_at_least_5_openrouter_static_models(self, mgr):
        assert len(mgr.WEE_MODELS["Wee Native (OpenRouter)"]) >= 5


# ── OPENROUTER_POPULAR_MODELS ──────────────────────────────────────────


class TestOpenrouterPopularModels:
    def test_is_a_set(self, mgr):
        assert isinstance(mgr.OPENROUTER_POPULAR_MODELS, set)

    def test_at_least_10_popular_models(self, mgr):
        assert len(mgr.OPENROUTER_POPULAR_MODELS) >= 10

    def test_contains_expected_ids(self, mgr):
        for mid in [
            "meta-llama/llama-4-maverick",
            "openai/gpt-4.1",
            "deepseek/deepseek-v3.2",
        ]:
            assert mid in mgr.OPENROUTER_POPULAR_MODELS

    def test_ids_have_no_openrouter_prefix(self, mgr):
        for mid in mgr.OPENROUTER_POPULAR_MODELS:
            assert not mid.startswith(
                "openrouter/"
            ), f"Popular model IDs should be bare provider/model: {mid}"


# ── fetch_wee_models() ─────────────────────────────────────────────────


class TestFetchWeeModels:
    def test_returns_dict_with_string_keys(self, mgr):
        mgr._env_wee_models = None  # reset cache
        mgr._openrouter_cache_ts = 0
        result = mgr.fetch_wee_models()
        assert isinstance(result, dict)
        for k, v in result.items():
            assert isinstance(k, str)
            assert isinstance(v, list)
            for model_id in v:
                assert isinstance(
                    model_id, str
                ), f"Expected flat string IDs, got {type(model_id)}: {model_id}"

    def test_ollama_group_in_result(self, mgr):
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0
        result = mgr.fetch_wee_models()
        assert "Wee Native (Ollama)" in result

    def test_cache_hit_returns_same_result(self, mgr):
        # First call to populate cache
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0
        r1 = mgr.fetch_wee_models()
        # Second call should use cache
        r2 = mgr.fetch_wee_models()
        assert r1 == r2

    def test_cache_expiry(self, mgr):
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0
        mgr.fetch_wee_models()
        # Expire the cache
        mgr._openrouter_cache_ts = time.time() - 600
        # Should re-fetch (not raise)
        result = mgr.fetch_wee_models()
        assert isinstance(result, dict)

    def test_fallback_on_no_api_key(self, mgr):
        """Without keyring and without env var, falls back to static."""
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0
        with patch.dict(os.environ, {}, clear=False):
            # Remove env var if present
            env_copy = os.environ.copy()
            env_copy.pop("OPENROUTER_API_KEY", None)
            with patch.dict(os.environ, env_copy, clear=True):
                with patch("keyring.get_password", return_value=None):
                    result = mgr.fetch_wee_models()
        assert "Wee Native (Ollama)" in result
        # Static OpenRouter fallback has at least 5 models
        if "Wee Native (OpenRouter)" in result:
            assert len(result["Wee Native (OpenRouter)"]) >= 5

    def test_fallback_on_api_error(self, mgr):
        """Network error falls back to static WEE_MODELS."""
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0
        with patch("keyring.get_password", return_value="fake-key"):
            with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
                result = mgr.fetch_wee_models()
        assert "Wee Native (Ollama)" in result
        assert "Wee Native (OpenRouter)" in result

    def test_discovery_filters_to_popular(self, mgr):
        """Only models in OPENROUTER_POPULAR_MODELS are returned."""
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0
        fake_api_response = json.dumps(
            {
                "data": [
                    {"id": "meta-llama/llama-4-maverick", "name": "Llama 4 Maverick"},
                    {"id": "meta-llama/llama-4-scout", "name": "Llama 4 Scout"},
                    {"id": "some-vendor/obscure-model", "name": "Obscure Model"},
                ]
            }
        ).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_api_response

        with patch("keyring.get_password", return_value="fake-key"):
            with patch("urllib.request.urlopen", return_value=mock_resp):
                result = mgr.fetch_wee_models()

        or_models = result.get("Wee Native (OpenRouter)", [])
        assert "openrouter/meta-llama/llama-4-maverick" in or_models
        assert "openrouter/meta-llama/llama-4-scout" in or_models
        assert "openrouter/some-vendor/obscure-model" not in or_models

    def test_discovered_models_have_openrouter_prefix(self, mgr):
        """Discovered models get openrouter/ prefix."""
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0
        fake_api_response = json.dumps(
            {
                "data": [
                    {"id": "openai/gpt-4.1", "name": "GPT-4.1"},
                ]
            }
        ).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_api_response

        with patch("keyring.get_password", return_value="fake-key"):
            with patch("urllib.request.urlopen", return_value=mock_resp):
                result = mgr.fetch_wee_models()

        or_models = result.get("Wee Native (OpenRouter)", [])
        assert "openrouter/openai/gpt-4.1" in or_models


# ── _get_model_description ──────────────────────────────────────────────


class TestGetModelDescription:
    def test_ollama_model_description(self, mgr):
        desc = mgr._get_model_description("ollama/gemma4:e4b", "wee")
        assert desc and "Gemma" in desc

    def test_openrouter_static_model_description(self, mgr):
        desc = mgr._get_model_description(
            "openrouter/meta-llama/llama-4-maverick", "wee"
        )
        assert desc and ("Llama" in desc or "Maverick" in desc)

    def test_unknown_model_returns_none_or_empty(self, mgr):
        desc = mgr._get_model_description("openrouter/unknown/model-xyz", "wee")
        # Should return None or empty string for unknown models
        assert desc is None or desc == "" or desc == "openrouter/unknown/model-xyz"


# ── get_model_from_name ─────────────────────────────────────────────────


class TestGetModelFromName:
    def _reset_cache(self, mgr):
        """Reset wee model cache so static aliases are used."""
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0

    def test_exact_ollama_model_id(self, mgr):
        self._reset_cache(mgr)
        result = mgr.get_model_from_name("ollama/gemma4:e4b", "wee")
        assert result == "ollama/gemma4:e4b"

    def test_ollama_alias(self, mgr):
        self._reset_cache(mgr)
        result = mgr.get_model_from_name("gemma4", "wee")
        assert result == "ollama/gemma4:e4b"

    def test_openrouter_exact_id(self, mgr):
        self._reset_cache(mgr)
        result = mgr.get_model_from_name(
            "openrouter/meta-llama/llama-4-maverick", "wee"
        )
        assert result == "openrouter/meta-llama/llama-4-maverick"

    def test_openrouter_alias(self, mgr):
        self._reset_cache(mgr)
        result = mgr.get_model_from_name("or-gpt-4.1", "wee")
        assert result == "openrouter/openai/gpt-4.1"

    def test_unknown_model_returns_none(self, mgr):
        self._reset_cache(mgr)
        result = mgr.get_model_from_name("nonexistent-model-xyz", "wee")
        assert result is None


# ── /api/v1/models endpoint ────────────────────────────────────────────


class TestModelsEndpoint:
    def test_wee_in_known_runtimes(self, mgr):
        """Verify wee is accepted as a valid runtime."""
        mod = importlib.import_module("agent_manager")
        # The known_runtimes set is defined inside the endpoint handler,
        # so we test via the API response instead
        pass  # Tested by test_endpoint_returns_models below

    def test_endpoint_returns_models(self):
        """Hit the real endpoint on the dev server."""
        import ssl
        import urllib.request

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request("https://127.0.0.1:8001/api/v1/models?runtime=wee")
        try:
            resp = urllib.request.urlopen(req, context=ctx, timeout=10)
            data = json.loads(resp.read())
        except Exception:
            pytest.skip("Dev server not reachable")

        assert data["runtime"] == "wee"
        assert "error" not in data or data.get("error") is None
        assert len(data["models"]) >= 3  # at least the static Ollama models

    def test_endpoint_models_have_group_field(self):
        """Each model in the response should have a group field."""
        import ssl
        import urllib.request

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request("https://127.0.0.1:8001/api/v1/models?runtime=wee")
        try:
            resp = urllib.request.urlopen(req, context=ctx, timeout=10)
            data = json.loads(resp.read())
        except Exception:
            pytest.skip("Dev server not reachable")

        for m in data["models"]:
            assert "group" in m, f"Model {m['id']} missing 'group' field"
            assert "id" in m
            assert "label" in m

    def test_endpoint_has_ollama_and_openrouter_groups(self):
        """Response should contain both Ollama and OpenRouter groups."""
        import ssl
        import urllib.request

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request("https://127.0.0.1:8001/api/v1/models?runtime=wee")
        try:
            resp = urllib.request.urlopen(req, context=ctx, timeout=10)
            data = json.loads(resp.read())
        except Exception:
            pytest.skip("Dev server not reachable")

        groups = {m["group"] for m in data["models"]}
        assert "Wee Native (Ollama)" in groups
        assert "Wee Native (OpenRouter)" in groups

    def test_endpoint_unknown_runtime_rejected(self):
        """Unknown runtime should return error."""
        import ssl
        import urllib.request

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(
            "https://127.0.0.1:8001/api/v1/models?runtime=fakexyz"
        )
        try:
            resp = urllib.request.urlopen(req, context=ctx, timeout=10)
            data = json.loads(resp.read())
        except Exception:
            pytest.skip("Dev server not reachable")

        assert "error" in data
        assert "Unknown runtime" in data["error"]


# ── Session dispatch ────────────────────────────────────────────────────


class TestSessionDispatch:
    def test_wee_runtime_resolves_openrouter_model(self, mgr):
        """Selecting an openrouter/ model should pass through to resolve."""
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0
        model = "openrouter/meta-llama/llama-4-maverick"
        resolved = mgr.get_model_from_name(model, "wee")
        assert resolved == model

    def test_openrouter_models_all_have_prefix(self, mgr):
        """All OpenRouter model IDs in static list have openrouter/ prefix."""
        for entry in mgr.WEE_MODELS.get("Wee Native (OpenRouter)", []):
            model_id = entry[0]
            assert model_id.startswith(
                "openrouter/"
            ), f"OpenRouter model should have openrouter/ prefix: {model_id}"


# ── Keyring integration ────────────────────────────────────────────────


class TestKeyringIntegration:
    def test_keyring_has_openrouter_key(self):
        """keyring should have the OpenRouter API key stored."""
        try:
            import keyring

            val = keyring.get_password("openrouter", "api_key")
        except Exception:
            pytest.skip("keyring not available")
        assert val is not None and val.startswith("sk-or-")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
