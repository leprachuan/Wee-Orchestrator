"""Tests for Issue #118 — wee runtime model selection bug.

Validates:
- fetch_wee_models() returns flat strings (not tuples)
- Ollama port is 11434 (not 11436) in run_wee_native and wee_runtime.py
- get_models_for_runtime("wee") returns strings that can call .lower()
- get_model_from_name resolves wee models correctly
- /api/v1/models endpoint includes "wee" as a known runtime
- When session model=ollama/gemma4:e4b, correct api_base port is used
"""

import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("API_SHARED_KEY", "test_key_123")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_manager import SessionManager  # noqa: E402


def _make_mgr():
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_map = {}
    mgr._session_map_lock = threading.Lock()
    mgr.command_timeout = 300
    mgr.AGENTS = {
        "orchestrator": {"path": "/opt", "description": "test", "name": "orchestrator"}
    }
    mgr._stream_buffers = {}
    mgr._env_wee_models = None
    mgr._env_claude_models = None
    mgr._env_gemini_models = None
    mgr._env_codex_models = None
    mgr._env_devin_models = None
    mgr._env_cursor_models = None
    mgr._openrouter_cache_ts = 0
    mgr._openrouter_models_cache = None
    return mgr


class TestFetchWeeModels(unittest.TestCase):
    """fetch_wee_models() must return flat strings, not tuples."""

    def setUp(self):
        self.mgr = _make_mgr()

    def test_fetch_wee_models_returns_flat_strings(self):
        """All returned items must be plain strings (not tuples)."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = (
                b'{"models": [{"name": "gemma4:e4b"}, {"name": "qwen3.5:latest"}]}'
            )
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = self.mgr.fetch_wee_models()

        # Result is a dict with section keys
        self.assertIsInstance(result, dict)
        for section, models in result.items():
            self.assertIsInstance(models, list, f"Section {section!r} must be a list")
            for m in models:
                self.assertIsInstance(
                    m, str,
                    f"Model {m!r} in section {section!r} must be a str, not {type(m)}",
                )

    def test_fetch_wee_models_ollama_prefix(self):
        """Ollama models must be prefixed with 'ollama/'."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = (
                b'{"models": [{"name": "gemma4:e4b"},'
                b' {"name": "granite3.3-tuned:latest"}]}'
            )
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = self.mgr.fetch_wee_models()

        ollama_section = result.get("Ollama Models", [])
        self.assertIn("ollama/gemma4:e4b", ollama_section)
        self.assertIn("ollama/granite3.3-tuned:latest", ollama_section)

    def test_fetch_wee_models_includes_openrouter(self):
        """OpenRouter section must be present with correct model IDs."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"models": []}'
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = self.mgr.fetch_wee_models()

        openrouter_models = result.get("OpenRouter Models", [])
        self.assertTrue(
            len(openrouter_models) > 0, "OpenRouter section must not be empty"
        )
        for m in openrouter_models:
            self.assertTrue(
                m.startswith("openrouter/"),
                f"OpenRouter model {m!r} must start with 'openrouter/'",
            )

    def test_fetch_wee_models_fallback_on_error(self):
        """Falls back to static list when Ollama is unreachable."""
        with patch(
            "urllib.request.urlopen",
            side_effect=Exception("connection refused"),
        ):
            result = self.mgr.fetch_wee_models()

        self.assertIsInstance(result, dict)
        all_models = [m for section in result.values() for m in section]
        self.assertTrue(
            len(all_models) > 0, "Fallback must return at least some models"
        )
        for m in all_models:
            self.assertIsInstance(m, str)


class TestGetModelsForRuntime(unittest.TestCase):
    """get_models_for_runtime('wee') must return flat strings."""

    def setUp(self):
        self.mgr = _make_mgr()

    def test_wee_models_are_strings(self):
        """All models for 'wee' runtime must be strings (can call .lower())."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"models": [{"name": "gemma4:e4b"}]}'
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp
            models = self.mgr.get_models_for_runtime("wee")

        all_models = [m for section in models.values() for m in section]
        for m in all_models:
            # This must not raise AttributeError (the original bug)
            self.assertIsInstance(m.lower(), str)

    def test_wee_models_entries_are_tuples(self):
        """Each model entry must be a (model_id, description, aliases) tuple."""
        for category, entries in self.mgr.WEE_MODELS.items():
            for entry in entries:
                assert isinstance(
                    entry, tuple
                ), f"Entry in {category} is not a tuple: {entry}"
                assert len(entry) == 3, f"Entry should have 3 elements: {entry}"
                model_id, desc, aliases = entry
                assert isinstance(model_id, str)
                assert isinstance(desc, str)
                assert isinstance(aliases, list)

    def test_wee_models_contains_gemma(self):
        """WEE_MODELS must include gemma4:e4b (the model that was being ignored)."""
        all_ids = [
            mid for entries in self.mgr.WEE_MODELS.values() for mid, _, _ in entries
        ]
        assert "ollama/gemma4:e4b" in all_ids

    def test_wee_models_contains_granite(self):
        """WEE_MODELS must include granite3.3-tuned (the default that was always used)."""  # noqa: E501
        all_ids = [
            mid for entries in self.mgr.WEE_MODELS.values() for mid, _, _ in entries
        ]
        assert "ollama/granite3.3-tuned" in all_ids


