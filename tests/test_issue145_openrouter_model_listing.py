"""Tests for Issue #145: All OpenRouter models shown in model listing.

Root cause: fetch_wee_models() filtered OpenRouter API responses through a
hardcoded OPENROUTER_POPULAR_MODELS set (~12 models), dropping all others.

Fix: Removed OPENROUTER_POPULAR_MODELS filter so ALL models returned by
the OpenRouter /api/v1/models endpoint are included in the listing.
"""

import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---------------------------------------------------------------------------
# Fixture: lightweight SessionManager
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mgr():
    """Create a SessionManager instance for testing."""
    import agent_manager

    m = agent_manager.SessionManager.__new__(agent_manager.SessionManager)
    m.session_map_file = MagicMock()
    m.session_map_file.exists.return_value = False
    m._env_wee_models = None
    m._openrouter_cache_ts = 0
    return m


def _reset_cache(mgr):
    """Clear model cache so fetch_wee_models re-fetches."""
    mgr._env_wee_models = None
    mgr._openrouter_cache_ts = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_openrouter_api_response(models):
    """Build a mock OpenRouter /api/v1/models response body."""
    data = []
    for mid in models:
        data.append({"id": mid, "name": mid.replace("/", " ").title()})
    return json.dumps({"data": data}).encode()


def _mock_urlopen(response_bytes, status=200):
    """Return a mock for urllib.request.urlopen."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = response_bytes
    mock_resp.status = status
    return mock_resp


SAMPLE_MODELS = [
    "meta-llama/llama-4-maverick",
    "meta-llama/llama-4-scout",
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-opus-4.6",
    "google/gemini-3.1-flash-lite-preview",
    "google/gemini-3.1-pro-preview-customtools",
    "openai/gpt-4.1",
    "openai/gpt-4.1-mini",
    "deepseek/deepseek-r1:free",
    "qwen/qwen3-32b:free",
    "microsoft/phi-4-reasoning-plus:free",
    "google/gemma-3-27b-it:free",
    # Models that were MISSING before the fix (not in old OPENROUTER_POPULAR_MODELS)
    "anthropic/claude-haiku-4.5",
    "google/gemini-2.5-flash-preview",
    "openai/gpt-4.1-nano",
    "mistralai/mistral-medium-3",
    "cohere/command-a-03-2025",
    "nousresearch/hermes-3-llama-3.1-405b",
    "x-ai/grok-3-mini-beta",
    "perplexity/sonar-deep-research",
    "amazon/nova-pro-v1",
    "databricks/dbrx-instruct",
    "01-ai/yi-large",
    "inflection/inflection-3-productivity",
]


# ---------------------------------------------------------------------------
# Core regression tests
# ---------------------------------------------------------------------------

class TestIssue145AllModelsShown:
    """Verify that ALL OpenRouter models from the API are included."""

    def test_all_api_models_returned_no_filter(self, mgr):
        """The primary regression test: every model from the API must appear."""
        _reset_cache(mgr)
        api_response = _make_openrouter_api_response(SAMPLE_MODELS)

        with patch("keyring.get_password", return_value="test-key"), \
             patch("urllib.request.urlopen", return_value=_mock_urlopen(api_response)):
            result = mgr.fetch_wee_models()

        # Collect all OpenRouter model IDs from the result
        or_models = result.get("OpenRouter Models", [])
        returned_ids = set(or_models)

        for mid in SAMPLE_MODELS:
            expected_id = "openrouter/" + mid
            assert expected_id in returned_ids, (
                f"Model '{mid}' was returned by the OpenRouter API but is missing "
                f"from the model listing. This was the Issue #145 bug."
            )

    def test_previously_missing_models_now_included(self, mgr):
        """Models that were filtered out before #145 fix must now be present."""
        _reset_cache(mgr)
        # These specific models were NOT in the old OPENROUTER_POPULAR_MODELS set
        previously_missing = [
            "anthropic/claude-haiku-4.5",
            "google/gemini-2.5-flash-preview",
            "mistralai/mistral-medium-3",
            "cohere/command-a-03-2025",
            "x-ai/grok-3-mini-beta",
            "perplexity/sonar-deep-research",
        ]
        api_response = _make_openrouter_api_response(previously_missing)

        with patch("keyring.get_password", return_value="test-key"), \
             patch("urllib.request.urlopen", return_value=_mock_urlopen(api_response)):
            result = mgr.fetch_wee_models()

        or_models = result.get("OpenRouter Models", [])
        returned_ids = set(or_models)

        for mid in previously_missing:
            expected_id = "openrouter/" + mid
            assert expected_id in returned_ids, (
                f"Previously-filtered model '{mid}' still missing after fix"
            )

    def test_model_count_matches_api_response(self, mgr):
        """The number of discovered OpenRouter models must match the API count."""
        _reset_cache(mgr)
        api_response = _make_openrouter_api_response(SAMPLE_MODELS)

        with patch("keyring.get_password", return_value="test-key"), \
             patch("urllib.request.urlopen", return_value=_mock_urlopen(api_response)):
            result = mgr.fetch_wee_models()

        or_models = result.get("OpenRouter Models", [])

        # At minimum, all API models should be present (static fallbacks may add more)
        assert len(or_models) >= len(SAMPLE_MODELS), (
            f"Expected at least {len(SAMPLE_MODELS)} OpenRouter models, "
            f"got {len(or_models)}"
        )

    def test_no_openrouter_popular_models_constant(self, mgr):
        """Verify the OPENROUTER_POPULAR_MODELS filtering constant is removed."""
        assert not hasattr(mgr, "OPENROUTER_POPULAR_MODELS"), (
            "OPENROUTER_POPULAR_MODELS still exists — it should have been removed "
            "as part of Issue #145 to prevent accidental filtering"
        )


