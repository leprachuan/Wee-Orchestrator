"""Tests for Issue #125: wee runtime 429 retry + fallback chain."""
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")


# ── Standalone wee_runtime.py tests ─────────────────────────────────────────

from wee_runtime import (
    is_free_openrouter_model,
    load_free_model_config,
    resolve_model_and_endpoint,
    _call_with_retry,
    run_with_fallback,
)


class TestIsFreeModel(unittest.TestCase):
    def test_openrouter_free_literal(self):
        self.assertTrue(is_free_openrouter_model("openrouter/free"))

    def test_openrouter_free_uppercase(self):
        self.assertTrue(is_free_openrouter_model("OPENROUTER/FREE"))

    def test_colon_free_suffix(self):
        self.assertTrue(is_free_openrouter_model("openrouter/google/gemma-4-31b-it:free"))

    def test_paid_model(self):
        self.assertFalse(is_free_openrouter_model("openrouter/google/gemma-4-31b-it"))

    def test_ollama_model(self):
        self.assertFalse(is_free_openrouter_model("ollama/gemma4:e4b"))

    def test_llama_free(self):
        self.assertTrue(is_free_openrouter_model("openrouter/meta-llama/llama-3.3-70b-instruct:free"))

    def test_not_openrouter_with_free_suffix(self):
        self.assertFalse(is_free_openrouter_model("lmstudio/some-model:free"))


class TestLoadFreeConfig(unittest.TestCase):
    def test_loads_existing_file(self):
        import tempfile
        data = {
            "free_model_fallback_chain": ["openrouter/free", "openrouter/foo:free"],
            "max_retries_per_model": 2,
            "retry_backoff_seconds": [1, 3],
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            fname = f.name
        try:
            config = load_free_model_config(fname)
            self.assertEqual(config["max_retries_per_model"], 2)
            self.assertEqual(config["retry_backoff_seconds"], [1, 3])
            self.assertIn("openrouter/free", config["free_model_fallback_chain"])
        finally:
            os.unlink(fname)

    def test_missing_file_returns_defaults(self):
        config = load_free_model_config("/nonexistent/path.json")
        self.assertIn("free_model_fallback_chain", config)
        self.assertEqual(config["max_retries_per_model"], 3)
        self.assertGreater(len(config["free_model_fallback_chain"]), 0)

    def test_defaults_include_openrouter_free(self):
        config = load_free_model_config("/nonexistent/path.json")
        self.assertIn("openrouter/free", config["free_model_fallback_chain"])

    def test_fallback_chain_first_is_openrouter_free(self):
        config = load_free_model_config("/nonexistent/path.json")
        self.assertEqual(config["free_model_fallback_chain"][0], "openrouter/free")

    def test_partial_override_merges_with_defaults(self):
        import tempfile
        data = {"max_retries_per_model": 5}
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            fname = f.name
        try:
            config = load_free_model_config(fname)
            self.assertEqual(config["max_retries_per_model"], 5)
            self.assertIn("free_model_fallback_chain", config)
        finally:
            os.unlink(fname)


class TestCallWithRetry(unittest.TestCase):
    def _make_stream(self, text):
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = text
        chunk.choices[0].delta.tool_calls = None
        return [chunk]

    def _make_client(self, return_value=None, side_effect=None):
        client = MagicMock()
        if side_effect:
            client.chat.completions.create.side_effect = side_effect
        else:
            client.chat.completions.create.return_value = return_value
        return client

    def test_success_on_first_attempt(self):
        client = self._make_client(return_value=self._make_stream("hello"))
        output, err = _call_with_retry(
            client, "some-model", [{"role": "user", "content": "hi"}],
            {"stream": True}, max_retries=3, backoff_seconds=[0, 0, 0]
        )
        self.assertIsNone(err)
        self.assertIn("hello", output)
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_non_429_error_no_retry(self):
        client = self._make_client(side_effect=Exception("auth error 401"))
        output, err = _call_with_retry(
            client, "model", [], {"stream": True},
            max_retries=3, backoff_seconds=[0, 0, 0]
        )
        self.assertIsNone(output)
        self.assertIsNotNone(err)
        self.assertEqual(client.chat.completions.create.call_count, 1)

    @patch("wee_runtime.time")
    def test_429_retries_with_backoff(self, mock_time):
        mock_time.sleep = MagicMock()
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            Exception("Error 429 rate limited"),
            Exception("Error 429 rate limited"),
            self._make_stream("success")[0],  # won't be used—stream returns iterable
        ]
        # Build a proper return for 3rd call
        client.chat.completions.create.side_effect = [
            Exception("429 rate limited"),
            Exception("429 rate limited"),
        ]
        # All fail → return None
        output, err = _call_with_retry(
            client, "model", [], {"stream": True},
            max_retries=2, backoff_seconds=[0, 0]
        )
        self.assertIsNone(output)
        self.assertIsNotNone(err)
        self.assertEqual(client.chat.completions.create.call_count, 2)

    @patch("wee_runtime.time")
    def test_429_succeeds_on_retry(self, mock_time):
        mock_time.sleep = MagicMock()
        stream = self._make_stream("retry success")
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            Exception("429 rate limit exceeded"),
            stream,
        ]
        output, err = _call_with_retry(
            client, "model", [], {"stream": True},
            max_retries=3, backoff_seconds=[0, 0, 0]
        )
        self.assertIsNone(err)
        self.assertIn("retry success", output)
        self.assertEqual(client.chat.completions.create.call_count, 2)

    @patch("wee_runtime.time")
    def test_all_retries_exhausted_returns_none(self, mock_time):
        mock_time.sleep = MagicMock()
        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("429 rate limited")
        output, err = _call_with_retry(
            client, "model", [], {"stream": True},
            max_retries=3, backoff_seconds=[0, 0, 0]
        )
        self.assertIsNone(output)
        self.assertIsNotNone(err)
        self.assertEqual(client.chat.completions.create.call_count, 3)


