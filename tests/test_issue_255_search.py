#!/usr/bin/env python3
"""Tests for Issue #255: SearXNG search tool in Wee Runtime.

Tests verify:
- search tool is present in _WEE_TOOLS with correct schema
- _execute_search returns graceful error when SearXNG is unavailable
- _execute_search returns text format results correctly
- _execute_search returns JSON format results correctly
- _execute_search enforces count limit
- execute_tool dispatches 'search' to _execute_search
- _ANTI_HALLUCINATION_PROMPT and _WEE_TOOL_CAPABILITY_PROMPT are importable
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from wee_runtime import (
    _ANTI_HALLUCINATION_PROMPT,
    _WEE_TOOL_CAPABILITY_PROMPT,
    _WEE_TOOLS,
    _execute_search,
    execute_tool,
)


class TestSearchToolDefinition(unittest.TestCase):
    """Verify search tool schema is registered in _WEE_TOOLS."""

    def _get_tool(self, name):
        for t in _WEE_TOOLS:
            if t["function"]["name"] == name:
                return t
        return None

    def test_search_tool_exists(self):
        tool = self._get_tool("search")
        self.assertIsNotNone(tool, "search tool missing from _WEE_TOOLS")

    def test_search_tool_type(self):
        tool = self._get_tool("search")
        self.assertEqual(tool["type"], "function")

    def test_search_tool_has_q_parameter(self):
        tool = self._get_tool("search")
        props = tool["function"]["parameters"]["properties"]
        self.assertIn("q", props)
        self.assertEqual(props["q"]["type"], "string")

    def test_search_tool_q_is_required(self):
        tool = self._get_tool("search")
        required = tool["function"]["parameters"].get("required", [])
        self.assertIn("q", required)

    def test_search_tool_has_count_parameter(self):
        tool = self._get_tool("search")
        props = tool["function"]["parameters"]["properties"]
        self.assertIn("count", props)
        self.assertEqual(props["count"]["type"], "integer")

    def test_search_tool_has_format_parameter(self):
        tool = self._get_tool("search")
        props = tool["function"]["parameters"]["properties"]
        self.assertIn("format", props)
        self.assertIn("json", props["format"]["enum"])
        self.assertIn("text", props["format"]["enum"])

    def test_all_three_tools_present(self):
        names = [t["function"]["name"] for t in _WEE_TOOLS]
        self.assertIn("bash", names)
        self.assertIn("python", names)
        self.assertIn("search", names)


class TestExecuteSearch(unittest.TestCase):
    """Test _execute_search() behavior."""

    def _fake_urlopen(self, results):
        """Return a context manager mock that yields SearXNG-like JSON."""
        import io
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
        mock_urlopen.return_value = self._fake_urlopen([
            {"title": "Claude AI", "url": "https://example.com/claude", "content": "Claude is an AI."},
        ])
        result = _execute_search({"q": "Claude AI", "format": "text"})
        self.assertIn("Claude AI", result)
        self.assertIn("example.com", result)

    @patch("urllib.request.urlopen")
    def test_json_format_returns_valid_json(self, mock_urlopen):
        mock_urlopen.return_value = self._fake_urlopen([
            {"title": "Claude AI", "url": "https://example.com/claude", "content": "Claude is an AI."},
        ])
        result = _execute_search({"q": "Claude AI", "format": "json"})
        parsed = json.loads(result)
        self.assertIsInstance(parsed, list)
        self.assertEqual(parsed[0]["title"], "Claude AI")
        self.assertEqual(parsed[0]["url"], "https://example.com/claude")

    @patch("urllib.request.urlopen")
    def test_count_limits_results(self, mock_urlopen):
        many_results = [
            {"title": f"Result {i}", "url": f"https://example.com/{i}", "content": f"Content {i}"}
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

    @patch("urllib.request.urlopen")
    def test_unavailable_searxng_returns_error_not_exception(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        result = _execute_search({"q": "test"})
        self.assertIn("Search unavailable", result)
        # Should NOT raise — graceful degradation
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
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.side_effect = urllib.error.URLError("refused")
                result = _execute_search({"q": "test"})
                # Verify the custom URL was used (appears in the error message)
                self.assertIn("custom-host:9999", result)


class TestExecuteToolDispatching(unittest.TestCase):
    """Verify execute_tool() dispatches 'search' correctly."""

    @patch("wee_runtime._execute_search")
    def test_execute_tool_calls_execute_search(self, mock_search):
        mock_search.return_value = "mocked result"
        result = execute_tool("search", {"q": "test query"})
        mock_search.assert_called_once_with({"q": "test query"})
        self.assertEqual(result, "mocked result")

    def test_execute_tool_unknown_name(self):
        result = execute_tool("nonexistent_tool", {})
        self.assertIn("Unknown tool", result)


class TestImports(unittest.TestCase):
    """Verify symbols imported by wee_cli.py exist in wee_runtime."""

    def test_anti_hallucination_prompt_is_string(self):
        self.assertIsInstance(_ANTI_HALLUCINATION_PROMPT, str)
        self.assertGreater(len(_ANTI_HALLUCINATION_PROMPT), 10)

    def test_tool_capability_prompt_is_string(self):
        self.assertIsInstance(_WEE_TOOL_CAPABILITY_PROMPT, str)
        self.assertGreater(len(_WEE_TOOL_CAPABILITY_PROMPT), 10)

    def test_tool_capability_prompt_mentions_search(self):
        self.assertIn("search", _WEE_TOOL_CAPABILITY_PROMPT.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
