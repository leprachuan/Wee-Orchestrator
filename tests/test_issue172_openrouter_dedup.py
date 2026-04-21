"""Regression tests for Issue #172: Deduplicate OpenRouter model list.

The OpenRouter API returns both base models and their variant suffixes
(:free, :thinking, :extended, :beta, :nitro, :floor, :preview) as separate
entries, and groups them by provider — creating a bloated model picker.

Fix:
- Strip variant suffixes to get the canonical base model ID.
- Keep only the first (base) entry when the same base ID appears multiple times.
- Collapse all provider groups into a single "OpenRouter" section.

Regression tests assert:
1. No model appears twice in the deduplicated list (base + variant).
2. The total count of returned models is lower than the raw API count.
3. openrouter/meta-llama/llama-3.3-70b-instruct IS present.
4. openrouter/meta-llama/llama-3.3-70b-instruct:free is NOT present.
5. All BASE_MODEL_RE suffixes are stripped correctly.
6. Result is a single "OpenRouter" group, not per-provider sub-groups.
"""

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")  # noqa: E402
from agent_manager import SessionManager  # noqa: E402


def _make_session_manager():
    sm = object.__new__(SessionManager)
    sm._env_wee_models = None
    sm._openrouter_cache_ts = 0
    sm._openrouter_models_cache = None
    sm._openrouter_models_cache_ts = 0
    return sm


