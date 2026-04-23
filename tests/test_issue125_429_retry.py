"""
Regression tests for Issue #125 — 429 retry + free model fallback chain.
Tests: B01 (no recursion), B02 (user visibility), M01 (sync sleep), M03 (coverage).
"""
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ.setdefault("API_SHARED_KEY", "test_key_123")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_manager import SessionManager


def _make_mgr():
    """Create a minimal SessionManager for Issue #125 testing."""
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_map = {}
    mgr._session_map_lock = threading.Lock()
    mgr.command_timeout = 60
    mgr._stream_buffers = {}
    mgr.AGENTS = {"test": {"path": "/opt", "description": "test"}}
    return mgr


def _make_chunk(text):
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = text
    chunk.usage = None
    return chunk


def _make_done_chunk():
    chunk = MagicMock()
    chunk.choices = []
    chunk.usage = MagicMock()
    chunk.usage.prompt_tokens = 10
    chunk.usage.completion_tokens = 20
    chunk.usage.total_tokens = 30
    return chunk


class TestIssue125RetryFallbackChain(unittest.TestCase):
    """Main regression tests for B01/B02/M01/M03."""

    def setUp(self):
        self.mgr = _make_mgr()
        self.mgr.get_or_create_session_data = MagicMock(return_value={
            "channel": "test", "api_base": None, "api_key": None
        })
        self.mgr.build_agent_context_prompt = MagicMock(return_value="")
        self.mgr.update_session_field = MagicMock()
        self.mgr._fetch_openrouter_pricing = MagicMock(return_value={})
        self.mgr._calculate_wee_cost = MagicMock(return_value=(0.0, "free"))
        self.mgr._log_token_usage = MagicMock()

    @patch("agent_manager.time.sleep")
    @patch("openai.OpenAI")
    def test_issue_125_retry_fallback_chain(self, mock_openai_cls, mock_sleep):
        """Main regression: 429 retries exhaust on primary, then falls back to next model."""
        free_cfg = {
            "max_retries_per_model": 2,
            "retry_backoff_seconds": [1, 2],
            "free_model_fallback_chain": [
                "openrouter/primary:free",
                "openrouter/fallback:free",
            ],
        }
        self.mgr._wee_load_free_config = MagicMock(return_value=free_cfg)

        call_tracker = []
        err_429 = Exception("429 rate limit exceeded")

        def stream_side_effect(*a, **kw):
            model_arg = kw.get("model", "unknown")
            call_tracker.append(model_arg)
            if "primary" in model_arg:
                raise err_429
            return iter([_make_chunk("fallback works"), _make_done_chunk()])

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = stream_side_effect
        mock_openai_cls.return_value = mock_instance

        with patch("sys.stderr"):
            result = self.mgr.run_wee_native(
                prompt="test prompt",
                model="openrouter/primary:free",
                agent="test",
                session_id="s1",
                resume=False,
                n8n_session_id="n2",
            )

        self.assertIn("fallback works", result)
        # primary was tried max_retries times before switching
        primary_calls = [c for c in call_tracker if "primary" in c]
        self.assertEqual(len(primary_calls), 2, "Should retry primary 2x before fallback")

    @patch("agent_manager.time.sleep")
    @patch("openai.OpenAI")
    def test_issue_125_all_fallbacks_exhausted_no_crash(self, mock_openai_cls, mock_sleep):
        """B01: When all fallbacks are 429, return error message (no stack overflow)."""
        free_cfg = {
            "max_retries_per_model": 1,
            "retry_backoff_seconds": [1],
            "free_model_fallback_chain": ["openrouter/b:free"],
        }
        self.mgr._wee_load_free_config = MagicMock(return_value=free_cfg)

        err_429 = Exception("429 rate limit")
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = err_429
        mock_openai_cls.return_value = mock_instance

        with patch("sys.stderr"):
            result = self.mgr.run_wee_native(
                prompt="test",
                model="openrouter/primary:free",
                agent="test",
                session_id="s1",
                resume=False,
                n8n_session_id="n3",
            )

        self.assertIn("exhausted", result.lower())

    @patch("agent_manager.time.sleep")
    @patch("openai.OpenAI")
    def test_issue_125_fallback_notification_in_stream(self, mock_openai_cls, mock_sleep):
        """B02: stream buffer should receive fallback notification message."""
        free_cfg = {
            "max_retries_per_model": 1,
            "retry_backoff_seconds": [1],
            "free_model_fallback_chain": [
                "openrouter/primary:free",
                "openrouter/backup:free",
            ],
        }
        self.mgr._wee_load_free_config = MagicMock(return_value=free_cfg)

        stream_buf = MagicMock()
        self.mgr._stream_buffers = {"n4": stream_buf}

        err_429 = Exception("429 rate limit")
        attempt = [0]

        def stream_side_effect(*a, **kw):
            attempt[0] += 1
            if attempt[0] == 1:
                raise err_429
            return iter([_make_chunk("backup ok"), _make_done_chunk()])

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = stream_side_effect
        mock_openai_cls.return_value = mock_instance

        with patch("sys.stderr"):
            result = self.mgr.run_wee_native(
                prompt="test",
                model="openrouter/primary:free",
                agent="test",
                session_id="s1",
                resume=False,
                n8n_session_id="n4",
            )

        push_calls = stream_buf.push.call_args_list
        fallback_pushed = any(
            any(kw in str(c).lower() for kw in ("fallback", "rate limited", "switching"))
            for c in push_calls
        )
        self.assertTrue(fallback_pushed, "B02: fallback notification must be pushed to stream buffer")
        self.assertIn("backup ok", result)

    @patch("agent_manager.time.sleep")
    @patch("openai.OpenAI")
    def test_issue_125_non_free_model_no_fallback(self, mock_openai_cls, mock_sleep):
        """Non-:free models must NOT use the fallback chain."""
        free_cfg = {
            "max_retries_per_model": 3,
            "retry_backoff_seconds": [1, 2, 5],
            "free_model_fallback_chain": ["openrouter/b:free", "openrouter/c:free"],
        }
        self.mgr._wee_load_free_config = MagicMock(return_value=free_cfg)

        err_429 = Exception("429 rate limit")
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = err_429
        mock_openai_cls.return_value = mock_instance

        with patch("sys.stderr"):
            result = self.mgr.run_wee_native(
                prompt="test",
                model="ollama/llama3",
                agent="test",
                session_id="s1",
                resume=False,
                n8n_session_id="n5",
            )

        # Should get a non-429 error (non-free model, no fallback)
        self.assertIn("Error", result)
        # sleep should NOT be called (non-free models don't retry)
        mock_sleep.assert_not_called()

    @patch("agent_manager.time.sleep")
    @patch("openai.OpenAI")
    def test_issue_125_sleep_called_on_retry(self, mock_openai_cls, mock_sleep):
        """M01: time.sleep (not asyncio.sleep) must be called between retries."""
        free_cfg = {
            "max_retries_per_model": 2,
            "retry_backoff_seconds": [3, 7],
            "free_model_fallback_chain": ["openrouter/primary:free"],
        }
        self.mgr._wee_load_free_config = MagicMock(return_value=free_cfg)

        attempt = [0]
        err_429 = Exception("429 rate limit")

        def stream_side_effect(*a, **kw):
            attempt[0] += 1
            if attempt[0] < 2:
                raise err_429
            return iter([_make_chunk("ok"), _make_done_chunk()])

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = stream_side_effect
        mock_openai_cls.return_value = mock_instance

        with patch("sys.stderr"):
            result = self.mgr.run_wee_native(
                prompt="test",
                model="openrouter/primary:free",
                agent="test",
                session_id="s1",
                resume=False,
                n8n_session_id="n6",
            )

        self.assertIn("ok", result)
        # sleep should have been called with the backoff value
        mock_sleep.assert_called()
        sleep_args = [c[0][0] for c in mock_sleep.call_args_list]
        self.assertIn(3, sleep_args, "Should sleep 3s per backoff config")


