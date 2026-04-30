"""Regression tests for Issue #153: OpenRouter 401 auth error.

Tests verify:
- OPENROUTER_API_KEY env var is used before keyring
- keyring fallback works when env var not set
- ValueError raised (not silent 'ollama' fallback) when key missing in agent_manager
- sys.exit(1) raised (not silent 'ollama' fallback) in wee_runtime.py
- Ollama still defaults correctly
- Explicit api_key argument takes priority
"""

import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

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
        "orchestrator": {
            "path": "/opt",
            "description": "test",
            "name": "orchestrator",
        }
    }
    mgr._stream_buffers = {}
    return mgr


def _run_wee_native_capturing_key(
    mgr, model, session_id="test-153", env_override=None, **session_overrides
):
    """Run run_wee_native, capturing the api_key passed to OpenAI client."""
    session_data = {
        "runtime": "wee",
        "model": model,
        "channel": "api",
        **session_overrides,
    }
    mgr.session_map[session_id] = session_data
    captured = {}

    def fake_openai(base_url, api_key, **kwargs):
        captured["api_key"] = api_key
        captured["base_url"] = base_url
        raise RuntimeError("stop_early")

    env = {k: v for k, v in os.environ.items()}
    if env_override:
        for k, v in env_override.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v

    with (
        patch.object(mgr, "get_or_create_session_data", return_value=session_data),
        patch.object(
            mgr, "build_agent_context_prompt", return_value="You are helpful."
        ),
        patch("openai.OpenAI", side_effect=fake_openai),
        patch.dict(os.environ, env, clear=True),
    ):
        try:
            mgr.run_wee_native(
                prompt="hello",
                model=model,
                agent="orchestrator",
                session_id=None,
                resume=False,
                n8n_session_id=session_id,
                timeout=30,
                render_type="text",
            )
        except RuntimeError as e:
            if "stop_early" not in str(e):
                raise
    return captured


# ---------------------------------------------------------------------------
# agent_manager.py tests
# ---------------------------------------------------------------------------


class TestAgentManagerOpenRouterEnvVar(unittest.TestCase):
    """Issue #153: OPENROUTER_API_KEY env var is used when set."""

    def test_env_var_used_as_api_key(self):
        mgr = _make_mgr()
        with patch("keyring.get_password", return_value=None):
            captured = _run_wee_native_capturing_key(
                mgr,
                "openrouter/meta-llama/llama-4-scout",
                session_id="test-153-env",
                env_override={
                    "OPENROUTER_API_KEY": "sk-or-env-test-key",
                    "WEE_API_KEY": None,
                },
            )
        self.assertEqual(captured.get("api_key"), "sk-or-env-test-key")
        self.assertIn("openrouter.ai", captured.get("base_url", ""))

    def test_env_var_priority_over_keyring(self):
        mgr = _make_mgr()
        with patch("keyring.get_password", return_value="sk-or-from-keyring"):
            captured = _run_wee_native_capturing_key(
                mgr,
                "openrouter/deepseek/deepseek-r1:free",
                session_id="test-153-env-priority",
                env_override={
                    "OPENROUTER_API_KEY": "sk-or-env-wins",
                    "WEE_API_KEY": None,
                },
            )
        self.assertEqual(captured.get("api_key"), "sk-or-env-wins")


class TestAgentManagerOpenRouterKeyring(unittest.TestCase):
    """Issue #153: keyring fallback used when env var absent."""

    def test_keyring_fallback_used(self):
        mgr = _make_mgr()
        with patch("keyring.get_password", return_value="sk-or-keyring-key"):
            captured = _run_wee_native_capturing_key(
                mgr,
                "openrouter/google/gemma-3-27b-it:free",
                session_id="test-153-keyring",
                env_override={"OPENROUTER_API_KEY": None, "WEE_API_KEY": None},
            )
        self.assertEqual(captured.get("api_key"), "sk-or-keyring-key")