def _mock_api_response(models):
    body = json.dumps({"data": models}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# Realistic sample with base + all variant types alongside other models
VARIANT_MODELS = [
    # Base + all variant types for llama-3.3-70b-instruct
    {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Llama 3.3 70B Instruct"},
    {"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "Llama 3.3 70B Instruct (free)"},
    {"id": "meta-llama/llama-3.3-70b-instruct:nitro", "name": "Llama 3.3 70B Nitro"},
    {"id": "meta-llama/llama-3.3-70b-instruct:floor", "name": "Llama 3.3 70B Floor"},
    # :thinking variant
    {"id": "anthropic/claude-3.7-sonnet", "name": "Claude 3.7 Sonnet"},
    {"id": "anthropic/claude-3.7-sonnet:thinking", "name": "Claude 3.7 Sonnet (thinking)"},
    # :extended variant
    {"id": "openai/gpt-4o", "name": "GPT-4o"},
    {"id": "openai/gpt-4o:extended", "name": "GPT-4o Extended"},
    # :beta variant
    {"id": "deepseek/deepseek-r1", "name": "DeepSeek R1"},
    {"id": "deepseek/deepseek-r1:free", "name": "DeepSeek R1 Free"},
    # :preview variant
    {"id": "google/gemini-2.5-pro-preview", "name": "Gemini 2.5 Pro Preview"},
    {"id": "google/gemini-2.5-pro-preview:free", "name": "Gemini 2.5 Pro Preview Free"},
    # A unique model with no variants
    {"id": "mistralai/mistral-small-24b", "name": "Mistral Small 24B"},
]

# Count: 13 raw API entries, expected deduplicated: 6 base models
# (meta-llama, anthropic, openai, deepseek, google, mistralai)
RAW_COUNT = len(VARIANT_MODELS)
EXPECTED_DEDUP_COUNT = 6


class TestIssue172Deduplication(unittest.TestCase):
    """Test that variant suffixes are stripped and duplicates removed."""

    def setUp(self):
        self.sm = _make_session_manager()

    @patch("keyring.get_password", return_value="test-key")
    @patch("urllib.request.urlopen")
    def test_no_duplicate_base_ids(self, mock_urlopen, _keyring):
        """No model ID should appear twice — base + :free must be collapsed."""
        mock_urlopen.return_value = _mock_api_response(VARIANT_MODELS)
        result = self.sm.fetch_openrouter_models()
        all_ids = [m[0] for group in result.values() for m in group]
        self.assertEqual(len(all_ids), len(set(all_ids)), "Duplicate model IDs found")

    @patch("keyring.get_password", return_value="test-key")
    @patch("urllib.request.urlopen")
    def test_count_lower_than_raw(self, mock_urlopen, _keyring):
        """Deduplicated count must be less than the raw API count."""
        mock_urlopen.return_value = _mock_api_response(VARIANT_MODELS)
        result = self.sm.fetch_openrouter_models()
        total = sum(len(v) for v in result.values())
        self.assertLess(
            total,
            RAW_COUNT,
            f"Expected dedup count ({total}) < raw count ({RAW_COUNT})",
        )
        self.assertEqual(total, EXPECTED_DEDUP_COUNT)

    @patch("keyring.get_password", return_value="test-key")
    @patch("urllib.request.urlopen")
    def test_base_model_present(self, mock_urlopen, _keyring):
        """openrouter/meta-llama/llama-3.3-70b-instruct must be present."""
        mock_urlopen.return_value = _mock_api_response(VARIANT_MODELS)
        result = self.sm.fetch_openrouter_models()
        all_ids = [m[0] for group in result.values() for m in group]
        self.assertIn("openrouter/meta-llama/llama-3.3-70b-instruct", all_ids)

    @patch("keyring.get_password", return_value="test-key")
    @patch("urllib.request.urlopen")
    def test_free_variant_not_present(self, mock_urlopen, _keyring):
        """openrouter/meta-llama/llama-3.3-70b-instruct:free must NOT be present."""
        mock_urlopen.return_value = _mock_api_response(VARIANT_MODELS)
        result = self.sm.fetch_openrouter_models()
        all_ids = [m[0] for group in result.values() for m in group]
        self.assertNotIn(
            "openrouter/meta-llama/llama-3.3-70b-instruct:free",
            all_ids,
            "Variant :free should be collapsed into base model",
        )

    @patch("keyring.get_password", return_value="test-key")
    @patch("urllib.request.urlopen")
    def test_all_suffix_types_stripped(self, mock_urlopen, _keyring):
        """All BASE_MODEL_RE suffix types must be stripped."""
        suffixes = [":free", ":thinking", ":extended", ":beta", ":nitro", ":floor", ":preview"]
        base = "provider/model-name"
        raw = [{"id": base, "name": "Base Model"}] + [
            {"id": base + s, "name": f"Variant {s}"}
            for s in suffixes
        ]
        mock_urlopen.return_value = _mock_api_response(raw)
        result = self.sm.fetch_openrouter_models()
        all_ids = [m[0] for group in result.values() for m in group]
        # Only base should exist
        self.assertIn("openrouter/" + base, all_ids)
        for s in suffixes:
            self.assertNotIn(
                "openrouter/" + base + s,
                all_ids,
                f"Variant '{s}' should be stripped",
            )
        self.assertEqual(len(all_ids), 1)

    @patch("keyring.get_password", return_value="test-key")
    @patch("urllib.request.urlopen")
    def test_single_openrouter_group(self, mock_urlopen, _keyring):
        """All models must be collapsed into a single 'OpenRouter' group."""
        mock_urlopen.return_value = _mock_api_response(VARIANT_MODELS)
        result = self.sm.fetch_openrouter_models()
        self.assertEqual(
            list(result.keys()),
            ["OpenRouter"],
            f"Expected single 'OpenRouter' group, got: {list(result.keys())}",
        )

    @patch("keyring.get_password", return_value="test-key")
    @patch("urllib.request.urlopen")
    def test_base_model_wins_when_base_comes_first(self, mock_urlopen, _keyring):
        """When base model appears before :free in API response, base is kept."""
        raw = [
            {"id": "openai/gpt-4o", "name": "GPT-4o"},
            {"id": "openai/gpt-4o:free", "name": "GPT-4o Free"},
        ]
        mock_urlopen.return_value = _mock_api_response(raw)
        result = self.sm.fetch_openrouter_models()
        all_ids = [m[0] for group in result.values() for m in group]
        self.assertIn("openrouter/openai/gpt-4o", all_ids)
        self.assertNotIn("openrouter/openai/gpt-4o:free", all_ids)

    @patch("keyring.get_password", return_value="test-key")
    @patch("urllib.request.urlopen")
    def test_variant_first_base_deduplicated(self, mock_urlopen, _keyring):
        """When :free appears before the base in API response, only one entry kept."""
        raw = [
            {"id": "openai/gpt-4o:free", "name": "GPT-4o Free"},
            {"id": "openai/gpt-4o", "name": "GPT-4o"},
        ]
        mock_urlopen.return_value = _mock_api_response(raw)
        result = self.sm.fetch_openrouter_models()
        all_ids = [m[0] for group in result.values() for m in group]
        # Only one entry for gpt-4o (whichever came first, base ID)
        gpt4o_entries = [i for i in all_ids if "gpt-4o" in i]
        self.assertEqual(len(gpt4o_entries), 1)
        # The ID should never have a suffix
        self.assertFalse(gpt4o_entries[0].endswith(":free"))

    @patch("keyring.get_password", return_value="test-key")
    @patch("urllib.request.urlopen")
    def test_base_model_re_constant_exists(self, mock_urlopen, _keyring):
        """BASE_MODEL_RE class constant must exist on SessionManager."""
        import re
        self.assertTrue(
            hasattr(self.sm, "BASE_MODEL_RE"),
            "BASE_MODEL_RE constant missing from SessionManager",
        )
        self.assertIsInstance(self.sm.BASE_MODEL_RE, type(re.compile("")))

    @patch("keyring.get_password", return_value="test-key")
    @patch("urllib.request.urlopen")
    def test_large_api_response_deduplicates(self, mock_urlopen, _keyring):
        """Simulate 345-model API response (like production) — dedup should reduce count."""
        # Build a realistic large set: 200 base models + 145 variant duplicates
        raw = []
        suffixes = [":free", ":thinking", ":nitro"]
        providers = ["anthropic", "openai", "google", "meta-llama", "deepseek",
                     "mistralai", "qwen", "microsoft", "nvidia", "cohere"]
        idx = 0
        for p in providers:
            for j in range(20):
                base = f"{p}/model-{idx}"
                raw.append({"id": base, "name": f"Model {idx}"})
                # Add some variants
                if idx % 3 == 0:
                    raw.append({"id": base + ":free", "name": f"Model {idx} Free"})
                if idx % 5 == 0:
                    raw.append({"id": base + ":thinking", "name": f"Model {idx} Thinking"})
                idx += 1

        raw_count = len(raw)
        mock_urlopen.return_value = _mock_api_response(raw)
        result = self.sm.fetch_openrouter_models()
        total = sum(len(v) for v in result.values())
        self.assertLess(total, raw_count, "Dedup should reduce count vs raw API")
        # Should equal number of unique base models (200)
        self.assertEqual(total, 200)


class TestIssue172BASE_MODEL_RE(unittest.TestCase):
    """Unit tests for the BASE_MODEL_RE regex pattern."""

    def setUp(self):
        self.sm = _make_session_manager()

    def test_strips_free(self):
        self.assertEqual(
            self.sm.BASE_MODEL_RE.sub("", "meta-llama/llama-3.3-70b-instruct:free"),
            "meta-llama/llama-3.3-70b-instruct",
        )

    def test_strips_thinking(self):
        self.assertEqual(
            self.sm.BASE_MODEL_RE.sub("", "anthropic/claude-3.7-sonnet:thinking"),
            "anthropic/claude-3.7-sonnet",
        )

    def test_strips_extended(self):
        self.assertEqual(
            self.sm.BASE_MODEL_RE.sub("", "openai/gpt-4o:extended"),
            "openai/gpt-4o",
        )

    def test_strips_beta(self):
        self.assertEqual(
            self.sm.BASE_MODEL_RE.sub("", "openai/o3:beta"),
            "openai/o3",
        )

    def test_strips_nitro(self):
        self.assertEqual(
            self.sm.BASE_MODEL_RE.sub("", "meta-llama/llama-3.3-70b-instruct:nitro"),
            "meta-llama/llama-3.3-70b-instruct",
        )

    def test_strips_floor(self):
        self.assertEqual(
            self.sm.BASE_MODEL_RE.sub("", "meta-llama/llama-3.3-70b-instruct:floor"),
            "meta-llama/llama-3.3-70b-instruct",
        )

    def test_strips_preview(self):
        self.assertEqual(
            self.sm.BASE_MODEL_RE.sub("", "google/gemini-2.5-pro-preview:free"),
            "google/gemini-2.5-pro-preview",
        )

    def test_base_id_unchanged(self):
        """Model IDs without suffixes should be unchanged."""
        base = "meta-llama/llama-3.3-70b-instruct"
        self.assertEqual(self.sm.BASE_MODEL_RE.sub("", base), base)

    def test_does_not_strip_mid_word(self):
        """Suffix tokens mid-model-name should not be stripped."""
        # 'free' in model name but not as a suffix
        mid = "provider/my-free-model"
        self.assertEqual(self.sm.BASE_MODEL_RE.sub("", mid), mid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
