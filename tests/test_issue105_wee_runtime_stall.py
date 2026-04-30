"""Tests for Issue #105: wee runtime stalls with Ollama gemma4:e4b on kubuntu.

Validates:
- Correct Ollama port (11434, not 11436)
- Model resolution for wee runtime (flat strings, not tuples)
- Provider prefix stripping in model name resolution
- Shortest-match preference in substring matching
- httpx.Timeout with connect timeout and max_retries=0
- wee_runtime.py endpoint configuration
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")


class TestIssue105OllamaPort(unittest.TestCase):
    """Verify Ollama port is 11434 (not 11436) throughout wee runtime code."""

    def test_agent_manager_presets_use_correct_port(self):
        """Provider presets in run_wee_native use port 11434."""
        from agent_manager import SessionManager

        mgr = SessionManager()
        # Access the _PRESETS dict defined inside run_wee_native by checking source
        import inspect

        source = inspect.getsource(mgr.run_wee_native)
        self.assertIn(
            "192.168.1.101:11434", source, "run_wee_native should use port 11434"
        )
        self.assertNotIn(
            "192.168.1.101:11436",
            source,
            "run_wee_native should NOT use old port 11436",
        )

    def test_agent_manager_fallback_uses_correct_port(self):
        """Default fallback api_base in run_wee_native uses port 11434."""
        import inspect

        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager.run_wee_native)
        # Count occurrences of the correct port
        assert (
            source.count("11434") >= 2
        ), "Should have at least 2 references to port 11434"

    def test_wee_runtime_presets_use_correct_port(self):
        """wee_runtime.py PROVIDER_PRESETS use port 11434."""
        import wee_runtime

        ollama_base, _ = wee_runtime.PROVIDER_PRESETS["ollama"]
        self.assertIn(
            "11434", ollama_base, "wee_runtime.py ollama preset should use port 11434"
        )
        self.assertNotIn(
            "11436", ollama_base, "wee_runtime.py should NOT use old port 11436"
        )

    def test_wee_runtime_resolve_default_endpoint(self):
        """resolve_model_and_endpoint default uses port 11434."""
        import wee_runtime

        _, base, _ = wee_runtime.resolve_model_and_endpoint("gemma4:e4b")
        self.assertIn("11434", base, "Default endpoint should use port 11434")

    def test_wee_runtime_resolve_ollama_prefix(self):
        """ollama/ prefix resolves to port 11434."""
        import wee_runtime

        model, base, key = wee_runtime.resolve_model_and_endpoint("ollama/gemma4:e4b")
        self.assertEqual(model, "gemma4:e4b")
        self.assertIn("11434", base)
        self.assertEqual(key, "ollama")


class TestIssue105ModelResolution(unittest.TestCase):
    """Verify wee model list and resolution works correctly."""

    def test_wee_models_are_flat_strings(self):
        """get_models_for_runtime('wee') returns flat strings, not tuples."""
        from agent_manager import SessionManager

        mgr = SessionManager()
        models = mgr.get_models_for_runtime("wee")
        # Check has model categories (Ollama Models or OpenRouter Models)
        self.assertTrue(len(models) > 0, "Should have at least one category")
        all_str = [m for vals in models.values() for m in vals]
        self.assertTrue(all(isinstance(m, str) for m in all_str))
        for m in [m for vals in models.values() for m in vals]:
            self.assertIsInstance(
                m, str, f"Model should be a string, got {type(m)}: {m}"
            )

    def test_wee_model_list_includes_gemma4(self):
        """Model list includes gemma4 variants."""
        from agent_manager import SessionManager

        mgr = SessionManager()
        models = mgr.get_models_for_runtime("wee")
        all_models = [m for vals in models.values() for m in vals]
        self.assertTrue(
            any("gemma4:e4b" in m for m in all_models), "Should include gemma4:e4b"
        )

    def test_model_resolution_exact_with_prefix(self):
        """Model resolution: 'ollama/gemma4:e4b' matches exactly."""
        from agent_manager import SessionManager

        mgr = SessionManager()
        result = mgr.get_model_from_name("ollama/gemma4:e4b", "wee")
        self.assertEqual(result, "ollama/gemma4:e4b")

    def test_model_resolution_prefix_stripped(self):
        """Model resolution: 'gemma4:e4b' matches 'ollama/gemma4:e4b' via prefix strip."""  # noqa: E501
        from agent_manager import SessionManager

        mgr = SessionManager()
        result = mgr.get_model_from_name("gemma4:e4b", "wee")
        self.assertEqual(
            result,
            "ollama/gemma4:e4b",
            "Should resolve to ollama/gemma4:e4b, not a longer variant",
        )

    def test_model_resolution_shortest_match(self):
        """Substring matching prefers shortest match."""
        from agent_manager import SessionManager

        mgr = SessionManager()
        # "gemma4:e2b" should match "ollama/gemma4:e2b" not "ollama/gemma4:e2b-nothinker"  # noqa: E501
        result = mgr.get_model_from_name("gemma4:e2b", "wee")
        self.assertEqual(result, "ollama/gemma4:e2b", "Should prefer shortest match")


class TestIssue105TimeoutConfig(unittest.TestCase):
    """Verify timeout and retry configuration."""

    def test_run_wee_native_uses_httpx_timeout(self):
        """run_wee_native creates OpenAI client with httpx.Timeout."""
        import inspect

        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager.run_wee_native)
        self.assertIn(
            "httpx.Timeout",
            source,
            "Should use httpx.Timeout for granular timeout control",
        )
        self.assertIn("connect=15.0", source, "Should set 15s connect timeout")

    def test_run_wee_native_no_retries(self):
        """run_wee_native sets max_retries=0 for fast failure."""
        import inspect

        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager.run_wee_native)
        self.assertIn(
            "max_retries=0",
            source,
            "Should set max_retries=0 to fail fast on connection issues",
        )

    def test_wee_runtime_uses_httpx_timeout(self):
        """wee_runtime.py uses httpx.Timeout."""
        import inspect

        import wee_runtime

        source = inspect.getsource(wee_runtime.main)
        self.assertIn("httpx.Timeout", source)
        self.assertIn("connect=15.0", source)
        self.assertIn("max_retries=0", source)


class TestIssue105NoPortRegression(unittest.TestCase):
    """Ensure no references to wrong port 11436 remain."""

    def test_no_11436_in_agent_manager(self):
        """agent_manager.py has no references to port 11436."""
        filepath = os.path.join(os.path.dirname(__file__), "..", "agent_manager.py")
        with open(filepath) as f:
            content = f.read()
        # Only check wee-related code (11436 might appear in comments/docs)
        self.assertNotIn(
            "192.168.1.101:11436",
            content,
            "No references to old port 11436 should remain",
        )

    def test_no_11436_in_wee_runtime(self):
        """wee_runtime.py has no references to port 11436."""
        filepath = os.path.join(os.path.dirname(__file__), "..", "wee_runtime.py")
        with open(filepath) as f:
            content = f.read()
        self.assertNotIn(
            "11436", content, "wee_runtime.py should have no references to port 11436"
        )


if __name__ == "__main__":
    unittest.main()
