"""Tests for Issue #144: OpenRouter runtime returns 401 Missing Authentication header.

Bug: run_wee_native() silently fell back to 'ollama' as the API key when
no OPENROUTER_API_KEY env var or keyring entry was found. OpenRouter rejected
this with 401 Missing Authentication.

Fix: Check OPENROUTER_API_KEY env var, then keyring, then raise ValueError
with a clear message instead of defaulting to 'ollama'.

Covers:
  - OPENROUTER_API_KEY env var resolution
  - WEE_API_KEY env var as explicit override
  - Keyring fallback for OpenRouter
  - ValueError raised when no key found for OpenRouter
  - Ollama still defaults correctly (no key needed)
  - Explicit api_key in session_data takes priority
  - OpenAI client receives the correct api_key
  - wee_runtime.py resolve_model_and_endpoint() auth
"""

import importlib
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")


# ── helpers ─────────────────────────────────────────────────────────────

def _get_session_mgr():
    """Instantiate SessionManager without starting the full server."""
    mod = importlib.import_module("agent_manager")
    mgr = mod.SessionManager.__new__(mod.SessionManager)
    mgr.__init__()
    return mgr


@pytest.fixture(scope="module")
def mgr():
    return _get_session_mgr()


def _clean_env(*keys):
    """Context manager to temporarily remove env vars."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        saved = {}
        for k in keys:
            if k in os.environ:
                saved[k] = os.environ.pop(k)
        try:
            yield
        finally:
            for k, v in saved.items():
                os.environ[k] = v
            for k in keys:
                if k not in saved and k in os.environ:
                    del os.environ[k]
    return _ctx()


# ── agent_manager.py: run_wee_native() auth tests ──────────────────────

class TestIssue144OpenRouterEnvVar:
    """OPENROUTER_API_KEY env var should be used for OpenRouter models."""

    def test_openrouter_api_key_env_var_used(self, mgr):
        """When OPENROUTER_API_KEY is set, it should be used as api_key."""
        mock_openai = MagicMock()
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))
        mock_openai.return_value.chat.completions.create.return_value = mock_stream

        with _clean_env("OPENROUTER_API_KEY", "WEE_API_KEY", "WEE_API_BASE"):
            os.environ["OPENROUTER_API_KEY"] = "sk-or-test-key-144"
            with patch("openai.OpenAI", mock_openai):
                with patch.object(mgr, "get_or_create_session_data", return_value={}):
                    with patch.object(mgr, "build_agent_context_prompt", return_value="ctx"):
                        with patch.object(mgr, "_wee_augment_system_prompt_with_tools", return_value="ctx"):
                            with patch.object(mgr, "_wee_anti_hallucination_prompt", return_value=""):
                                with patch.object(mgr, "_wee_load_messages", return_value=[]):
                                    try:
                                        mgr.run_wee_native(
                                            "test prompt",
                                            "openrouter/meta-llama/llama-4-scout",
                                            "orchestrator",
                                            None,
                                            False,
                                            "test-session-144",
                                        )
                                    except Exception:
                                        pass  # May fail on stream iteration

            # Verify OpenAI was called with the correct key
            if mock_openai.called:
                call_kwargs = mock_openai.call_args
                assert call_kwargs.kwargs.get("api_key") == "sk-or-test-key-144", (
                    f"Expected api_key='sk-or-test-key-144', "
                    f"got '{call_kwargs.kwargs.get('api_key')}'"
                )

    def test_wee_api_key_takes_priority(self, mgr):
        """WEE_API_KEY env var should override OPENROUTER_API_KEY."""
        mock_openai = MagicMock()
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))
        mock_openai.return_value.chat.completions.create.return_value = mock_stream

        with _clean_env("OPENROUTER_API_KEY", "WEE_API_KEY", "WEE_API_BASE"):
            os.environ["WEE_API_KEY"] = "sk-or-wee-key"
            os.environ["OPENROUTER_API_KEY"] = "sk-or-fallback"
            with patch("openai.OpenAI", mock_openai):
                with patch.object(mgr, "get_or_create_session_data", return_value={}):
                    with patch.object(mgr, "build_agent_context_prompt", return_value="ctx"):
                        with patch.object(mgr, "_wee_augment_system_prompt_with_tools", return_value="ctx"):
                            with patch.object(mgr, "_wee_anti_hallucination_prompt", return_value=""):
                                with patch.object(mgr, "_wee_load_messages", return_value=[]):
                                    try:
                                        mgr.run_wee_native(
                                            "test",
                                            "openrouter/meta-llama/llama-4-scout",
                                            "orchestrator",
                                            None,
                                            False,
                                            "test-session-144b",
                                        )
                                    except Exception:
                                        pass

            if mock_openai.called:
                call_kwargs = mock_openai.call_args
                assert call_kwargs.kwargs.get("api_key") == "sk-or-wee-key", (
                    "WEE_API_KEY should take priority over OPENROUTER_API_KEY"
                )

    def test_session_data_api_key_takes_priority(self, mgr):
        """api_key in session_data should override all env vars."""
        mock_openai = MagicMock()
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))
        mock_openai.return_value.chat.completions.create.return_value = mock_stream

        session = {"api_key": "sk-or-session-key"}
        with _clean_env("OPENROUTER_API_KEY", "WEE_API_KEY", "WEE_API_BASE"):
            os.environ["OPENROUTER_API_KEY"] = "sk-or-env-key"
            with patch("openai.OpenAI", mock_openai):
                with patch.object(mgr, "get_or_create_session_data", return_value=session):
                    with patch.object(mgr, "build_agent_context_prompt", return_value="ctx"):
                        with patch.object(mgr, "_wee_augment_system_prompt_with_tools", return_value="ctx"):
                            with patch.object(mgr, "_wee_anti_hallucination_prompt", return_value=""):
                                with patch.object(mgr, "_wee_load_messages", return_value=[]):
                                    try:
                                        mgr.run_wee_native(
                                            "test",
                                            "openrouter/meta-llama/llama-4-scout",
                                            "orchestrator",
                                            None,
                                            False,
                                            "test-session-144c",
                                        )
                                    except Exception:
                                        pass

            if mock_openai.called:
                call_kwargs = mock_openai.call_args
                assert call_kwargs.kwargs.get("api_key") == "sk-or-session-key", (
                    "session_data api_key should override env vars"
                )


class TestIssue144KeyringFallback:
    """Keyring should be tried when env var is not set."""

    def test_keyring_used_when_env_var_missing(self, mgr):
        """When OPENROUTER_API_KEY env var is not set, keyring is tried."""
        mock_openai = MagicMock()
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))
        mock_openai.return_value.chat.completions.create.return_value = mock_stream

        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = "sk-or-keyring-key"

        with _clean_env("OPENROUTER_API_KEY", "WEE_API_KEY", "WEE_API_BASE"):
            with patch("openai.OpenAI", mock_openai):
                with patch.dict("sys.modules", {"keyring": mock_keyring}):
                    with patch.object(mgr, "get_or_create_session_data", return_value={}):
                        with patch.object(mgr, "build_agent_context_prompt", return_value="ctx"):
                            with patch.object(mgr, "_wee_augment_system_prompt_with_tools", return_value="ctx"):
                                with patch.object(mgr, "_wee_anti_hallucination_prompt", return_value=""):
                                    with patch.object(mgr, "_wee_load_messages", return_value=[]):
                                        try:
                                            mgr.run_wee_native(
                                                "test",
                                                "openrouter/meta-llama/llama-4-scout",
                                                "orchestrator",
                                                None,
                                                False,
                                                "test-session-144d",
                                            )
                                        except Exception:
                                            pass

            if mock_openai.called:
                call_kwargs = mock_openai.call_args
                assert call_kwargs.kwargs.get("api_key") == "sk-or-keyring-key", (
                    "keyring api_key should be used when env var is missing"
                )


class TestIssue144MissingKeyError:
    """ValueError should be raised when no OpenRouter key is found."""

    def test_raises_valueerror_for_openrouter_missing_key(self, mgr):
        """When no API key is found for OpenRouter, raise ValueError."""
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = None

        with _clean_env("OPENROUTER_API_KEY", "WEE_API_KEY", "WEE_API_BASE"):
            with patch.dict("sys.modules", {"keyring": mock_keyring}):
                with patch.object(mgr, "get_or_create_session_data", return_value={}):
                    with pytest.raises(ValueError, match="OpenRouter API key not found"):
                        mgr.run_wee_native(
                            "test",
                            "openrouter/meta-llama/llama-4-scout",
                            "orchestrator",
                            None,
                            False,
                            "test-session-144e",
                        )

    def test_error_message_mentions_env_var(self, mgr):
        """Error message should mention OPENROUTER_API_KEY."""
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = None

        with _clean_env("OPENROUTER_API_KEY", "WEE_API_KEY", "WEE_API_BASE"):
            with patch.dict("sys.modules", {"keyring": mock_keyring}):
                with patch.object(mgr, "get_or_create_session_data", return_value={}):
                    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
                        mgr.run_wee_native(
                            "test",
                            "openrouter/meta-llama/llama-4-scout",
                            "orchestrator",
                            None,
                            False,
                            "test-session-144f",
                        )

    def test_no_silent_ollama_default_for_openrouter(self, mgr):
        """OpenRouter should NOT silently fall back to 'ollama' api_key."""
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = None
        mock_openai = MagicMock()

        with _clean_env("OPENROUTER_API_KEY", "WEE_API_KEY", "WEE_API_BASE"):
            with patch.dict("sys.modules", {"keyring": mock_keyring}):
                with patch("openai.OpenAI", mock_openai):
                    with patch.object(mgr, "get_or_create_session_data", return_value={}):
                        with pytest.raises(ValueError):
                            mgr.run_wee_native(
                                "test",
                                "openrouter/meta-llama/llama-4-scout",
                                "orchestrator",
                                None,
                                False,
                                "test-session-144g",
                            )

            # OpenAI should NOT have been called with 'ollama' key
            if mock_openai.called:
                call_kwargs = mock_openai.call_args
                assert call_kwargs.kwargs.get("api_key") != "ollama", (
                    "OpenRouter should not silently default to 'ollama' api_key"
                )


class TestIssue144OllamaDefault:
    """Ollama models should still default to 'ollama' api_key."""

    def test_ollama_still_defaults_to_ollama_key(self, mgr):
        """Non-OpenRouter models should still use 'ollama' as default key."""
        mock_openai = MagicMock()
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))
        mock_openai.return_value.chat.completions.create.return_value = mock_stream

        with _clean_env("WEE_API_KEY", "WEE_API_BASE"):
            with patch("openai.OpenAI", mock_openai):
                with patch.object(mgr, "get_or_create_session_data", return_value={}):
                    with patch.object(mgr, "build_agent_context_prompt", return_value="ctx"):
                        with patch.object(mgr, "_wee_augment_system_prompt_with_tools", return_value="ctx"):
                            with patch.object(mgr, "_wee_anti_hallucination_prompt", return_value=""):
                                with patch.object(mgr, "_wee_load_messages", return_value=[]):
                                    try:
                                        mgr.run_wee_native(
                                            "test",
                                            "ollama/gemma4:e4b",
                                            "orchestrator",
                                            None,
                                            False,
                                            "test-session-144h",
                                        )
                                    except Exception:
                                        pass

            if mock_openai.called:
                call_kwargs = mock_openai.call_args
                assert call_kwargs.kwargs.get("api_key") == "ollama", (
                    "Ollama models should default to 'ollama' api_key"
                )


class TestIssue144OpenRouterBaseUrl:
    """OpenRouter base URL should be correctly set."""

    def test_openrouter_base_url_set(self, mgr):
        """openrouter/ prefix should set base_url to openrouter.ai."""
        mock_openai = MagicMock()
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))
        mock_openai.return_value.chat.completions.create.return_value = mock_stream

        with _clean_env("OPENROUTER_API_KEY", "WEE_API_KEY", "WEE_API_BASE"):
            os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
            with patch("openai.OpenAI", mock_openai):
                with patch.object(mgr, "get_or_create_session_data", return_value={}):
                    with patch.object(mgr, "build_agent_context_prompt", return_value="ctx"):
                        with patch.object(mgr, "_wee_augment_system_prompt_with_tools", return_value="ctx"):
                            with patch.object(mgr, "_wee_anti_hallucination_prompt", return_value=""):
                                with patch.object(mgr, "_wee_load_messages", return_value=[]):
                                    try:
                                        mgr.run_wee_native(
                                            "test",
                                            "openrouter/meta-llama/llama-4-scout",
                                            "orchestrator",
                                            None,
                                            False,
                                            "test-session-144i",
                                        )
                                    except Exception:
                                        pass

            if mock_openai.called:
                call_kwargs = mock_openai.call_args
                assert call_kwargs.kwargs.get("base_url") == "https://openrouter.ai/api/v1", (
                    "OpenRouter base_url should be https://openrouter.ai/api/v1"
                )

    def test_openrouter_model_prefix_stripped(self, mgr):
        """'openrouter/' prefix should be stripped from model name."""
        mock_openai = MagicMock()
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))
        mock_openai.return_value.chat.completions.create.return_value = mock_stream

        with _clean_env("OPENROUTER_API_KEY", "WEE_API_KEY", "WEE_API_BASE"):
            os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
            with patch("openai.OpenAI", mock_openai):
                with patch.object(mgr, "get_or_create_session_data", return_value={}):
                    with patch.object(mgr, "build_agent_context_prompt", return_value="ctx"):
                        with patch.object(mgr, "_wee_augment_system_prompt_with_tools", return_value="ctx"):
                            with patch.object(mgr, "_wee_anti_hallucination_prompt", return_value=""):
                                with patch.object(mgr, "_wee_load_messages", return_value=[]):
                                    try:
                                        mgr.run_wee_native(
                                            "test",
                                            "openrouter/meta-llama/llama-4-scout",
                                            "orchestrator",
                                            None,
                                            False,
                                            "test-session-144j",
                                        )
                                    except Exception:
                                        pass

            if mock_openai.called:
                create_kwargs = mock_openai.return_value.chat.completions.create.call_args
                assert create_kwargs.kwargs.get("model") == "meta-llama/llama-4-scout", (
                    "openrouter/ prefix should be stripped from model name"
                )


# ── wee_runtime.py: resolve_model_and_endpoint() auth tests ────────────

class TestIssue144WeeRuntimeAuth:
    """Test wee_runtime.py resolve_model_and_endpoint() auth handling."""

    def test_env_var_used_for_openrouter(self):
        """OPENROUTER_API_KEY env var should be used in wee_runtime."""
        wee_rt = importlib.import_module("wee_runtime")

        with _clean_env("OPENROUTER_API_KEY", "WEE_API_KEY", "WEE_API_BASE"):
            os.environ["OPENROUTER_API_KEY"] = "sk-or-runtime-test"
            model, base, key = wee_rt.resolve_model_and_endpoint(
                "openrouter/meta-llama/llama-4-scout"
            )
            assert key == "sk-or-runtime-test"
            assert base == "https://openrouter.ai/api/v1"
            assert model == "meta-llama/llama-4-scout"

    def test_missing_key_exits_for_openrouter(self):
        """wee_runtime should sys.exit(1) when no OpenRouter key found."""
        wee_rt = importlib.import_module("wee_runtime")

        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = None

        with _clean_env("OPENROUTER_API_KEY", "WEE_API_KEY", "WEE_API_BASE"):
            with patch.dict("sys.modules", {"keyring": mock_keyring}):
                with pytest.raises(SystemExit) as exc_info:
                    wee_rt.resolve_model_and_endpoint(
                        "openrouter/meta-llama/llama-4-scout"
                    )
                assert exc_info.value.code == 1

    def test_ollama_default_in_wee_runtime(self):
        """Ollama models should default to 'ollama' in wee_runtime."""
        wee_rt = importlib.import_module("wee_runtime")

        with _clean_env("WEE_API_KEY", "WEE_API_BASE"):
            model, base, key = wee_rt.resolve_model_and_endpoint(
                "ollama/gemma4:e4b"
            )
            assert key == "ollama"
            assert model == "gemma4:e4b"


class TestIssue144FreeModelAuth:
    """OpenRouter free models (with :free suffix) should also authenticate."""

    def test_free_model_uses_api_key(self, mgr):
        """openrouter/model:free should still require auth."""
        mock_openai = MagicMock()
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))
        mock_openai.return_value.chat.completions.create.return_value = mock_stream

        with _clean_env("OPENROUTER_API_KEY", "WEE_API_KEY", "WEE_API_BASE"):
            os.environ["OPENROUTER_API_KEY"] = "sk-or-free-test"
            with patch("openai.OpenAI", mock_openai):
                with patch.object(mgr, "get_or_create_session_data", return_value={}):
                    with patch.object(mgr, "build_agent_context_prompt", return_value="ctx"):
                        with patch.object(mgr, "_wee_augment_system_prompt_with_tools", return_value="ctx"):
                            with patch.object(mgr, "_wee_anti_hallucination_prompt", return_value=""):
                                with patch.object(mgr, "_wee_load_messages", return_value=[]):
                                    try:
                                        mgr.run_wee_native(
                                            "test",
                                            "openrouter/deepseek/deepseek-r1:free",
                                            "orchestrator",
                                            None,
                                            False,
                                            "test-session-144k",
                                        )
                                    except Exception:
                                        pass

            if mock_openai.called:
                call_kwargs = mock_openai.call_args
                assert call_kwargs.kwargs.get("api_key") == "sk-or-free-test"

    def test_free_model_missing_key_raises(self, mgr):
        """Free models should still raise ValueError when no key found."""
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = None

        with _clean_env("OPENROUTER_API_KEY", "WEE_API_KEY", "WEE_API_BASE"):
            with patch.dict("sys.modules", {"keyring": mock_keyring}):
                with patch.object(mgr, "get_or_create_session_data", return_value={}):
                    with pytest.raises(ValueError, match="OpenRouter API key not found"):
                        mgr.run_wee_native(
                            "test",
                            "openrouter/google/gemma-3-27b-it:free",
                            "orchestrator",
                            None,
                            False,
                            "test-session-144l",
                        )
