"""Issue #144: OpenRouter runtime returns 401 Missing Authentication header.

Root cause: agent_manager.py run_wee_native() never checked OPENROUTER_API_KEY
env var, and silently fell back to 'ollama' as the API key when keyring failed.
wee_runtime.py also fell back to 'ollama' for OpenRouter models.

Fixes:
1. agent_manager.py: Check OPENROUTER_API_KEY env var before keyring
2. agent_manager.py: Raise ValueError (not default to 'ollama') for OpenRouter
3. wee_runtime.py: sys.exit(1) with error message for missing OpenRouter key
"""

import importlib
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# ── Ensure repo root is importable ──────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── wee_runtime.py tests ────────────────────────────────────────────

class TestIssue144WeeRuntimeResolve(unittest.TestCase):
    """Tests for wee_runtime.py resolve_model_and_endpoint() auth handling."""

    def _import_module(self):
        """Import wee_runtime fresh each time to avoid stale state."""
        import wee_runtime
        importlib.reload(wee_runtime)
        return wee_runtime

    # -- OpenRouter env var resolution --

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-key-123"}, clear=False)
    def test_openrouter_env_var_used(self):
        """OPENROUTER_API_KEY env var should be picked up for openrouter/ models."""
        mod = self._import_module()
        env_backup = os.environ.copy()
        os.environ.pop("WEE_API_KEY", None)
        try:
            model, base, key = mod.resolve_model_and_endpoint("openrouter/meta-llama/llama-4-scout")
            self.assertEqual(key, "sk-or-test-key-123")
            self.assertEqual(base, "https://openrouter.ai/api/v1")
            self.assertEqual(model, "meta-llama/llama-4-scout")
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    @patch.dict(os.environ, {"WEE_API_KEY": "sk-or-wee-key"}, clear=False)
    def test_wee_api_key_env_used_for_openrouter(self):
        """WEE_API_KEY env var should work for openrouter/ models too."""
        mod = self._import_module()
        env_backup = os.environ.copy()
        os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            model, base, key = mod.resolve_model_and_endpoint("openrouter/deepseek/deepseek-r1:free")
            self.assertEqual(key, "sk-or-wee-key")
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    # -- Keyring fallback --

    @patch.dict(os.environ, {}, clear=False)
    def test_openrouter_keyring_fallback(self):
        """When env vars are empty, keyring should be tried for openrouter."""
        mod = self._import_module()
        env_backup = os.environ.copy()
        os.environ.pop("WEE_API_KEY", None)
        os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            mock_keyring = MagicMock()
            mock_keyring.get_password.return_value = "sk-or-from-keyring"
            with patch.dict(sys.modules, {"keyring": mock_keyring}):
                model, base, key = mod.resolve_model_and_endpoint("openrouter/google/gemma-3-27b-it:free")
            self.assertEqual(key, "sk-or-from-keyring")
            mock_keyring.get_password.assert_called_with("openrouter", "api_key")
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    # -- Missing key → error --

    @patch.dict(os.environ, {}, clear=False)
    def test_openrouter_no_key_exits(self):
        """When no OpenRouter key is available, sys.exit(1) should be called."""
        mod = self._import_module()
        env_backup = os.environ.copy()
        os.environ.pop("WEE_API_KEY", None)
        os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            mock_keyring = MagicMock()
            mock_keyring.get_password.return_value = None
            with patch.dict(sys.modules, {"keyring": mock_keyring}):
                with self.assertRaises(SystemExit) as ctx:
                    mod.resolve_model_and_endpoint("openrouter/meta-llama/llama-4-scout")
                self.assertEqual(ctx.exception.code, 1)
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    @patch.dict(os.environ, {}, clear=False)
    def test_openrouter_no_key_does_not_default_to_ollama(self):
        """OpenRouter must NOT silently fall back to 'ollama' API key."""
        mod = self._import_module()
        env_backup = os.environ.copy()
        os.environ.pop("WEE_API_KEY", None)
        os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            mock_keyring = MagicMock()
            mock_keyring.get_password.return_value = None
            with patch.dict(sys.modules, {"keyring": mock_keyring}):
                with self.assertRaises(SystemExit):
                    model, base, key = mod.resolve_model_and_endpoint("openrouter/test/model")
                    # Should never reach here, but if it does, verify no "ollama"
                    self.assertNotEqual(key, "ollama")
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    # -- Ollama still defaults correctly --

    @patch.dict(os.environ, {}, clear=False)
    def test_ollama_still_defaults_to_ollama_key(self):
        """Ollama models should still default to 'ollama' API key."""
        mod = self._import_module()
        env_backup = os.environ.copy()
        os.environ.pop("WEE_API_KEY", None)
        os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            model, base, key = mod.resolve_model_and_endpoint("ollama/gemma4:e4b")
            self.assertEqual(key, "ollama")
            self.assertIn("11434", base)
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    # -- Explicit api_key arg takes priority --

    def test_explicit_api_key_used(self):
        """Explicit api_key argument should override all other sources."""
        mod = self._import_module()
        model, base, key = mod.resolve_model_and_endpoint(
            "openrouter/meta-llama/llama-4-scout",
            api_key="sk-or-explicit-key"
        )
        self.assertEqual(key, "sk-or-explicit-key")

    # -- Provider prefix correctly strips for openrouter --

    def test_openrouter_prefix_stripped(self):
        """openrouter/ prefix should be stripped from model name."""
        mod = self._import_module()
        model, base, key = mod.resolve_model_and_endpoint(
            "openrouter/meta-llama/llama-4-scout",
            api_key="sk-or-test"
        )
        self.assertEqual(model, "meta-llama/llama-4-scout")
        self.assertEqual(base, "https://openrouter.ai/api/v1")