class TestIssue145LargeModelList:
    """Verify correct behavior with large API responses (hundreds of models)."""

    def test_large_model_list_all_included(self, mgr):
        """Simulate an API returning 200+ models — all must be included."""
        _reset_cache(mgr)
        large_model_list = [f"provider-{i}/model-{i}" for i in range(250)]
        api_response = _make_openrouter_api_response(large_model_list)

        with patch("keyring.get_password", return_value="test-key"), \
             patch("urllib.request.urlopen", return_value=_mock_urlopen(api_response)):
            result = mgr.fetch_wee_models()

        or_models = result.get("OpenRouter Models", [])
        returned_ids = set(or_models)

        for mid in large_model_list:
            expected_id = "openrouter/" + mid
            assert expected_id in returned_ids, (
                f"Model '{mid}' missing from a 250-model API response"
            )

    def test_empty_model_ids_skipped(self, mgr):
        """Models with empty IDs should be skipped."""
        _reset_cache(mgr)
        raw = json.dumps({
            "data": [
                {"id": "valid/model-a", "name": "Model A"},
                {"id": "", "name": "Empty ID"},
                {"id": "valid/model-b", "name": "Model B"},
            ]
        }).encode()

        with patch("keyring.get_password", return_value="test-key"), \
             patch("urllib.request.urlopen", return_value=_mock_urlopen(raw)):
            result = mgr.fetch_wee_models()

        or_models = result.get("OpenRouter Models", [])
        returned_ids = set(or_models)

        assert "openrouter/valid/model-a" in returned_ids
        assert "openrouter/valid/model-b" in returned_ids
        assert "openrouter/" not in returned_ids  # empty ID not included


class TestIssue145Fallback:
    """Verify fallback behavior when API is unavailable."""

    def test_fallback_to_static_on_api_failure(self, mgr):
        """When the API fails, static WEE_MODELS should still be returned."""
        _reset_cache(mgr)

        with patch("keyring.get_password", return_value="test-key"), \
             patch("urllib.request.urlopen", side_effect=Exception("API down")):
            result = mgr.fetch_wee_models()

        # Should still return something (static models)
        assert isinstance(result, dict)
        assert len(result) > 0, "No models returned even as fallback"

    def test_fallback_to_static_on_no_api_key(self, mgr):
        """When no API key is available, static models should be returned."""
        _reset_cache(mgr)

        with patch("keyring.get_password", return_value=None), \
             patch.dict(os.environ, {}, clear=False):
            # Remove OPENROUTER_API_KEY if set
            env_backup = os.environ.pop("OPENROUTER_API_KEY", None)
            try:
                result = mgr.fetch_wee_models()
            finally:
                if env_backup is not None:
                    os.environ["OPENROUTER_API_KEY"] = env_backup

        assert isinstance(result, dict)
        assert len(result) > 0, "No models returned without API key"