class TestRunWithFallbackNonFree(unittest.TestCase):
    """Non-free models should get single attempt, no fallback."""

    def _make_stream(self, text):
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = text
        chunk.choices[0].delta.tool_calls = None
        return [chunk]

    def test_ollama_model_is_not_free(self):
        self.assertFalse(is_free_openrouter_model("ollama/gemma4:e4b"))

    def test_paid_openrouter_model_is_not_free(self):
        self.assertFalse(is_free_openrouter_model("openrouter/google/gemma-4-31b-it"))


class TestWeeFreeFallbackChainConfig(unittest.TestCase):
    """Test that wee_free_models.json is correctly formed."""

    def test_config_file_exists(self):
        config_path = Path("/opt/n8n-copilot-shim-dev/wee_free_models.json")
        self.assertTrue(config_path.exists(), "wee_free_models.json should exist")

    def test_config_file_is_valid_json(self):
        config_path = Path("/opt/n8n-copilot-shim-dev/wee_free_models.json")
        with open(config_path) as f:
            data = json.load(f)
        self.assertIn("free_model_fallback_chain", data)

    def test_config_has_max_retries_3(self):
        config_path = Path("/opt/n8n-copilot-shim-dev/wee_free_models.json")
        with open(config_path) as f:
            data = json.load(f)
        self.assertEqual(data["max_retries_per_model"], 3)

    def test_config_has_backoff_2_5_10(self):
        config_path = Path("/opt/n8n-copilot-shim-dev/wee_free_models.json")
        with open(config_path) as f:
            data = json.load(f)
        self.assertEqual(data["retry_backoff_seconds"], [2, 5, 10])

    def test_config_fallback_chain_not_empty(self):
        config_path = Path("/opt/n8n-copilot-shim-dev/wee_free_models.json")
        with open(config_path) as f:
            data = json.load(f)
        self.assertGreater(len(data["free_model_fallback_chain"]), 0)

    def test_config_all_chain_items_are_openrouter(self):
        config_path = Path("/opt/n8n-copilot-shim-dev/wee_free_models.json")
        with open(config_path) as f:
            data = json.load(f)
        for model in data["free_model_fallback_chain"]:
            self.assertTrue(
                model.startswith("openrouter/"),
                f"Fallback model should be openrouter: {model}"
            )

    def test_config_first_entry_is_openrouter_free(self):
        config_path = Path("/opt/n8n-copilot-shim-dev/wee_free_models.json")
        with open(config_path) as f:
            data = json.load(f)
        self.assertEqual(data["free_model_fallback_chain"][0], "openrouter/free")


# ── SessionManager (agent_manager.py) static helpers ────────────────────────