class TestAgentManagerOpenRouterMissingKeyRaises(unittest.TestCase):
    """Issue #153: ValueError raised instead of silent 'ollama' fallback."""

    def test_missing_key_raises_value_error(self):
        mgr = _make_mgr()
        session_data = {
            "runtime": "wee",
            "model": "openrouter/qwen/qwen3-32b:free",
            "channel": "api",
        }
        mgr.session_map["test-153-missing"] = session_data
        env = {k: v for k, v in os.environ.items()}
        env.pop("OPENROUTER_API_KEY", None)
        env.pop("WEE_API_KEY", None)

        with (
            patch.object(mgr, "get_or_create_session_data", return_value=session_data),
            patch.object(mgr, "build_agent_context_prompt", return_value="test"),
            patch.dict(os.environ, env, clear=True),
            patch("keyring.get_password", return_value=None),
        ):
            with self.assertRaises(ValueError) as ctx:
                mgr.run_wee_native(
                    prompt="hello",
                    model="openrouter/qwen/qwen3-32b:free",
                    agent="orchestrator",
                    session_id=None,
                    resume=False,
                    n8n_session_id="test-153-missing",
                    timeout=30,
                    render_type="text",
                )
        self.assertIn("OpenRouter", str(ctx.exception))
        self.assertIn("OPENROUTER_API_KEY", str(ctx.exception))

    def test_missing_key_does_not_use_ollama(self):
        """Regression: 'ollama' must NOT be used as the OpenRouter API key."""
        mgr = _make_mgr()
        session_data = {
            "runtime": "wee",
            "model": "openrouter/meta-llama/llama-4-scout",
            "channel": "api",
        }
        mgr.session_map["test-153-no-ollama"] = session_data
        env = {k: v for k, v in os.environ.items()}
        env.pop("OPENROUTER_API_KEY", None)
        env.pop("WEE_API_KEY", None)

        captured = {}

        def fake_openai(base_url, api_key, **kwargs):
            captured["api_key"] = api_key
            raise RuntimeError("stop_early")

        with (
            patch.object(mgr, "get_or_create_session_data", return_value=session_data),
            patch.object(mgr, "build_agent_context_prompt", return_value="test"),
            patch("openai.OpenAI", side_effect=fake_openai),
            patch.dict(os.environ, env, clear=True),
            patch("keyring.get_password", return_value=None),
        ):
            try:
                mgr.run_wee_native(
                    prompt="hello",
                    model="openrouter/meta-llama/llama-4-scout",
                    agent="orchestrator",
                    session_id=None,
                    resume=False,
                    n8n_session_id="test-153-no-ollama",
                    timeout=30,
                    render_type="text",
                )
                self.fail("Expected ValueError to be raised")
            except ValueError:
                pass  # correct
            except RuntimeError:
                # If we hit fake_openai, api_key must NOT be "ollama"
                self.assertNotEqual(
                    captured.get("api_key"),
                    "ollama",
                    "OpenRouter must not fall back to 'ollama' API key",
                )


class TestAgentManagerOllamaStillDefaults(unittest.TestCase):
    """Issue #153: Ollama still defaults to 'ollama' key."""

    def test_ollama_uses_default_key(self):
        mgr = _make_mgr()
        env = {k: v for k, v in os.environ.items()}
        env.pop("WEE_API_KEY", None)
        captured = _run_wee_native_capturing_key(
            mgr,
            "ollama/gemma4:e4b",
            session_id="test-153-ollama",
            env_override={"WEE_API_KEY": None},
        )
        self.assertEqual(captured.get("api_key"), "ollama")
        self.assertIn("11434", captured.get("base_url", ""))


class TestAgentManagerExplicitApiKeyPriority(unittest.TestCase):
    """Issue #153: Explicit api_key in session data takes priority."""

    def test_explicit_key_overrides_env(self):
        mgr = _make_mgr()
        with patch("keyring.get_password", return_value=None):
            captured = _run_wee_native_capturing_key(
                mgr,
                "openrouter/meta-llama/llama-4-scout",
                session_id="test-153-explicit",
                env_override={"OPENROUTER_API_KEY": "sk-or-env"},
                api_key="sk-or-explicit",
            )
        self.assertEqual(captured.get("api_key"), "sk-or-explicit")


# ---------------------------------------------------------------------------
# wee_runtime.py tests
# ---------------------------------------------------------------------------

import importlib.util  # noqa: E402

_wrt_spec = importlib.util.spec_from_file_location(
    "wee_runtime_module",
    str(REPO / "wee_runtime.py"),
)
_wrt_mod = importlib.util.module_from_spec(_wrt_spec)
_wrt_spec.loader.exec_module(_wrt_mod)
resolve_model_and_endpoint = _wrt_mod.resolve_model_and_endpoint