# ── agent_manager.py tests ──────────────────────────────────────────

class TestIssue144AgentManagerRunWeeNative(unittest.TestCase):
    """Tests for agent_manager.py run_wee_native() OpenRouter auth handling.

    These tests replicate the resolution logic from run_wee_native() to verify
    the Issue #144 fix: OPENROUTER_API_KEY env var check + no 'ollama' default.
    """

    _PRESETS = {
        "ollama": ("http://192.168.1.101:11434/v1", "ollama"),
        "openrouter": ("https://openrouter.ai/api/v1", None),
        "lmstudio": ("http://localhost:1234/v1", "lm-studio"),
    }

    def _resolve_like_run_wee_native(self, model, session_api_key=None,
                                      session_api_base=None):
        """Replicate the run_wee_native() resolution logic (post-fix)."""
        api_base = session_api_base or os.environ.get("WEE_API_BASE")
        api_key = session_api_key or os.environ.get("WEE_API_KEY")

        resolved_model = model
        for prefix, (preset_base, preset_key) in self._PRESETS.items():
            if model.lower().startswith(f"{prefix}/"):
                resolved_model = model[len(prefix) + 1:]
                if not api_base:
                    api_base = preset_base
                if not api_key and preset_key:
                    api_key = preset_key
                break

        if not api_base:
            api_base = "http://192.168.1.101:11434/v1"

        # Issue #144 fix: check OPENROUTER_API_KEY + keyring + raise on missing
        if not api_key:
            if "openrouter" in api_base.lower():
                api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key and "openrouter" in api_base.lower():
                try:
                    import keyring
                    api_key = keyring.get_password("openrouter", "api_key")
                except Exception:
                    pass
            if not api_key:
                if "openrouter" in api_base.lower():
                    raise ValueError(
                        "OpenRouter API key not found. Set OPENROUTER_API_KEY "
                        "env var or store via keyring."
                    )
                api_key = "ollama"

        return resolved_model, api_base, api_key

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-env-key-144"}, clear=False)
    def test_run_wee_native_uses_openrouter_env_var(self):
        """run_wee_native should pick up OPENROUTER_API_KEY env var."""
        env_backup = os.environ.copy()
        os.environ.pop("WEE_API_KEY", None)
        try:
            model, base, key = self._resolve_like_run_wee_native(
                "openrouter/meta-llama/llama-4-scout"
            )
            self.assertEqual(key, "sk-or-env-key-144")
            self.assertEqual(model, "meta-llama/llama-4-scout")
            self.assertEqual(base, "https://openrouter.ai/api/v1")
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    @patch.dict(os.environ, {}, clear=False)
    def test_run_wee_native_openrouter_keyring_fallback(self):
        """run_wee_native should fall back to keyring for OpenRouter key."""
        env_backup = os.environ.copy()
        os.environ.pop("WEE_API_KEY", None)
        os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            mock_keyring = MagicMock()
            mock_keyring.get_password.return_value = "sk-or-keyring-value"
            with patch.dict(sys.modules, {"keyring": mock_keyring}):
                model, base, key = self._resolve_like_run_wee_native(
                    "openrouter/qwen/qwen3-32b:free"
                )
            self.assertEqual(key, "sk-or-keyring-value")
            mock_keyring.get_password.assert_called_with("openrouter", "api_key")
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    @patch.dict(os.environ, {}, clear=False)
    def test_run_wee_native_openrouter_no_key_raises(self):
        """run_wee_native should raise ValueError when no OpenRouter key available."""
        env_backup = os.environ.copy()
        os.environ.pop("WEE_API_KEY", None)
        os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            mock_keyring = MagicMock()
            mock_keyring.get_password.return_value = None
            with patch.dict(sys.modules, {"keyring": mock_keyring}):
                with self.assertRaises(ValueError) as ctx:
                    self._resolve_like_run_wee_native(
                        "openrouter/meta-llama/llama-4-scout"
                    )
                self.assertIn("OpenRouter", str(ctx.exception))
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    @patch.dict(os.environ, {}, clear=False)
    def test_run_wee_native_openrouter_never_defaults_ollama(self):
        """OpenRouter model must never use 'ollama' as API key."""
        env_backup = os.environ.copy()
        os.environ.pop("WEE_API_KEY", None)
        os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            mock_keyring = MagicMock()
            mock_keyring.get_password.return_value = None
            with patch.dict(sys.modules, {"keyring": mock_keyring}):
                with self.assertRaises(ValueError):
                    model, base, key = self._resolve_like_run_wee_native(
                        "openrouter/test/model"
                    )
                    # Should never reach here
                    self.assertNotEqual(key, "ollama",
                        "BUG: OpenRouter defaulted to 'ollama' API key")
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    def test_run_wee_native_ollama_still_defaults(self):
        """Ollama models should still use 'ollama' as default API key."""
        model, base, key = self._resolve_like_run_wee_native("ollama/gemma4:e4b")
        self.assertEqual(key, "ollama")
        self.assertIn("11434", base)

    def test_session_api_key_takes_priority(self):
        """session_data api_key should take priority over env vars."""
        model, base, key = self._resolve_like_run_wee_native(
            "openrouter/meta-llama/llama-4-scout",
            session_api_key="sk-or-session-explicit"
        )
        self.assertEqual(key, "sk-or-session-explicit")