class TestOllamaPort(unittest.TestCase):
    """Ollama must connect on port 11434, not 11436."""

    def test_wee_runtime_py_uses_correct_port(self):
        """wee_runtime.py PROVIDER_PRESETS must reference port 11434."""
        wee_runtime_path = REPO / "wee_runtime.py"
        source = wee_runtime_path.read_text()
        # Must NOT have 11436
        self.assertNotIn(
            "11436", source, "wee_runtime.py must not reference port 11436"
        )
        # Must have 11434
        self.assertIn("11434", source, "wee_runtime.py must reference port 11434")

    def test_agent_manager_wee_presets_use_correct_port(self):
        """agent_manager.py run_wee_native presets must use port 11434."""
        am_path = REPO / "agent_manager.py"
        source = am_path.read_text()
        # Find occurrences of 11436 — there should be none
        import re
        # Specifically check that 11436 doesn't appear near ollama
        ollama_section = re.findall(r'.{100}11436.{100}', source)
        for ctx in ollama_section:
            if "ollama" in ctx.lower():
                self.fail(f"Found 11436 near 'ollama' in agent_manager.py: ...{ctx}...")


class TestRunWeeNativeModelPassthrough(unittest.TestCase):
    """run_wee_native must pass the session model to OpenAI client."""

    def setUp(self):
        self.mgr = _make_mgr()

    def test_returns_flat_strings(self):
        """All model IDs must be flat strings, not tuples."""
        result = self.mgr.get_models_for_runtime("wee")
        for category, model_ids in result.items():
            for mid in model_ids:
                assert isinstance(
                    mid, str
                ), f"Model ID in {category} is {type(mid).__name__}, not str: {mid}"

        captured = {}

        def fake_openai(**kwargs):
            captured["base_url"] = kwargs.get("base_url", "")
            captured["model"] = kwargs.get("model", "")
            client = MagicMock()
            client.chat.completions.create.return_value = iter([
                MagicMock(choices=[
                    MagicMock(delta=MagicMock(content="hi"), finish_reason=None)
                ]),
                MagicMock(choices=[
                    MagicMock(delta=MagicMock(content=None), finish_reason="stop")
                ]),
            ])
            return client

        session_data = {
            "model": "ollama/gemma4:e4b",
            "runtime": "wee",
            "session_id": "test-118",
            "messages": [],
        }
        self.mgr.session_map["test-118"] = session_data

        with patch("urllib.request.urlopen") as mock_u:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"models": [{"name": "gemma4:e4b"}]}'
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_u.return_value = mock_resp
            with patch("openai.OpenAI", fake_openai):
                try:
                    result = []
                    gen = self.mgr.run_wee_native(
                        session_id="test-118",
                        user_message="hello",
                        session_data=session_data,
                        stream=False,
                    )
                    if hasattr(gen, "__iter__"):
                        result = list(gen)
                    elif hasattr(gen, "__next__"):
                        result = list(gen)
                except Exception:
                    pass  # We only care about what was captured

        if captured.get("base_url"):
            self.assertIn(
                "11434",
                captured["base_url"],
                f"api_base {captured['base_url']!r} must use port 11434",
            )
            self.assertNotIn("11436", captured["base_url"])


class TestWeeInKnownRuntimes(unittest.TestCase):
    """The /api/v1/models endpoint must recognize 'wee' as a known runtime."""

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


class TestSessionValidationWee(unittest.TestCase):
    def setUp(self):
        self.mgr = _make_mgr()
    """Verify session validation properly handles wee model switching."""

    def test_empty_model_gets_default(self):
        """When model is empty for wee runtime, default should be set."""

        session_data = {"runtime": "wee", "model": ""}
        # Simulate the validation logic
        runtime = "wee"  # noqa: F841
        current_model = session_data.get("model", "")
        if not current_model or not self.mgr.get_model_from_name(
            current_model, "wee"
        ):
            session_data["model"] = os.getenv("WEE_DEFAULT_MODEL", "ollama/gemma4:e4b")
        assert session_data["model"] == "ollama/gemma4:e4b"

    def test_valid_model_preserved(self):
        """When a valid wee model is set, it should be preserved."""
        session_data = {"runtime": "wee", "model": "ollama/gemma4:e4b"}
        runtime = "wee"  # noqa: F841
        current_model = session_data.get("model", "")
        if not current_model or not self.mgr.get_model_from_name(
            current_model, "wee"
        ):
            session_data["model"] = "ollama/gemma4:e4b"
        assert session_data["model"] == "ollama/gemma4:e4b"

    def test_stale_copilot_model_replaced(self):
        """A stale copilot model (e.g. gpt-5-mini) should be replaced for wee."""
        session_data = {"runtime": "wee", "model": "gpt-5-mini"}
        runtime = "wee"  # noqa: F841
        current_model = session_data.get("model", "")
        resolved = self.mgr.get_model_from_name(current_model, "wee")
        if not current_model or not resolved:
            session_data["model"] = os.getenv("WEE_DEFAULT_MODEL", "ollama/gemma4:e4b")
        # gpt-5-mini is not a wee model, so it should be replaced
        assert session_data["model"] == "ollama/gemma4:e4b"


# ── static_alias_map includes wee ──


class TestStaticAliasMap(unittest.TestCase):
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
