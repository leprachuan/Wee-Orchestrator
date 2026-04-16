"""Regression test for Issue #142 B01: Substring matching too permissive for wee runtime.  # noqa: E501

When OpenRouter has 349+ models including multi-namespace ones like
'openrouter/openai/gpt-5-mini', substring matching should NOT match
'gpt-5-mini' from static aliases to that dynamic model.

The fix: For wee runtime, skip substring matching on models with 2+ slashes
(multi-namespace like 'openrouter/provider/model') while allowing single-namespace
matches (1 slash like 'ollama/model').
"""

from unittest.mock import patch

import pytest

from agent_manager import SessionManager


class TestIssue142B01SubstringMatching:
    """Regression tests for substring matching blocker."""

    @pytest.fixture
    def session_mgr(self):
        """Create a SessionManager instance."""
        mgr = SessionManager()
        return mgr

    def test_stale_copilot_model_not_matched_to_openrouter(self, session_mgr):
        """KEY TEST: gpt-5-mini should NOT match openrouter/openai/gpt-5-mini.

        This is the B01 BLOCKER FIX.
        """
        models_dict = {
            "Ollama": [],
            "OpenRouter": ["openrouter/openai/gpt-5-mini"],
        }
        with patch.object(
            session_mgr, "get_models_for_runtime", return_value=models_dict
        ):
            result = session_mgr.get_model_from_name("gpt-5-mini", "wee")
        assert (
            result is None
        ), "Multi-namespace models should NOT substring-match for wee"

    def test_exact_prefix_match_still_works(self, session_mgr):
        """Exact prefix-stripped matches should still work."""
        models_dict = {
            "Ollama": ["ollama/gemma4:e4b"],
        }
        with patch.object(
            session_mgr, "get_models_for_runtime", return_value=models_dict
        ):
            result = session_mgr.get_model_from_name("gemma4:e4b", "wee")
        assert result == "ollama/gemma4:e4b"

    def test_ollama_substring_match_still_works(self, session_mgr):
        """Ollama models (single slash) can still substring-match for wee."""
        models_dict = {
            "Ollama": ["ollama/gemma4:e4b"],
        }
        with patch.object(
            session_mgr, "get_models_for_runtime", return_value=models_dict
        ):
            result = session_mgr.get_model_from_name("gemma", "wee")
        assert result is not None, "Single-slash Ollama models should substring-match"
        assert "gemma" in result.lower()

    def test_other_runtimes_match_multi_namespace(self, session_mgr):
        """Non-wee runtimes can still substring-match multi-namespace models."""
        models_dict = {
            "Models": ["provider/sub-provider/model-3.5-sonnet"],
        }
        with patch.object(
            session_mgr, "get_models_for_runtime", return_value=models_dict
        ):
            result = session_mgr.get_model_from_name("3.5-sonnet", "claude")
        assert result is not None
        assert "3.5-sonnet" in result.lower()

    def test_openrouter_full_name_exact_match(self, session_mgr):
        """Full OpenRouter model name matches exactly."""
        models_dict = {
            "OpenRouter": ["openrouter/openai/gpt-5-mini"],
        }
        with patch.object(
            session_mgr, "get_models_for_runtime", return_value=models_dict
        ):
            result = session_mgr.get_model_from_name(
                "openrouter/openai/gpt-5-mini", "wee"
            )
        assert result == "openrouter/openai/gpt-5-mini"

    def test_openrouter_prefix_stripped_match(self, session_mgr):
        """OpenRouter models match via prefix-stripped exact match."""
        models_dict = {
            "OpenRouter": ["openrouter/openai/gpt-5-mini"],
        }
        with patch.object(
            session_mgr, "get_models_for_runtime", return_value=models_dict
        ):
            result = session_mgr.get_model_from_name("openai/gpt-5-mini", "wee")
        assert result == "openrouter/openai/gpt-5-mini"

    def test_multiple_models_bare_name_blocked(self, session_mgr):
        """Bare names from other runtimes don't match OpenRouter via substring."""
        models_dict = {
            "Ollama": ["ollama/custom-7b"],
            "OpenRouter": [
                "openrouter/openai/gpt-5-mini",
                "openrouter/anthropic/claude-3.5-sonnet",
            ],
        }
        with patch.object(type(session_mgr), "WEE_MODELS", {}):
            with patch.object(
                session_mgr, "get_models_for_runtime", return_value=models_dict
            ):
                assert session_mgr.get_model_from_name("gpt-5-mini", "wee") is None
                assert (
                    session_mgr.get_model_from_name("claude-3.5-sonnet", "wee") is None
                )
                assert (
                    session_mgr.get_model_from_name("custom", "wee")
                    == "ollama/custom-7b"
                )