class TestIssue125HelperMethods(unittest.TestCase):
    """Unit tests for _wee_load_free_config, _wee_is_free_model, _wee_resolve_endpoint."""

    def setUp(self):
        self.mgr = _make_mgr()

    def test_issue_125_config_file_exists(self):
        """wee_free_models.json config file must exist in repo root."""
        import json
        config_path = REPO / "wee_free_models.json"
        self.assertTrue(config_path.exists(), "wee_free_models.json must exist")
        with open(config_path) as f:
            cfg = json.load(f)
        self.assertIn("free_model_fallback_chain", cfg)
        self.assertIn("max_retries_per_model", cfg)
        self.assertIn("retry_backoff_seconds", cfg)
        self.assertIsInstance(cfg["free_model_fallback_chain"], list)

    def test_issue_125_config_default_on_missing(self):
        """_wee_load_free_config must return defaults if file missing."""
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = self.mgr._wee_load_free_config()
        self.assertIn("free_model_fallback_chain", result)
        self.assertIn("max_retries_per_model", result)

    def test_issue_125_free_model_detection(self):
        """_wee_is_free_model must correctly identify :free models."""
        self.assertTrue(self.mgr._wee_is_free_model("openrouter/anthropic/claude-3-haiku:free"))
        self.assertTrue(self.mgr._wee_is_free_model("openrouter/mistral/mistral-7b-instruct:free"))
        self.assertFalse(self.mgr._wee_is_free_model("ollama/llama3.2"))
        self.assertFalse(self.mgr._wee_is_free_model("anthropic/claude-3-haiku"))

    def test_issue_125_resolve_endpoint_ollama(self):
        """_wee_resolve_endpoint must resolve ollama/ prefix correctly."""
        base, key, model = self.mgr._wee_resolve_endpoint("ollama/llama3.2", None, None)
        self.assertIn("11434", base)
        self.assertEqual(model, "llama3.2")

    def test_issue_125_resolve_endpoint_openrouter(self):
        """_wee_resolve_endpoint must resolve openrouter/ prefix correctly."""
        base, key, model = self.mgr._wee_resolve_endpoint(
            "openrouter/anthropic/claude:free", None, None
        )
        self.assertIn("openrouter.ai", base)
        self.assertEqual(model, "anthropic/claude:free")

    def test_issue_125_time_sleep_not_asyncio_sleep(self):
        """M01: run_wee_native/_wee_run_attempt must use time.sleep, not asyncio.sleep.

        Issue #175: sleep logic was moved to _wee_run_attempt(); verify both
        the orchestration path and the attempt helper use _time.sleep (not asyncio).
        """
        import inspect
        src_native = inspect.getsource(SessionManager.run_wee_native)
        src_attempt = inspect.getsource(SessionManager._wee_run_attempt)
        combined_src = src_native + src_attempt
        self.assertIn(
            "_time.sleep", combined_src, "Must use time.sleep via _time alias"
        )
        self.assertNotIn(
            "asyncio.sleep", combined_src, "Must NOT use asyncio.sleep in sync function"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
