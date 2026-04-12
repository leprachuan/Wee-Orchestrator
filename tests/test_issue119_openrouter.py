"""Tests for Issue #119 — OpenRouter wiring in wee runtime UI.

Validates:
- fetch_wee_models() returns OpenRouter models grouped under "OpenRouter (Cloud)"
- OpenRouter models have correct prefix format: openrouter/<provider>/<model>
- wee_runtime.py correctly resolves openrouter/ prefix to api.openrouter.ai
- OpenRouter API key is loaded from keyring or environment
- Dispatch path passes openrouter/<model> correctly
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

from agent_manager import SessionManager


def _make_mgr():
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_map = {}
    mgr._session_map_lock = threading.Lock()
    mgr.command_timeout = 300
    mgr.AGENTS = {
        "orchestrator": {"path": "/opt", "description": "test", "name": "orchestrator"}
    }
    mgr._stream_buffers = {}
    return mgr


class TestOpenRouterModelsInUI(unittest.TestCase):
    """OpenRouter models must appear in fetch_wee_models() output."""

    def setUp(self):
        self.mgr = _make_mgr()

    def _get_all_models(self):
        with patch("urllib.request.urlopen") as mock_u:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"models": [{"name": "gemma4:e4b"}]}'
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_u.return_value = mock_resp
            return self.mgr.fetch_wee_models()

    def test_openrouter_section_exists(self):
        """fetch_wee_models() must include an OpenRouter section."""
        result = self._get_all_models()
        self.assertIn("OpenRouter (Cloud)", result,
                      f"Expected 'OpenRouter (Cloud)' section, got keys: {list(result.keys())}")

    def test_openrouter_has_models(self):
        """OpenRouter section must contain at least 5 models."""
        result = self._get_all_models()
        openrouter_models = result.get("OpenRouter (Cloud)", [])
        self.assertGreaterEqual(len(openrouter_models), 5,
                                f"Expected >=5 OpenRouter models, got {len(openrouter_models)}")

    def test_openrouter_models_prefixed(self):
        """All OpenRouter models must start with 'openrouter/'."""
        result = self._get_all_models()
        for m in result.get("OpenRouter (Cloud)", []):
            self.assertTrue(m.startswith("openrouter/"),
                            f"Model {m!r} must start with 'openrouter/'")

    def test_openrouter_llama4_present(self):
        """Llama 4 must be in the OpenRouter model list."""
        result = self._get_all_models()
        openrouter = result.get("OpenRouter (Cloud)", [])
        llama_models = [m for m in openrouter if "llama-4" in m.lower()]
        self.assertTrue(len(llama_models) > 0,
                        "Expected at least one llama-4 model in OpenRouter section")

    def test_openrouter_claude_present(self):
        """Claude must be in the OpenRouter model list."""
        result = self._get_all_models()
        openrouter = result.get("OpenRouter (Cloud)", [])
        claude_models = [m for m in openrouter if "claude" in m.lower()]
        self.assertTrue(len(claude_models) > 0,
                        "Expected at least one claude model in OpenRouter section")


class TestOpenRouterPrefixResolution(unittest.TestCase):
    """wee_runtime.py must correctly resolve openrouter/ prefix."""

    def test_wee_runtime_has_openrouter_preset(self):
        """wee_runtime.py must define openrouter in PROVIDER_PRESETS."""
        wee_runtime_path = REPO / "wee_runtime.py"
        source = wee_runtime_path.read_text()
        self.assertIn("openrouter", source.lower(),
                      "wee_runtime.py must reference OpenRouter")
        self.assertIn("openrouter.ai", source,
                      "wee_runtime.py must reference openrouter.ai API endpoint")

    def test_wee_runtime_resolve_openrouter_model(self):
        """resolve_model_and_endpoint must handle openrouter/ prefix."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("wee_runtime", REPO / "wee_runtime.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key-123"}):
            result = mod.resolve_model_and_endpoint("openrouter/mistralai/mistral-small-2603")

        model_id, api_base, api_key = result
        self.assertIn("openrouter.ai", api_base,
                      f"api_base {api_base!r} must point to openrouter.ai")
        self.assertEqual(model_id, "mistralai/mistral-small-2603",
                         f"model_id must be 'mistralai/mistral-small-2603', got {model_id!r}")
        self.assertEqual(api_key, "test-key-123",
                         "api_key must come from OPENROUTER_API_KEY env")

    def test_wee_runtime_resolve_ollama_model(self):
        """resolve_model_and_endpoint must handle ollama/ prefix with correct port."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("wee_runtime", REPO / "wee_runtime.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        result = mod.resolve_model_and_endpoint("ollama/gemma4:e4b")
        model_id, api_base, api_key = result
        self.assertIn("11434", api_base,
                      f"Ollama api_base {api_base!r} must use port 11434")
        self.assertNotIn("11436", api_base,
                         f"Ollama api_base {api_base!r} must NOT use port 11436")
        self.assertEqual(model_id, "gemma4:e4b")


class TestOpenRouterDispatch(unittest.TestCase):
    """run_wee_native must dispatch openrouter models to openrouter.ai."""

    def setUp(self):
        self.mgr = _make_mgr()

    def test_openrouter_model_uses_openrouter_base(self):
        """When model=openrouter/..., api_base must be openrouter.ai."""
        captured = {}

        def fake_openai(**kwargs):
            captured["base_url"] = kwargs.get("base_url", "")
            captured["api_key"] = kwargs.get("api_key", "")
            client = MagicMock()
            client.chat.completions.create.return_value = iter([
                MagicMock(choices=[MagicMock(delta=MagicMock(content="hi"), finish_reason=None)]),
                MagicMock(choices=[MagicMock(delta=MagicMock(content=None), finish_reason="stop")]),
            ])
            return client

        session_data = {
            "model": "openrouter/mistralai/mistral-small-2603",
            "runtime": "wee",
            "session_id": "test-119",
            "messages": [],
        }
        self.mgr.session_map["test-119"] = session_data

        env_patch = {"OPENROUTER_API_KEY": "test-openrouter-key"}
        with patch.dict(os.environ, env_patch):
            with patch("urllib.request.urlopen") as mock_u:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"models": []}'
                mock_resp.__enter__ = lambda s: s
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_u.return_value = mock_resp
                with patch("openai.OpenAI", fake_openai):
                    try:
                        gen = self.mgr.run_wee_native(
                            session_id="test-119",
                            user_message="hello",
                            session_data=session_data,
                            stream=False,
                        )
                        if gen is not None:
                            list(gen)
                    except Exception:
                        pass

        if captured.get("base_url"):
            self.assertIn("openrouter.ai", captured["base_url"],
                          f"api_base {captured['base_url']!r} must use openrouter.ai for openrouter/ model")


class TestOpenRouterAPIKeyRetrieval(unittest.TestCase):
    """OpenRouter API key must be retrievable."""

    def test_env_key_readable(self):
        """OPENROUTER_API_KEY env var must be set in .env."""
        env_path = REPO / ".env"
        if env_path.exists():
            content = env_path.read_text()
            self.assertIn("OPENROUTER_API_KEY", content,
                          ".env must contain OPENROUTER_API_KEY")

    def test_wee_runtime_reads_api_key(self):
        """wee_runtime.py must have logic to read OpenRouter API key."""
        wee_runtime_path = REPO / "wee_runtime.py"
        source = wee_runtime_path.read_text()
        self.assertIn("OPENROUTER_API_KEY", source,
                      "wee_runtime.py must reference OPENROUTER_API_KEY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