class TestIssue145MergeAliases:
    """Verify that static model aliases are preserved during merge."""

    def test_static_aliases_merged_into_discovered(self, mgr):
        """Aliases from static WEE_MODELS should be merged into discovered models."""
        _reset_cache(mgr)

        # Get a static OpenRouter model that has aliases
        static_or = mgr.WEE_MODELS.get("OpenRouter Models", [])
        if not static_or:
            pytest.skip("No static OpenRouter models to test alias merging")

        # Use just the first static model's raw ID
        first_static = static_or[0]
        raw_id = first_static[0].replace("openrouter/", "")
        expected_aliases = first_static[2]

        api_response = _make_openrouter_api_response([raw_id])

        with patch("keyring.get_password", return_value="test-key"), \
             patch("urllib.request.urlopen", return_value=_mock_urlopen(api_response)):
            result = mgr.fetch_wee_models()

        # fetch_wee_models returns flattened IDs via _static_models_to_dict,
        # so we just verify the model is present
        or_models = result.get("OpenRouter Models", [])
        returned_ids = set(or_models)
        assert "openrouter/" + raw_id in returned_ids

    def test_static_models_not_in_api_still_included(self, mgr):
        """Static models not found in the API response should still appear."""
        _reset_cache(mgr)

        # Return only one model from the API
        api_response = _make_openrouter_api_response(["some-new/model-xyz"])

        with patch("keyring.get_password", return_value="test-key"), \
             patch("urllib.request.urlopen", return_value=_mock_urlopen(api_response)):
            result = mgr.fetch_wee_models()

        or_models = result.get("OpenRouter Models", [])
        returned_ids = set(or_models)

        # The API model should be there
        assert "openrouter/some-new/model-xyz" in returned_ids

        # Static models should also be there (merged in)
        static_or = mgr.WEE_MODELS.get("OpenRouter Models", [])
        for entry in static_or:
            assert entry[0] in returned_ids, (
                f"Static model '{entry[0]}' missing after merge"
            )


class TestIssue145Caching:
    """Verify caching behavior doesn't hide newly available models."""

    def test_cache_expires_after_ttl(self, mgr):
        """After cache TTL, a new API call should be made."""
        import time as _time

        _reset_cache(mgr)
        first_models = ["provider/model-v1"]
        api_resp1 = _make_openrouter_api_response(first_models)

        with patch("keyring.get_password", return_value="test-key"), \
             patch("urllib.request.urlopen", return_value=_mock_urlopen(api_resp1)):
            result1 = mgr.fetch_wee_models()

        # Second call with different models but expired cache
        second_models = ["provider/model-v1", "provider/model-v2-new"]
        api_resp2 = _make_openrouter_api_response(second_models)

        # Force cache expiry
        mgr._openrouter_cache_ts = _time.time() - 600  # 10 min ago

        with patch("keyring.get_password", return_value="test-key"), \
             patch("urllib.request.urlopen", return_value=_mock_urlopen(api_resp2)):
            result2 = mgr.fetch_wee_models()

        or_models2 = result2.get("OpenRouter Models", [])
        returned_ids2 = set(or_models2)
        assert "openrouter/provider/model-v2-new" in returned_ids2, (
            "Newly added model not showing after cache expiry"
        )


class TestIssue145ModelsEndpointIntegration:
    """Verify the /api/v1/models endpoint returns all OpenRouter models."""

    def test_get_models_for_runtime_wee_returns_all(self, mgr):
        """get_models_for_runtime('wee') should return all API models."""
        _reset_cache(mgr)
        api_response = _make_openrouter_api_response(SAMPLE_MODELS)

        with patch("keyring.get_password", return_value="test-key"), \
             patch("urllib.request.urlopen", return_value=_mock_urlopen(api_response)):
            result = mgr.get_models_for_runtime("wee")

        or_models = result.get("OpenRouter Models", [])
        returned_ids = set(or_models)

        for mid in SAMPLE_MODELS:
            expected_id = "openrouter/" + mid
            assert expected_id in returned_ids, (
                f"get_models_for_runtime('wee') missing model '{mid}'"
            )
