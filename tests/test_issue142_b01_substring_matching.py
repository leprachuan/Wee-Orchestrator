"""Regression test for Issue #142 B01: Substring matching too permissive for wee runtime.

When OpenRouter has 349+ models including multi-namespace ones like
'openrouter/openai/gpt-5-mini', substring matching should NOT match
'gpt-5-mini' from static aliases to that dynamic model.

Expected: get_model_from_name('gpt-5-mini', 'wee') returns None
(so the session can replace it with a valid wee default)

The fix: For wee runtime, skip substring matching on models with 2+ slashes
(multi-namespace like 'openrouter/provider/model') while allowing single-namespace
matches (1 slash like 'ollama/model').
"""

import pytest
from unittest.mock import patch
from agent_manager import SessionManager


class TestIssue142B01SubstringMatching:
    """Regression tests for substring matching blocker."""

    @pytest.fixture
    def session_mgr(self):
        """Create a SessionManager instance."""
        mgr = SessionManager()
        return mgr

    def test_stale_copilot_model_not_matched_to_openrouter(self, session_mgr):
        """KEY TEST: gpt-5-mini should NOT match openrouter/openai/gpt-5-mini via substring.

        This is the B01 BLOCKER FIX: multi-namespace models should not match
        on substring for wee runtime.
        """
        models_dict = {
            "Ollama": [],
            "OpenRouter": ["openrouter/openai/gpt-5-mini"],  # 2+ slashes - should NOT match
        }

        with patch.object(session_mgr, "get_models_for_runtime", return_value=models_dict):
            result = session_mgr.get_model_from_name("gpt-5-mini", "wee")
            assert (
                result is None
            ), "Multi-namespace models (2+ slashes) should NOT substring-match for wee"

    def test_exact_prefix_match_still_works(self, session_mgr):
        """Exact prefix-stripped matches should still work.

        Example: 'gemma4:e4b' should match 'ollama/gemma4:e4b'
        """
        models_dict = {
            "Ollama": ["ollama/gemma4:e4b"],
        }

        with patch.object(session_mgr, "get_models_for_runtime", return_value=models_dict):
            result = session_mgr.get_model_from_name("gemma4:e4b", "wee")
            assert (
                result == "ollama/gemma4:e4b"
            ), "Prefix-stripped exact match should work"

    def test_ollama_substring_match_still_works(self, session_mgr):
        """Ollama models (single slash) can still substring-match for wee.

        Example: 'gemma' should match 'ollama/gemma4:e4b'
        """
        models_dict = {
            "Ollama": ["ollama/gemma4:e4b"],  # Single slash - should allow substring
        }

        with patch.object(session_mgr, "get_models_for_runtime", return_value=models_dict):
            result = session_mgr.get_model_from_name("gemma", "wee")
            # Will find shortest match - could be 'ollama/gemma4:e4b'
            assert result is not None, "Single-slash Ollama models should substring-match"
            assert "gemma" in result.lower(), f"Result should contain 'gemma': {result}"

    def test_other_runtimes_match_multi_namespace(self, session_mgr):
        """Non-wee runtimes can still substring-match multi-namespace models.

        This ensures the fix is specific to wee runtime.
        """
        models_dict = {
            "Models": ["provider/sub-provider/model-3.5-sonnet"],  # 2+ slashes
        }

        with patch.object(session_mgr, "get_models_for_runtime", return_value=models_dict):
            # Non-wee runtimes should allow substring matching on all models
            result = session_mgr.get_model_from_name("3.5-sonnet", "claude")
            assert result is not None, "Non-wee runtime should substring-match any model"
            assert "3.5-sonnet" in result.lower(), f"Result should contain search term: {result}"
