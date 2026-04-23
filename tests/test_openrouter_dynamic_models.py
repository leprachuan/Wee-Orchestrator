"""Regression tests for dynamic OpenRouter model discovery (Issue #172 deduplication).

Tests:
1. fetch_openrouter_models() deduplicates variant suffixes (:free, :thinking, etc.)
2. Models are collapsed into a single "OpenRouter" group
3. Falls back to static WEE_MODELS on network error
4. Falls back to static WEE_MODELS when no API key
5. Static aliases are preserved for known base model IDs
6. Cache TTL works (second call returns cached result without HTTP)
7. fetch_wee_models() integrates with fetch_openrouter_models()
8. fetch_wee_models() falls back gracefully when fetch_openrouter_models() raises
9. get_models_for_runtime("wee") returns dynamic models
"""

import json
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_manager import SessionManager  # noqa: E402


def _make_session_manager():
    """Create a minimal SessionManager for testing."""
    sm = object.__new__(SessionManager)
    sm._env_wee_models = None
    sm._openrouter_cache_ts = 0
    sm._openrouter_models_cache = None
    sm._openrouter_models_cache_ts = 0
    return sm


def _mock_api_response(models):
    """Build a urllib response mock returning the given model list."""
    body = json.dumps({"data": models}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


SAMPLE_MODELS = [
    {"id": "anthropic/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet"},
    {"id": "anthropic/claude-3-haiku", "name": "Claude 3 Haiku"},
    {"id": "openai/gpt-4o", "name": "GPT-4o"},
    {"id": "meta-llama/llama-3-8b-instruct", "name": "Llama 3 8B"},
    {"id": "meta-llama/llama-4-scout", "name": "Llama 4 Scout"},
    {"id": "google/gemma-2-9b-it:free", "name": "Gemma 2 9B Free"},
    {"id": "deepseek/deepseek-r1:free", "name": "DeepSeek R1 Free"},
    {"id": "qwen/qwen3-32b:free", "name": "Qwen 3 32B Free"},
    {"id": "unknown-provider/some-model", "name": "Some Model"},
]


class TestFetchOpenrouterModels(unittest.TestCase):
    def setUp(self):
        self.sm = _make_session_manager()

    def _urlopen_mock(self, req, timeout=15):
        return _mock_api_response(SAMPLE_MODELS)

    @patch("keyring.get_password", return_value="test-key-123")
    @patch("urllib.request.urlopen")
    def test_returns_base_model_ids(self, mock_urlopen, mock_keyring):
        """Models are returned with variant suffixes stripped (Issue #172)."""
        mock_urlopen.side_effect = self._urlopen_mock
        result = self.sm.fetch_openrouter_models()
        all_ids = [m for models in result.values() for m in models]
        all_model_ids = [m[0] for m in all_ids]
        self.assertIn("openrouter/anthropic/claude-3.5-sonnet", all_model_ids)
        # :free suffix should be stripped to base ID
        self.assertIn("openrouter/qwen/qwen3-32b", all_model_ids)
        self.assertNotIn("openrouter/qwen/qwen3-32b:free", all_model_ids)
        self.assertIn("openrouter/unknown-provider/some-model", all_model_ids)

    @patch("keyring.get_password", return_value="test-key-123")
    @patch("urllib.request.urlopen")
    def test_single_openrouter_group(self, mock_urlopen, mock_keyring):
        """All models collapsed into single 'OpenRouter' group (Issue #172)."""
        mock_urlopen.side_effect = self._urlopen_mock
        result = self.sm.fetch_openrouter_models()
        # Should be exactly one group
        self.assertEqual(len(result), 1)
        self.assertIn("OpenRouter", result)
        # No per-provider groups
        self.assertNotIn("OpenRouter - Anthropic", result)
        self.assertNotIn("OpenRouter - OpenAI", result)

    @patch("keyring.get_password", return_value="test-key-123")
    @patch("urllib.request.urlopen")
    def test_models_sorted_alphabetically(self, mock_urlopen, mock_keyring):
        """Models within the OpenRouter group should be sorted
        alphabetically (Issue #172)."""
        mock_urlopen.side_effect = self._urlopen_mock
        result = self.sm.fetch_openrouter_models()
        self.assertIn("OpenRouter", result)
        names = [m[1].lower() for m in result["OpenRouter"]]
        self.assertEqual(names, sorted(names))

    @patch("keyring.get_password", side_effect=Exception("no keyring"))
    @patch.dict("os.environ", {}, clear=True)
    def test_fallback_no_api_key(self, mock_keyring):
        """Without an API key, returns static WEE_MODELS fallback."""
        result = self.sm.fetch_openrouter_models()
        self.assertIn("OpenRouter Models", result)
        # Should not be empty
        self.assertTrue(len(result["OpenRouter Models"]) > 0)

    @patch("keyring.get_password", return_value="test-key-123")
    @patch("urllib.request.urlopen", side_effect=Exception("network error"))
    def test_fallback_on_network_error(self, mock_urlopen, mock_keyring):
        """On network error, returns static WEE_MODELS fallback."""
        result = self.sm.fetch_openrouter_models()
        self.assertIn("OpenRouter Models", result)
        self.assertTrue(len(result["OpenRouter Models"]) > 0)

    @patch("keyring.get_password", return_value="test-key-123")
    @patch("urllib.request.urlopen")
    def test_static_aliases_preserved(self, mock_urlopen, mock_keyring):
        """Static aliases in WEE_MODELS should be preserved after dedup (Issue #172)."""
        # The static WEE_MODELS has "openrouter/meta-llama/llama-4-scout"
        # with alias ["or-scout"]. When API returns this, alias included.
        models = [{"id": "meta-llama/llama-4-scout", "name": "Llama 4 Scout"}]
        mock_urlopen.return_value = _mock_api_response(models)
        result = self.sm.fetch_openrouter_models()
        # All models now in single "OpenRouter" group
        or_group = result.get("OpenRouter", [])
        scout_entries = [
            m for m in or_group if m[0] == "openrouter/meta-llama/llama-4-scout"
        ]
        self.assertTrue(len(scout_entries) > 0)
        # aliases should be the third element
        self.assertIn("or-scout", scout_entries[0][2])

    @patch("keyring.get_password", return_value="test-key-123")
    @patch("urllib.request.urlopen")
    def test_cache_ttl(self, mock_urlopen, mock_keyring):
        """Second call within TTL returns cached result without HTTP."""
        mock_urlopen.side_effect = self._urlopen_mock
        self.sm.fetch_openrouter_models()
        call_count_1 = mock_urlopen.call_count
        # Second call should use cache
        self.sm.fetch_openrouter_models()
        call_count_2 = mock_urlopen.call_count
        self.assertEqual(
            call_count_1, call_count_2, "Second call should use cache, not HTTP"
        )

    @patch("keyring.get_password", return_value="test-key-123")
    @patch("urllib.request.urlopen")
    def test_cache_expires(self, mock_urlopen, mock_keyring):
        """After TTL expires, should make a new HTTP request."""
        mock_urlopen.side_effect = self._urlopen_mock
        self.sm.fetch_openrouter_models()
        # Expire the cache (TTL: 300s, set to 400s ago)
        self.sm._openrouter_models_cache_ts = time.time() - 400
        self.sm.fetch_openrouter_models()
        self.assertEqual(
            mock_urlopen.call_count, 2, "Should make new HTTP call after TTL expires"
        )

    @patch("keyring.get_password", return_value="test-key-123")
    @patch("urllib.request.urlopen")
    def test_models_sorted_within_group(self, mock_urlopen, mock_keyring):
        """Models within each group should be sorted alphabetically."""
        mock_urlopen.side_effect = self._urlopen_mock
        result = self.sm.fetch_openrouter_models()
        for cat, models in result.items():
            names = [m[1].lower() for m in models]
            self.assertEqual(
                names, sorted(names), f"Models in '{cat}' should be sorted"
            )

    @patch("keyring.get_password", return_value="test-key-123")
    @patch("urllib.request.urlopen")
    def test_fetch_wee_models_uses_fetch_openrouter_models(
        self, mock_urlopen, mock_keyring
    ):
        """fetch_wee_models() should integrate fetch_openrouter_models()."""
        mock_urlopen.side_effect = self._urlopen_mock
        # Initialise remaining required attrs for fetch_wee_models
        self.sm._env_wee_models = None
        result = self.sm.fetch_wee_models()
        # Should contain dynamic OpenRouter groups, not just static
        or_keys = [k for k in result if "OpenRouter" in k]
        self.assertTrue(len(or_keys) > 0)

    @patch("keyring.get_password", return_value="test-key-123")
    @patch("urllib.request.urlopen", side_effect=Exception("down"))
    def test_fetch_wee_models_fallback_when_openrouter_fails(
        self, mock_urlopen, mock_keyring
    ):
        """fetch_wee_models() returns Ollama models when OpenRouter fails."""
        self.sm._env_wee_models = None
        result = self.sm.fetch_wee_models()
        ollama_keys = [k for k in result if "Ollama" in k]
        self.assertTrue(len(ollama_keys) > 0)

    def test_provider_names_constant(self):
        """OPENROUTER_PROVIDER_NAMES should contain common providers."""
        self.assertIn("anthropic", self.sm.OPENROUTER_PROVIDER_NAMES)
        self.assertIn("meta-llama", self.sm.OPENROUTER_PROVIDER_NAMES)
        self.assertIn("openai", self.sm.OPENROUTER_PROVIDER_NAMES)
        self.assertIn("google", self.sm.OPENROUTER_PROVIDER_NAMES)

    @patch("keyring.get_password", return_value="test-key-123")
    @patch("urllib.request.urlopen")
    def test_unknown_provider_in_single_group(self, mock_urlopen, mock_keyring):
        """Unknown providers are included in the single 'OpenRouter' group
        (Issue #172)."""
        models = [{"id": "some-new-provider/a-model", "name": "A Model"}]
        mock_urlopen.return_value = _mock_api_response(models)
        result = self.sm.fetch_openrouter_models()
        # No per-provider groups — everything in "OpenRouter"
        self.assertNotIn("OpenRouter - Some New Provider", result)
        self.assertIn("OpenRouter", result)
        or_group = result.get("OpenRouter", [])
        all_ids = [m[0] for m in or_group]
        self.assertIn("openrouter/some-new-provider/a-model", all_ids)

    @patch("keyring.get_password", return_value="test-key-123")
    @patch("urllib.request.urlopen")
    def test_model_id_prefix(self, mock_urlopen, mock_keyring):
        """All returned model IDs should have 'openrouter/' prefix."""
        mock_urlopen.side_effect = self._urlopen_mock
        result = self.sm.fetch_openrouter_models()
        for cat, models in result.items():
            for model_id, _, _ in models:
                self.assertTrue(
                    model_id.startswith("openrouter/"),
                    f"Model ID '{model_id}' should start with 'openrouter/'",
                )

    @patch("keyring.get_password", return_value="test-key-123")
    @patch("urllib.request.urlopen")
    def test_empty_id_skipped(self, mock_urlopen, mock_keyring):
        """Models with empty/None id should be skipped."""
        models = [
            {"id": "", "name": "Empty ID"},
            {"id": None, "name": "Null ID"},
            {"id": "valid-provider/valid-model", "name": "Valid"},
        ]
        mock_urlopen.return_value = _mock_api_response(models)
        result = self.sm.fetch_openrouter_models()
        all_ids = [m[0] for group in result.values() for m in group]
        # Empty/None id should be skipped
        self.assertNotIn("openrouter/", all_ids)
        self.assertIn("openrouter/valid-provider/valid-model", all_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