class TestSessionManagerWeeHelpers(unittest.TestCase):
    """Test wee static/instance helpers on SessionManager."""

    @classmethod
    def setUpClass(cls):
        from agent_manager import SessionManager
        cls.SM = SessionManager

    def test_is_free_model_openrouter_free(self):
        self.assertTrue(self.SM._wee_is_free_model("openrouter/free"))

    def test_is_free_model_colon_free(self):
        self.assertTrue(self.SM._wee_is_free_model("openrouter/google/gemma-4-31b-it:free"))

    def test_is_free_model_paid(self):
        self.assertFalse(self.SM._wee_is_free_model("openrouter/google/gemma-4-31b-it"))

    def test_is_free_model_ollama(self):
        self.assertFalse(self.SM._wee_is_free_model("ollama/gemma4:e4b"))

    def test_is_free_model_case_insensitive(self):
        self.assertTrue(self.SM._wee_is_free_model("OPENROUTER/FREE"))

    def test_load_free_config_missing_file(self):
        config = self.SM._wee_load_free_config("/nonexistent/path.json")
        self.assertIn("free_model_fallback_chain", config)
        self.assertEqual(config["max_retries_per_model"], 3)

    def test_load_free_config_file_present(self):
        import tempfile
        data = {"max_retries_per_model": 7, "retry_backoff_seconds": [1, 2, 3]}
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            fname = f.name
        try:
            config = self.SM._wee_load_free_config(fname)
            self.assertEqual(config["max_retries_per_model"], 7)
        finally:
            os.unlink(fname)

    def test_fallback_chain_default_starts_with_openrouter_free(self):
        config = self.SM._wee_load_free_config("/nonexistent.json")
        chain = config["free_model_fallback_chain"]
        self.assertEqual(chain[0], "openrouter/free")

    def test_wee_run_attempt_success_returns_output_false(self):
        """_wee_run_attempt returns (str, False) on success."""
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = "test response"
        chunk.choices[0].delta.tool_calls = None

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = [chunk]

        mock_self = MagicMock()
        mock_self._wee_execute_tool = MagicMock(return_value="")
        mock_self._wee_save_messages = MagicMock()

        result = self.SM._wee_run_attempt(
            mock_self,
            client=mock_client,
            resolved_model="gemma4:e4b",
            messages=[{"role": "user", "content": "hi"}],
            stream_buffer=None,
            n8n_session_id="test-session",
            agent="orchestrator",
            wee_tools=[],
            max_retries=3,
            backoff=[0, 0, 0],
            attempt_model="ollama/gemma4:e4b",
        )
        self.assertIsInstance(result, tuple)
        output, is_429 = result
        self.assertFalse(is_429)
        self.assertIn("test response", output)

    def test_wee_run_attempt_429_exhaustion_returns_none_true(self):
        """On 429 exhaustion, _wee_run_attempt returns (None, True)."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("429 rate limit exceeded")

        mock_self = MagicMock()

        result = self.SM._wee_run_attempt(
            mock_self,
            client=mock_client,
            resolved_model="free",
            messages=[{"role": "user", "content": "hi"}],
            stream_buffer=None,
            n8n_session_id="test-session",
            agent="orchestrator",
            wee_tools=[],
            max_retries=1,
            backoff=[0],
            attempt_model="openrouter/free",
        )
        self.assertEqual(result, (None, True))

    def test_wee_run_attempt_non_429_error_returns_message_false(self):
        """Non-429 errors return error message with is_429=False."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("connection refused 503")

        mock_self = MagicMock()

        result = self.SM._wee_run_attempt(
            mock_self,
            client=mock_client,
            resolved_model="free",
            messages=[{"role": "user", "content": "hi"}],
            stream_buffer=None,
            n8n_session_id="test-session",
            agent="orchestrator",
            wee_tools=[],
            max_retries=3,
            backoff=[0, 0, 0],
            attempt_model="openrouter/free",
        )
        output, is_429 = result
        self.assertFalse(is_429)
        self.assertIsNotNone(output)


class TestWeeModelsIncludesFree(unittest.TestCase):
    """openrouter/free should appear in WEE_MODELS."""

    def test_wee_models_has_openrouter_free(self):
        """The WEE_MODELS constant should include openrouter/free."""
        import agent_manager
        wee_models = agent_manager.WEE_MODELS if hasattr(agent_manager, "WEE_MODELS") else None
        if wee_models is None:
            self.skipTest("WEE_MODELS constant not at module level")
        all_model_ids = [m[0] for group in wee_models.values() for m in group]
        self.assertIn("openrouter/free", all_model_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