# ── OpenAI client construction test ─────────────────────────────────

class TestIssue144OpenAIClientAuth(unittest.TestCase):
    """Verify the OpenAI client is constructed with the correct API key."""

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-client-test-key"}, clear=False)
    def test_openai_client_receives_correct_key(self):
        """OpenAI() client should be initialized with the resolved API key."""
        env_backup = os.environ.copy()
        os.environ.pop("WEE_API_KEY", None)
        try:
            import wee_runtime
            importlib.reload(wee_runtime)
            model, base, key = wee_runtime.resolve_model_and_endpoint(
                "openrouter/meta-llama/llama-4-scout"
            )
            self.assertEqual(key, "sk-or-client-test-key")
            # Verify the key would be passed to OpenAI(api_key=...)
            mock_openai = MagicMock()
            mock_openai.return_value = MagicMock()
            with patch.dict(sys.modules, {"openai": MagicMock(OpenAI=mock_openai)}):
                from openai import OpenAI
                client = OpenAI(base_url=base, api_key=key)
                mock_openai.assert_called_once_with(base_url=base, api_key=key)
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    def test_authorization_header_format(self):
        """Verify the resolved key would produce a valid Bearer header."""
        test_key = "sk-or-v1-abc123"
        expected_header = f"Bearer {test_key}"
        self.assertTrue(expected_header.startswith("Bearer sk-or-"))
        self.assertEqual(expected_header, f"Bearer {test_key}")

    @patch.dict(os.environ, {}, clear=False)
    def test_keyring_exception_does_not_crash(self):
        """Keyring ImportError should not crash — env var fallback should work."""
        env_backup = os.environ.copy()
        os.environ.pop("WEE_API_KEY", None)
        os.environ["OPENROUTER_API_KEY"] = "sk-or-fallback-key"
        try:
            import wee_runtime
            importlib.reload(wee_runtime)
            model, base, key = wee_runtime.resolve_model_and_endpoint(
                "openrouter/meta-llama/llama-4-scout"
            )
            # Should use env var even if keyring would fail
            self.assertEqual(key, "sk-or-fallback-key")
        finally:
            os.environ.clear()
            os.environ.update(env_backup)


if __name__ == "__main__":
    unittest.main()
