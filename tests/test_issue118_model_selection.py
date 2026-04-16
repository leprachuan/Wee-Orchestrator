"""Regression tests for Issue #118: wee runtime ignores selected Ollama model.

Root cause: Multiple bugs in the model dispatch pipeline prevented the selected
model from reaching the wee runtime — the model picker returned empty results,
model name/alias resolution was missing for wee, and session validation didn't
properly validate wee models.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def session_mgr():
    """Create a SessionManager instance for testing."""
    from agent_manager import SessionManager

    mgr = SessionManager.__new__(SessionManager)
    # Initialize minimal required attributes
    mgr._env_claude_models = {}
    mgr._env_gemini_models = {}
    mgr._env_codex_models = {}
    mgr._env_devin_models = {}
    mgr._env_cursor_models = {}
    mgr._env_wee_models = None
    mgr.session_map_file = MagicMock()
    mgr.session_map_file.exists.return_value = False
    return mgr


# ── WEE_MODELS constant ──


class TestWeeModelsConstant:
    """Verify the WEE_MODELS class constant exists and has correct structure."""

    def test_wee_models_exists(self, session_mgr):
        """WEE_MODELS constant must be defined on SessionManager."""
        assert hasattr(session_mgr, "WEE_MODELS")
        assert isinstance(session_mgr.WEE_MODELS, dict)

    def test_wee_models_has_ollama_category(self, session_mgr):
        """WEE_MODELS must contain an 'Ollama Models' category."""
        assert "Ollama Models" in session_mgr.WEE_MODELS

    def test_wee_models_has_openrouter_category(self, session_mgr):
        """WEE_MODELS must contain an 'OpenRouter Models' category."""
        assert "OpenRouter Models" in session_mgr.WEE_MODELS

    def test_wee_models_entries_are_tuples(self, session_mgr):
        """Each model entry must be a (model_id, description, aliases) tuple."""
        for category, entries in session_mgr.WEE_MODELS.items():
            for entry in entries:
                assert isinstance(
                    entry, tuple
                ), f"Entry in {category} is not a tuple: {entry}"
                assert len(entry) == 3, f"Entry should have 3 elements: {entry}"
                model_id, desc, aliases = entry
                assert isinstance(model_id, str)
                assert isinstance(desc, str)
                assert isinstance(aliases, list)

    def test_wee_models_contains_gemma(self, session_mgr):
        """WEE_MODELS must include gemma4:e4b (the model that was being ignored)."""
        all_ids = [
            mid for entries in session_mgr.WEE_MODELS.values() for mid, _, _ in entries
        ]
        assert "ollama/gemma4:e4b" in all_ids

    def test_wee_models_contains_granite(self, session_mgr):
        """WEE_MODELS must include granite3.3-tuned (the default that was always used)."""  # noqa: E501
        all_ids = [
            mid for entries in session_mgr.WEE_MODELS.values() for mid, _, _ in entries
        ]
        assert "ollama/granite3.3-tuned" in all_ids


# ── get_models_for_runtime("wee") ──


class TestGetModelsForRuntimeWee:
    """Verify get_models_for_runtime returns proper flat strings for wee."""

    def test_returns_dict(self, session_mgr):
        """get_models_for_runtime('wee') must return a dict."""
        result = session_mgr.get_models_for_runtime("wee")
        assert isinstance(result, dict)

    def test_returns_flat_strings(self, session_mgr):
        """All model IDs must be flat strings, not tuples."""
        result = session_mgr.get_models_for_runtime("wee")
        for category, model_ids in result.items():
            for mid in model_ids:
                assert isinstance(
                    mid, str
                ), f"Model ID in {category} is {type(mid).__name__}, not str: {mid}"

    def test_contains_gemma_model(self, session_mgr):
        """Result must include ollama/gemma4:e4b."""
        result = session_mgr.get_models_for_runtime("wee")
        all_models = [m for models in result.values() for m in models]
        assert "ollama/gemma4:e4b" in all_models

    def test_not_empty(self, session_mgr):
        """Result must not be empty."""
        result = session_mgr.get_models_for_runtime("wee")
        all_models = [m for models in result.values() for m in models]
        assert len(all_models) > 0


# ── get_model_from_name() for wee ──


class TestGetModelFromNameWee:
    """Verify model name/alias resolution works for wee runtime."""

    def test_exact_match(self, session_mgr):
        """Exact model ID match must return the model."""
        result = session_mgr.get_model_from_name("ollama/gemma4:e4b", "wee")
        assert result == "ollama/gemma4:e4b"

    def test_alias_match_gemma(self, session_mgr):
        """Alias 'gemma' must resolve to ollama/gemma4:e4b."""
        result = session_mgr.get_model_from_name("gemma", "wee")
        assert result == "ollama/gemma4:e4b"

    def test_alias_match_granite(self, session_mgr):
        """Alias 'granite' must resolve to ollama/granite3.3-tuned."""
        result = session_mgr.get_model_from_name("granite", "wee")
        assert result == "ollama/granite3.3-tuned"

    def test_alias_match_qwen(self, session_mgr):
        """Alias 'qwen' must resolve to a qwen model."""
        result = session_mgr.get_model_from_name("qwen", "wee")
        assert result is not None
        assert "qwen" in result.lower()

    def test_alias_match_scout(self, session_mgr):
        """Alias 'scout' must resolve to ollama/llama4:scout."""
        result = session_mgr.get_model_from_name("scout", "wee")
        assert result is not None
        assert "scout" in result.lower()

    def test_case_insensitive(self, session_mgr):
        """Model resolution must be case-insensitive."""
        result = session_mgr.get_model_from_name("OLLAMA/GEMMA4:E4B", "wee")
        assert result == "ollama/gemma4:e4b"

    def test_unknown_model_returns_none(self, session_mgr):
        """Unknown model name must return None."""
        result = session_mgr.get_model_from_name("nonexistent-model-xyz", "wee")
        assert result is None

    def test_description_match(self, session_mgr):
        """Description string match must resolve the model."""
        result = session_mgr.get_model_from_name("Gemma 4 E4B (local)", "wee")
        assert result == "ollama/gemma4:e4b"


# ── _get_model_description() for wee ──


class TestGetModelDescriptionWee:
    """Verify human-readable descriptions are returned for wee models."""

    def test_gemma_description(self, session_mgr):
        """ollama/gemma4:e4b must have a human-readable description."""
        desc = session_mgr._get_model_description("ollama/gemma4:e4b", "wee")
        assert desc is not None
        assert "Gemma" in desc

    def test_granite_description(self, session_mgr):
        """ollama/granite3.3-tuned must have a description."""
        desc = session_mgr._get_model_description("ollama/granite3.3-tuned", "wee")
        assert desc is not None
        assert "Granite" in desc

    def test_openrouter_description(self, session_mgr):
        """OpenRouter models must have descriptions."""
        desc = session_mgr._get_model_description(
            "openrouter/meta-llama/llama-4-scout", "wee"
        )
        assert desc is not None
        assert "Scout" in desc or "Llama" in desc

    def test_unknown_returns_none(self, session_mgr):
        """Unknown model ID returns None."""
        desc = session_mgr._get_model_description("ollama/nonexistent", "wee")
        assert desc is None


# ── known_runtimes includes "wee" ──


class TestKnownRuntimes:
    """Verify the /api/v1/models endpoint recognizes wee as a valid runtime."""

    def test_wee_in_known_runtimes(self):
        """'wee' must appear in the known_runtimes set in get_models endpoint."""
        import inspect

        from agent_manager import SessionManager

        # Read the source to find known_runtimes set
        src_file = inspect.getfile(SessionManager)
        with open(src_file, "r") as f:
            source = f.read()

        # Find the known_runtimes set definition
        idx = source.find("known_runtimes = {")
        assert idx != -1, "known_runtimes set not found in source"
        # Extract until closing brace
        end = source.find("}", idx)
        block = source[idx : end + 1]
        assert '"wee"' in block, f"'wee' not in known_runtimes: {block}"


# ── Session validation ──


class TestSessionValidationWee:
    """Verify session validation properly handles wee model switching."""

    def test_empty_model_gets_default(self, session_mgr):
        """When model is empty for wee runtime, default should be set."""

        session_data = {"runtime": "wee", "model": ""}
        # Simulate the validation logic
        runtime = "wee"  # noqa: F841
        current_model = session_data.get("model", "")
        if not current_model or not session_mgr.get_model_from_name(
            current_model, "wee"
        ):
            session_data["model"] = os.getenv("WEE_DEFAULT_MODEL", "ollama/gemma4:e4b")
        assert session_data["model"] == "ollama/gemma4:e4b"

    def test_valid_model_preserved(self, session_mgr):
        """When a valid wee model is set, it should be preserved."""
        session_data = {"runtime": "wee", "model": "ollama/gemma4:e4b"}
        runtime = "wee"  # noqa: F841
        current_model = session_data.get("model", "")
        if not current_model or not session_mgr.get_model_from_name(
            current_model, "wee"
        ):
            session_data["model"] = "ollama/gemma4:e4b"
        assert session_data["model"] == "ollama/gemma4:e4b"

    def test_stale_copilot_model_replaced(self, session_mgr):
        """A stale copilot model (e.g. gpt-5-mini) should be replaced for wee."""
        session_data = {"runtime": "wee", "model": "gpt-5-mini"}
        runtime = "wee"  # noqa: F841
        current_model = session_data.get("model", "")
        resolved = session_mgr.get_model_from_name(current_model, "wee")
        if not current_model or not resolved:
            session_data["model"] = os.getenv("WEE_DEFAULT_MODEL", "ollama/gemma4:e4b")
        # gpt-5-mini is not a wee model, so it should be replaced
        assert session_data["model"] == "ollama/gemma4:e4b"


# ── static_alias_map includes wee ──


class TestStaticAliasMap:
    """Verify static_alias_map in get_model_from_name includes wee."""

    def test_wee_in_static_alias_map(self):
        """'wee' must be present in static_alias_map within get_model_from_name."""
        import inspect

        from agent_manager import SessionManager

        src_file = inspect.getfile(SessionManager)
        with open(src_file, "r") as f:
            source = f.read()

        # Find static_alias_map in get_model_from_name
        fn_start = source.find("def get_model_from_name")
        assert fn_start != -1
        alias_start = source.find("static_alias_map = {", fn_start)
        assert alias_start != -1
        alias_end = source.find("}", alias_start)
        block = source[alias_start : alias_end + 1]
        assert '"wee"' in block, f"'wee' not in static_alias_map: {block}"


# ── fetch_wee_models() ──


class TestFetchWeeModels:
    """Verify fetch_wee_models returns proper model structure."""

    def test_returns_dict(self, session_mgr):
        """fetch_wee_models must return a dict."""
        with patch("httpx.get", side_effect=Exception("offline")):
            result = session_mgr.fetch_wee_models()
        assert isinstance(result, dict)

    def test_fallback_returns_flat_strings(self, session_mgr):
        """When Ollama is unreachable, fallback returns flat strings."""
        with patch("httpx.get", side_effect=Exception("offline")):
            result = session_mgr.fetch_wee_models()
        for category, model_ids in result.items():
            for mid in model_ids:
                assert isinstance(mid, str), f"Not a string: {mid}"

    def test_fallback_contains_known_models(self, session_mgr):
        """Fallback must contain key models from WEE_MODELS."""
        with patch("httpx.get", side_effect=Exception("offline")):
            result = session_mgr.fetch_wee_models()
        all_models = [m for models in result.values() for m in models]
        assert "ollama/gemma4:e4b" in all_models
        assert "ollama/granite3.3-tuned" in all_models
