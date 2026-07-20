#!/usr/bin/env python3
"""Tests for Issue #255: SearXNG search helper in Wee Runtime.

Issue #443 removed the hand-rolled bash/python/search tool-dispatch loop
(_WEE_TOOLS, execute_tool, _WEE_TOOL_CAPABILITY_PROMPT) — the Copilot SDK is
now the only execution path. `_execute_search` itself survives as a plain
helper still used by agent_manager.py's call_agent/browser tool handler, so
its behavior is still covered here.

Tests verify:
- _execute_search returns graceful error when SearXNG is unavailable
- _execute_search returns text format results correctly
- _execute_search returns JSON format results correctly
- _execute_search enforces count limit
- _ANTI_HALLUCINATION_PROMPT is importable
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from wee_runtime import (  # noqa: E402
    _ANTI_HALLUCINATION_PROMPT,
    _execute_search,
)


class TestExecuteSearch(unittest.TestCase):
    """Test _execute_search() behavior."""

    def _fake_urlopen(self, results):
        """Return a context manager mock that yields SearXNG-like JSON."""
        payload = json.dumps({"results": results}).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = payload
        return mock_resp

    def test_empty_query_returns_error(self):
        result = _execute_search({"q": ""})
        self.assertIn("Error", result)
        self.assertIn("required", result)

    def test_missing_query_returns_error(self):
        result = _execute_search({})
        self.assertIn("Error", result)

    @patch("urllib.request.urlopen")
    def test_text_format_returns_summary(self, mock_urlopen):
        mock_urlopen.return_value = self._fake_urlopen(
            [
                {
                    "title": "Claude AI",
                    "url": "https://example.com/claude",
                    "content": "Claude is an AI.",
                },
            ]
        )
        result = _execute_search({"q": "Claude AI", "format": "text"})
        self.assertIn("Claude AI", result)
        self.assertIn("example.com", result)

    @patch("urllib.request.urlopen")
    def test_json_format_returns_valid_json(self, mock_urlopen):
        mock_urlopen.return_value = self._fake_urlopen(
            [
                {
                    "title": "Claude AI",
                    "url": "https://example.com/claude",
                    "content": "Claude is an AI.",
                },
            ]
        )
        result = _execute_search({"q": "Claude AI", "format": "json"})
        parsed = json.loads(result)
        self.assertIsInstance(parsed, list)
        self.assertEqual(parsed[0]["title"], "Claude AI")
        self.assertEqual(parsed[0]["url"], "https://example.com/claude")

    @patch("urllib.request.urlopen")
    def test_count_limits_results(self, mock_urlopen):
        many_results = [
            {
                "title": f"Result {i}",
                "url": f"https://example.com/{i}",
                "content": f"Content {i}",
            }
            for i in range(10)
        ]
        mock_urlopen.return_value = self._fake_urlopen(many_results)
        result = _execute_search({"q": "test", "count": 3, "format": "json"})
        parsed = json.loads(result)
        self.assertEqual(len(parsed), 3)

    @patch("urllib.request.urlopen")
    def test_count_capped_at_20(self, mock_urlopen):
        # Even if user requests 100, we cap at 20 in the query
        many_results = [
            {"title": f"R{i}", "url": f"https://ex.com/{i}", "content": ""}
            for i in range(25)
        ]
        mock_urlopen.return_value = self._fake_urlopen(many_results)
        result = _execute_search({"q": "test", "count": 100, "format": "json"})
        parsed = json.loads(result)
        self.assertLessEqual(len(parsed), 20)

    @patch("urllib.request.urlopen")
    def test_no_results_returns_graceful_message(self, mock_urlopen):
        mock_urlopen.return_value = self._fake_urlopen([])
        result = _execute_search({"q": "xyzzy_nonexistent_query_12345"})
        self.assertIn("No results", result)

    @patch("wee_runtime._execute_brave_search")
    @patch("urllib.request.urlopen")
    def test_unavailable_searxng_uses_public_fallback(self, mock_urlopen, mock_fallback):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        mock_fallback.return_value = "Search results for: test\n\n1. Fallback result"
        result = _execute_search({"q": "test"})
        self.assertIn("Fallback result", result)
        mock_fallback.assert_called_once()
        self.assertIsInstance(result, str)

    def test_result_under_2000_chars(self):
        """Result must stay under 2000 chars to avoid context bloat."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            # Inject a result with very long content
            big_results = [
                {"title": "X" * 500, "url": "https://example.com", "content": "Y" * 500}
                for _ in range(20)
            ]
            mock_urlopen.return_value = self._fake_urlopen(big_results)
            result = _execute_search({"q": "test", "count": 20, "format": "text"})
            self.assertLessEqual(len(result), 2000)

    def test_env_var_overrides_searxng_url(self):
        """WEE_SEARXNG_URL env var should be used if set."""
        import urllib.error

        with patch.dict(os.environ, {"WEE_SEARXNG_URL": "http://custom-host:9999"}):
            with patch("wee_runtime._execute_brave_search") as mock_fallback, patch("urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.side_effect = urllib.error.URLError("refused")
                mock_fallback.return_value = "fallback"
                self.assertEqual(_execute_search({"q": "test"}), "fallback")
                self.assertEqual(mock_fallback.call_args.args[3], "http://custom-host:9999")


class TestImports(unittest.TestCase):
    """Verify symbols imported by wee_cli.py exist in wee_runtime."""

    def test_anti_hallucination_prompt_is_string(self):
        self.assertIsInstance(_ANTI_HALLUCINATION_PROMPT, str)
        self.assertGreater(len(_ANTI_HALLUCINATION_PROMPT), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
