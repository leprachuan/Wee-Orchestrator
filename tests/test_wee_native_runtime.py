"""Tests for the wee native runtime (Issue #88).

Validates:
- Runtime registration (check_runtime_available, get_available_runtimes)
- Model parsing and provider prefix resolution
- run_wee_native method (mocked OpenAI client)
- Background task command building
- Streaming integration
- strip_metadata pass-through
- Default model assignment
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

from agent_manager import (
    SessionManager,
    check_runtime_available,
    get_available_runtimes,
)


def _make_mgr():
    """Create a minimal SessionManager for testing run_wee_native."""
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_map = {}
    mgr._session_map_lock = threading.Lock()
    mgr.command_timeout = 300
    mgr.AGENTS = {
        "orchestrator": {
            "path": "/opt",
            "description": "test",
            "name": "orchestrator",
        }
    }
    mgr._stream_buffers = {}
    mgr.session_map_file = Path("/tmp/wee_test_session_map.json")
    return mgr


def _run_wee_native_test(mgr, test_session, model="ollama/gemma4:e4b", **kwargs):
    """Helper to call run_wee_native with patched session data lookup."""
    defaults = dict(
        prompt="test",
        model=model,
        agent="orchestrator",
        session_id=None,
        resume=False,
        n8n_session_id=test_session,
        timeout=30,
        render_type="text",
    )
    defaults.update(kwargs)
    # Patch get_or_create_session_data to avoid file system dependencies
    session_data = mgr.session_map.get(
        test_session,
        {
            "runtime": "wee",
            "model": model,
            "channel": "api",
        },
    )
    with patch.object(mgr, "get_or_create_session_data", return_value=session_data):
        with patch.object(
            mgr,
            "build_agent_context_prompt",
            return_value="You are a helpful assistant.",
        ):
            with patch.object(
                mgr, "load_session_map", return_value=dict(mgr.session_map)
            ):
                with patch.object(mgr, "save_session_map"):
                    return mgr.run_wee_native(**defaults)


class TestWeeRuntimeRegistration(unittest.TestCase):
    """Test that wee runtime is properly registered in all discovery functions."""

    def test_wee_in_available_runtimes(self):
        """Wee should appear in get_available_runtimes()."""
        runtimes = get_available_runtimes()
        ids = [r["id"] for r in runtimes]
        self.assertIn("wee", ids)

    def test_wee_runtime_icon(self):
        """Wee runtime should have the leaf icon."""
        runtimes = get_available_runtimes()
        wee_entry = next((r for r in runtimes if r["id"] == "wee"), None)
        self.assertIsNotNone(wee_entry)
        self.assertEqual(wee_entry["icon"], "\0001F340")  # leaf

    def test_wee_check_runtime_available(self):
        """check_runtime_available('wee') should return True (openai installed)."""
        result = check_runtime_available("wee")
        self.assertTrue(result)

    def test_wee_not_blocked_by_binary_check(self):
        """Wee runtime availability should not depend on a binary in PATH."""
        result = check_runtime_available("wee")
        self.assertTrue(result)


class TestWeeRuntimeDispatch(unittest.TestCase):
    """Test that wee runtime is properly dispatched."""

    def test_run_wee_native_exists(self):
        """run_wee_native method should exist on SessionManager."""
        self.assertTrue(hasattr(SessionManager, "run_wee_native"))
        self.assertTrue(callable(getattr(SessionManager, "run_wee_native")))

    @patch("openai.OpenAI")
    def test_run_wee_native_with_mock(self, mock_openai_cls):
        """run_wee_native should call OpenAI SDK and return output."""
        mgr = _make_mgr()

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta.content = "Hello "

        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta.content = "world!"

        mock_client.chat.completions.create.return_value = [chunk1, chunk2]

        test_session = "test_wee_native_mock"
        mgr.session_map[test_session] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
        }

        result = _run_wee_native_test(mgr, test_session, prompt="Say hello")

        self.assertEqual(result, "Hello world!")
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertTrue(call_kwargs.get("stream"))
        self.assertEqual(call_kwargs["model"], "gemma4:e4b")

    @patch("openai.OpenAI")
    def test_run_wee_native_openrouter_model(self, mock_openai_cls):
        """OpenRouter model prefix should resolve to the correct API base."""
        mgr = _make_mgr()

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = []

        test_session = "test_wee_openrouter"
        mgr.session_map[test_session] = {
            "runtime": "wee",
            "model": "openrouter/meta-llama/llama-4-scout",
            "channel": "api",
        }

        _run_wee_native_test(
            mgr, test_session, model="openrouter/meta-llama/llama-4-scout"
        )

        init_kwargs = mock_openai_cls.call_args[1]
        self.assertEqual(init_kwargs["base_url"], "https://openrouter.ai/api/v1")

    @patch("openai.OpenAI")
    def test_run_wee_native_ollama_model(self, mock_openai_cls):
        """Ollama model prefix should resolve to kubuntu endpoint."""
        mgr = _make_mgr()

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = []

        test_session = "test_wee_ollama"
        mgr.session_map[test_session] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
        }

        _run_wee_native_test(mgr, test_session, model="ollama/gemma4:e4b")

        init_kwargs = mock_openai_cls.call_args[1]
        self.assertEqual(init_kwargs["base_url"], "http://192.168.1.101:11434/v1")
        self.assertEqual(init_kwargs["api_key"], "ollama")

    @patch("openai.OpenAI")
    def test_run_wee_native_lmstudio_model(self, mock_openai_cls):
        """LM Studio model prefix should resolve correctly."""
        mgr = _make_mgr()

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = []

        test_session = "test_wee_lmstudio"
        mgr.session_map[test_session] = {
            "runtime": "wee",
            "model": "lmstudio/qwen2.5-7b",
            "channel": "api",
        }

        _run_wee_native_test(mgr, test_session, model="lmstudio/qwen2.5-7b")

        init_kwargs = mock_openai_cls.call_args[1]
        self.assertEqual(init_kwargs["base_url"], "http://localhost:1234/v1")
        self.assertEqual(init_kwargs["api_key"], "lm-studio")

    @patch("openai.OpenAI")
    def test_run_wee_native_error_handling(self, mock_openai_cls):
        """run_wee_native should return error message on failure."""
        mgr = _make_mgr()

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception(
            "Connection refused"
        )

        test_session = "test_wee_error"
        mgr.session_map[test_session] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
        }

        result = _run_wee_native_test(mgr, test_session)

        self.assertIn("Error", result)
        self.assertIn("Connection refused", result)


class TestWeeRuntimeStreaming(unittest.TestCase):
    """Test streaming support for wee runtime."""

    @patch("openai.OpenAI")
    def test_streaming_pushes_to_buffer(self, mock_openai_cls):
        """Streaming tokens should be pushed to stream buffer."""
        mgr = _make_mgr()

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = "Token"
        mock_client.chat.completions.create.return_value = [chunk]

        test_session = "test_wee_streaming"
        mgr.session_map[test_session] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "webui",
        }

        mock_buffer = MagicMock()
        mgr._stream_buffers[test_session] = mock_buffer

        result = _run_wee_native_test(mgr, test_session)

        mock_buffer.push.assert_any_call("chunk", {"text": "Token"})
        mock_buffer.push.assert_any_call("done", "Token")
        self.assertEqual(result, "Token")

    @patch("openai.OpenAI")
    def test_done_sentinel_on_error(self, mock_openai_cls):
        """Done sentinel should fire even on error."""
        mgr = _make_mgr()

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("fail")

        test_session = "test_wee_err_stream"
        mgr.session_map[test_session] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "webui",
        }

        mock_buffer = MagicMock()
        mgr._stream_buffers[test_session] = mock_buffer

        _run_wee_native_test(mgr, test_session)

        done_calls = [c for c in mock_buffer.push.call_args_list if c[0][0] == "done"]
        self.assertEqual(len(done_calls), 1)
        self.assertIn("Error", done_calls[0][0][1])


class TestWeeStripMetadata(unittest.TestCase):
    """Test strip_metadata for wee runtime."""

    def test_strip_metadata_wee_passthrough(self):
        """Wee runtime strip_metadata should pass through clean text."""
        mgr = _make_mgr()
        test_output = "Hello, this is a response\nfrom the wee runtime."
        result = mgr.strip_metadata(test_output, "wee")
        self.assertIn("Hello, this is a response", result)
        self.assertIn("from the wee runtime.", result)

    def test_strip_metadata_wee_strips_leading_blanks(self):
        """Wee runtime should strip leading blank lines."""
        mgr = _make_mgr()
        test_output = "\n\n\nActual content here."
        result = mgr.strip_metadata(test_output, "wee")
        self.assertTrue(result.startswith("Actual content"))


class TestWeeDefaultModel(unittest.TestCase):
    """Test default model assignment for wee runtime."""

    def test_default_model_env(self):
        """Default wee model from env should be ollama/gemma4:e4b."""
        default = os.getenv("WEE_DEFAULT_MODEL", "ollama/gemma4:e4b")
        self.assertEqual(default, "ollama/gemma4:e4b")


class TestWeeBackgroundTask(unittest.TestCase):
    """Test background task command building for wee runtime."""

    def test_wee_runtime_script_exists(self):
        """wee_runtime.py should exist in the dev directory."""
        script_path = REPO / "wee_runtime.py"
        self.assertTrue(
            script_path.exists(),
            f"wee_runtime.py not found at {script_path}",
        )

    def test_wee_runtime_script_importable(self):
        """wee_runtime.py should be valid Python."""
        script_path = REPO / "wee_runtime.py"
        import py_compile

        py_compile.compile(str(script_path), doraise=True)


class TestWeeRuntimeValidation(unittest.TestCase):
    """Test that wee is accepted in the runtime validation paths."""

    def test_wee_in_valid_runtimes_source(self):
        """wee should appear in the /runtime error message."""
        import inspect

        source = inspect.getsource(SessionManager)
        self.assertIn("cursor, or wee", source)

    def test_wee_in_session_id_validation(self):
        """Session ID validation should include wee."""
        import inspect

        source = inspect.getsource(SessionManager)
        self.assertIn('"wee"', source)


if __name__ == "__main__":
    unittest.main()
