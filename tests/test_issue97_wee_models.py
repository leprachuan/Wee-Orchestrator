"""Tests for Issue #97: wee runtime model listing fixes.

Bug 1: /api/v1/models excludes wee from known_runtimes
Bug 2: get_models_for_runtime wee lambda returns raw tuples
Enhancement: WEE_MODELS_JSON env var support
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_manager import SessionManager


class TestIssue97WeeModelsKnownRuntimes(unittest.TestCase):
    """Bug 1: wee must be in known_runtimes for /api/v1/models endpoint."""

    def test_wee_in_known_runtimes(self):
        """GET /api/v1/models?runtime=wee should not return 'Unknown runtime'."""
        # Read the source file and verify wee is in known_runtimes
        import pathlib
        src = pathlib.Path(__file__).parent.parent / "agent_manager.py"
        text = src.read_text()
        # Find the known_runtimes block
        idx = text.index("known_runtimes = {")
        block_end = text.index("}", idx)
        block = text[idx:block_end]
        self.assertIn('"wee"', block,
                       "wee must be in known_runtimes set in get_models endpoint")


class TestIssue97WeeModelsFormat(unittest.TestCase):
    """Bug 2: get_models_for_runtime('wee') must return {cat: [id_str, ...]}."""

    @classmethod
    def setUpClass(cls):
        cls.shim = SessionManager.__new__(SessionManager)
        cls.shim._env_claude_models = None
        cls.shim._env_gemini_models = None
        cls.shim._env_codex_models = None
        cls.shim._env_devin_models = None
        cls.shim._env_cursor_models = None
        cls.shim._env_wee_models = None

    def test_wee_models_returns_dict_of_string_lists(self):
        """get_models_for_runtime('wee') values must be lists of strings, not tuples."""
        result = self.shim.get_models_for_runtime("wee")
        self.assertIsInstance(result, dict)
        self.assertGreater(len(result), 0, "Should return at least one category")
        for category, models in result.items():
            self.assertIsInstance(category, str)
            self.assertIsInstance(models, list)
            for model_id in models:
                self.assertIsInstance(model_id, str,
                    f"Model in category '{category}' should be str, got {type(model_id)}: {model_id}")

    def test_wee_models_contains_expected_models(self):
        """Default WEE_MODELS should include known model IDs."""
        result = self.shim.get_models_for_runtime("wee")
        all_models = [m for models in result.values() for m in models]
        self.assertIn("ollama/gemma4:e4b", all_models)
        self.assertIn("openrouter/meta-llama/llama-4-scout", all_models)

    def test_wee_models_multiple_categories(self):
        """Default WEE_MODELS should have multiple categories."""
        result = self.shim.get_models_for_runtime("wee")
        self.assertGreaterEqual(len(result), 2, "Should have at least 2 categories")


class TestIssue97WeeModelsConstant(unittest.TestCase):
    """WEE_MODELS class constant follows the standard tuple format."""

    def test_wee_models_class_constant_exists(self):
        """SessionManager.WEE_MODELS must be defined."""
        self.assertTrue(hasattr(SessionManager, "WEE_MODELS"))

    def test_wee_models_constant_format(self):
        """WEE_MODELS entries must be (id, description, aliases) tuples."""
        for category, entries in SessionManager.WEE_MODELS.items():
            self.assertIsInstance(category, str)
            self.assertIsInstance(entries, list)
            for entry in entries:
                self.assertIsInstance(entry, tuple, f"Entry should be tuple: {entry}")
                self.assertEqual(len(entry), 3, f"Entry should be 3-tuple: {entry}")
                model_id, desc, aliases = entry
                self.assertIsInstance(model_id, str)
                self.assertIsInstance(desc, str)
                self.assertIsInstance(aliases, list)

    def test_wee_models_has_ollama_category(self):
        """WEE_MODELS should have an Ollama category."""
        categories = list(SessionManager.WEE_MODELS.keys())
        ollama_cats = [c for c in categories if "ollama" in c.lower()]
        self.assertGreater(len(ollama_cats), 0, f"Should have Ollama category, got: {categories}")


class TestIssue97FetchWeeModels(unittest.TestCase):
    """fetch_wee_models method and WEE_MODELS_JSON env var support."""

    @classmethod
    def setUpClass(cls):
        cls.shim = SessionManager.__new__(SessionManager)
        cls.shim._env_claude_models = None
        cls.shim._env_gemini_models = None
        cls.shim._env_codex_models = None
        cls.shim._env_devin_models = None
        cls.shim._env_cursor_models = None
        cls.shim._env_wee_models = None

    def test_fetch_wee_models_exists(self):
        """fetch_wee_models method must exist."""
        self.assertTrue(hasattr(self.shim, "fetch_wee_models"))
        self.assertTrue(callable(self.shim.fetch_wee_models))

    def test_fetch_wee_models_returns_string_lists(self):
        """fetch_wee_models must return {cat: [str, ...]} format."""
        result = self.shim.fetch_wee_models()
        for cat, models in result.items():
            for m in models:
                self.assertIsInstance(m, str, f"Expected str, got {type(m)}: {m}")

    @patch.dict(os.environ, {"WEE_MODELS_JSON": json.dumps({
        "Custom Ollama": [
            ["custom/model-1", "Custom Model 1", []],
            ["custom/model-2", "Custom Model 2", ["cm2"]],
        ]
    })})
    def test_wee_models_json_env_override(self):
        """WEE_MODELS_JSON env var should override static WEE_MODELS."""
        shim = SessionManager.__new__(SessionManager)
        shim._env_wee_models = None
        result = shim.fetch_wee_models()
        self.assertIn("Custom Ollama", result)
        self.assertIn("custom/model-1", result["Custom Ollama"])
        self.assertIn("custom/model-2", result["Custom Ollama"])

    @patch.dict(os.environ, {"WEE_MODELS_JSON": json.dumps({
        "Test": [["test/m1", "Test Model", ["tm1"]]]
    })})
    def test_wee_models_json_caches_env_models(self):
        """fetch_wee_models should cache env models in _env_wee_models."""
        shim = SessionManager.__new__(SessionManager)
        shim._env_wee_models = None
        shim.fetch_wee_models()
        self.assertIsNotNone(shim._env_wee_models)
        self.assertIn("Test", shim._env_wee_models)

    @patch.dict(os.environ, {"WEE_MODELS_JSON": "{invalid json"})
    def test_wee_models_json_invalid_falls_back(self):
        """Invalid WEE_MODELS_JSON should fall back to static models."""
        shim = SessionManager.__new__(SessionManager)
        shim._env_wee_models = None
        result = shim.fetch_wee_models()
        # Should return static models, not crash
        self.assertGreater(len(result), 0)
        # Should include default categories
        all_models = [m for models in result.values() for m in models]
        self.assertIn("ollama/gemma4:e4b", all_models)

    def test_fetch_wee_models_no_env_uses_static(self):
        """Without WEE_MODELS_JSON, fetch_wee_models uses WEE_MODELS constant."""
        # Ensure no env var
        with patch.dict(os.environ, {}, clear=False):
            if "WEE_MODELS_JSON" in os.environ:
                del os.environ["WEE_MODELS_JSON"]
            shim = SessionManager.__new__(SessionManager)
            shim._env_wee_models = None
            result = shim.fetch_wee_models()
            all_models = [m for models in result.values() for m in models]
            self.assertIn("ollama/gemma4:e4b", all_models)


class TestIssue97ModelDescription(unittest.TestCase):
    """_get_model_description should work for wee runtime models."""

    @classmethod
    def setUpClass(cls):
        cls.shim = SessionManager.__new__(SessionManager)
        cls.shim._env_claude_models = None
        cls.shim._env_gemini_models = None
        cls.shim._env_codex_models = None
        cls.shim._env_devin_models = None
        cls.shim._env_cursor_models = None
        cls.shim._env_wee_models = None

    def test_wee_model_description_from_static(self):
        """Should find description for wee models from static WEE_MODELS."""
        desc = self.shim._get_model_description("ollama/gemma4:e4b", "wee")
        self.assertIsNotNone(desc)
        self.assertIn("Gemma", desc)

    def test_wee_model_description_unknown_model(self):
        """Should return None for unknown wee model."""
        desc = self.shim._get_model_description("nonexistent/model", "wee")
        self.assertIsNone(desc)


class TestIssue97ResolveModelAlias(unittest.TestCase):
    """get_model_from_name should work for wee runtime aliases."""

    @classmethod
    def setUpClass(cls):
        cls.shim = SessionManager.__new__(SessionManager)
        cls.shim._env_claude_models = None
        cls.shim._env_gemini_models = None
        cls.shim._env_codex_models = None
        cls.shim._env_devin_models = None
        cls.shim._env_cursor_models = None
        cls.shim._env_wee_models = None

    def test_resolve_wee_alias_exact(self):
        """Should resolve exact wee model ID."""
        result = self.shim.get_model_from_name("ollama/gemma4:e4b", "wee")
        self.assertEqual(result, "ollama/gemma4:e4b")

    def test_resolve_wee_alias_by_alias(self):
        """Should resolve wee model by alias."""
        result = self.shim.get_model_from_name("gemma4-e4b", "wee")
        self.assertEqual(result, "ollama/gemma4:e4b")

    def test_resolve_wee_alias_by_description(self):
        """Should resolve wee model by description."""
        result = self.shim.get_model_from_name("Gemma 4 E4B (local)", "wee")
        self.assertEqual(result, "ollama/gemma4:e4b")


class TestIssue97DispatchTable(unittest.TestCase):
    """Dispatch table in get_models_for_runtime must use fetch_wee_models."""

    @classmethod
    def setUpClass(cls):
        cls.shim = SessionManager.__new__(SessionManager)
        cls.shim._env_claude_models = None
        cls.shim._env_gemini_models = None
        cls.shim._env_codex_models = None
        cls.shim._env_devin_models = None
        cls.shim._env_cursor_models = None
        cls.shim._env_wee_models = None

    def test_dispatch_wee_is_not_lambda(self):
        """The wee dispatch entry should be a bound method, not an inline lambda."""
        import inspect
        source = inspect.getsource(self.shim.get_models_for_runtime)
        # Should NOT contain a lambda for wee
        self.assertNotIn('lambda:', source.split('"wee"')[1].split('\n')[0] if '"wee"' in source else "",
                         "wee dispatch should use fetch_wee_models, not a lambda")


if __name__ == "__main__":
    unittest.main()