class TestWeeRuntimeOpenRouterEnvVar(unittest.TestCase):
    """Issue #153: wee_runtime uses OPENROUTER_API_KEY env var."""

    def test_env_var_used(self):
        env = {k: v for k, v in os.environ.items()}
        env["OPENROUTER_API_KEY"] = "sk-or-wee-env"
        env.pop("WEE_API_KEY", None)

        with (
            patch.dict(os.environ, env, clear=True),
            patch("keyring.get_password", return_value=None),
        ):
            _, _, key = resolve_model_and_endpoint(
                "openrouter/meta-llama/llama-4-scout"
            )

        self.assertEqual(key, "sk-or-wee-env")

    def test_env_var_priority_over_keyring(self):
        env = {k: v for k, v in os.environ.items()}
        env["OPENROUTER_API_KEY"] = "sk-or-env-priority"
        env.pop("WEE_API_KEY", None)

        with (
            patch.dict(os.environ, env, clear=True),
            patch("keyring.get_password", return_value="sk-or-keyring"),
        ):
            _, _, key = resolve_model_and_endpoint(
                "openrouter/deepseek/deepseek-r1:free"
            )

        self.assertEqual(key, "sk-or-env-priority")


class TestWeeRuntimeOpenRouterKeyring(unittest.TestCase):
    """Issue #153: wee_runtime keyring fallback for OpenRouter."""

    def test_keyring_fallback(self):
        env = {k: v for k, v in os.environ.items()}
        env.pop("OPENROUTER_API_KEY", None)
        env.pop("WEE_API_KEY", None)

        with (
            patch.dict(os.environ, env, clear=True),
            patch("keyring.get_password", return_value="sk-or-from-keyring"),
        ):
            _, _, key = resolve_model_and_endpoint("openrouter/qwen/qwen3-32b:free")

        self.assertEqual(key, "sk-or-from-keyring")


class TestWeeRuntimeOpenRouterMissingKeyExits(unittest.TestCase):
    """Issue #153: wee_runtime exits (not 'ollama') when key missing."""

    def test_missing_key_calls_sys_exit(self):
        env = {k: v for k, v in os.environ.items()}
        env.pop("OPENROUTER_API_KEY", None)
        env.pop("WEE_API_KEY", None)

        with (
            patch.dict(os.environ, env, clear=True),
            patch("keyring.get_password", return_value=None),
        ):
            with self.assertRaises(SystemExit) as ctx:
                resolve_model_and_endpoint("openrouter/meta-llama/llama-4-scout")
        self.assertEqual(ctx.exception.code, 1)

    def test_missing_key_not_ollama(self):
        """Regression: 'ollama' must NOT be returned for OpenRouter."""
        env = {k: v for k, v in os.environ.items()}
        env.pop("OPENROUTER_API_KEY", None)
        env.pop("WEE_API_KEY", None)

        with (
            patch.dict(os.environ, env, clear=True),
            patch("keyring.get_password", return_value=None),
        ):
            try:
                _, _, key = resolve_model_and_endpoint(
                    "openrouter/google/gemma-3-27b-it:free"
                )
                self.assertNotEqual(
                    key, "ollama", "Must not fall back to 'ollama' for OpenRouter"
                )
            except SystemExit:
                pass  # correct behavior


class TestWeeRuntimeOllamaDefaultKey(unittest.TestCase):
    """Issue #153: Ollama still defaults correctly after OpenRouter fix."""

    def test_ollama_default_key_unchanged(self):
        env = {k: v for k, v in os.environ.items()}
        env.pop("WEE_API_KEY", None)

        with patch.dict(os.environ, env, clear=True):
            model, base, key = resolve_model_and_endpoint("ollama/gemma4:e4b")

        self.assertEqual(key, "ollama")
        self.assertIn("11434", base)
        self.assertEqual(model, "gemma4:e4b")

    def test_ollama_model_resolution(self):
        model, base, key = resolve_model_and_endpoint(
            "ollama/qwen3:8b", api_base=None, api_key=None
        )
        self.assertEqual(model, "qwen3:8b")
        self.assertIn("ollama", key.lower())


class TestWeeRuntimeOpenRouterModelResolution(unittest.TestCase):
    """Issue #153: OpenRouter model prefix is stripped correctly."""

    def test_model_prefix_stripped(self):
        env = {k: v for k, v in os.environ.items()}
        env["OPENROUTER_API_KEY"] = "sk-or-test"

        with patch.dict(os.environ, env, clear=True):
            model, base, key = resolve_model_and_endpoint(
                "openrouter/meta-llama/llama-4-scout"
            )

        self.assertEqual(model, "meta-llama/llama-4-scout")
        self.assertIn("openrouter.ai", base)
        self.assertEqual(key, "sk-or-test")

    def test_free_model_prefix_stripped(self):
        env = {k: v for k, v in os.environ.items()}
        env["OPENROUTER_API_KEY"] = "sk-or-test"

        with patch.dict(os.environ, env, clear=True):
            model, base, key = resolve_model_and_endpoint(
                "openrouter/google/gemma-3-27b-it:free"
            )

        self.assertEqual(model, "google/gemma-3-27b-it:free")


if __name__ == "__main__":
    unittest.main(verbosity=2)
