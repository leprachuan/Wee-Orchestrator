"""
Regression tests for issue #397: local Wee agents need a web *search* tool.

The Copilot SDK exposes 14 built-in tools, confirmed by capturing the request it
sends through a logging proxy:

    apply_patch, bash, glob, list_agents, list_bash, read_agent, read_bash,
    rg, skill, sql, stop_bash, task, view, web_fetch

`web_fetch` retrieves a URL that is already known; none of them can discover
pages for a query. So an Ollama-backed agent asked for current information had
no way to get it.

The implementation was never missing — `wee_runtime._execute_search` (SearXNG
with a public-search fallback) survived #443, which removed only its caller when
the runtime moved to the SDK loop. This wires it back as a registered SDK tool.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_397")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9397")

from agent_manager import SessionManager  # noqa: E402


def _make_sm():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"agents": []}, tmp)
    tmp.close()
    return SessionManager(tmp.name)


class TestIssue397SearchToolIsWired(unittest.TestCase):
    def setUp(self):
        self.sm = _make_sm()

    def test_search_routes_to_the_surviving_implementation(self):
        with patch("wee_runtime._execute_search", return_value="1. Result") as search:
            result = self.sm._wee_execute_tool(
                "search", {"q": "hash maps", "count": 3}, "orchestrator", "sess-1"
            )

        self.assertEqual(result, "1. Result")
        search.assert_called_once_with({"q": "hash maps", "count": 3})

    def test_search_arguments_are_passed_through_untouched(self):
        """_execute_search owns its own defaults and clamping; don't second-guess."""
        with patch("wee_runtime._execute_search", return_value="ok") as search:
            self.sm._wee_execute_tool(
                "search", {"q": "x", "count": 99, "format": "json"}, "a", "s"
            )
        search.assert_called_once_with({"q": "x", "count": 99, "format": "json"})

    def test_unknown_tool_message_lists_search(self):
        message = self.sm._wee_execute_tool("nope", {}, "orchestrator", "sess-1")

        self.assertIn("Unknown tool 'nope'", message)
        self.assertIn("search", message)
        self.assertIn("call_agent", message)
        self.assertIn("browser", message)

    def test_search_failure_is_reported_not_raised(self):
        """A failing tool must not break the agentic turn."""
        with patch("wee_runtime._execute_search", side_effect=RuntimeError("boom")):
            message = self.sm._wee_execute_tool("search", {"q": "x"}, "a", "s")

        self.assertIn("Error executing search", message)
        self.assertIn("boom", message)

    def test_search_tool_is_registered_on_the_sdk_session(self):
        import inspect

        source = inspect.getsource(self.sm.run_wee_native)

        self.assertIn('name="search"', source, "search must be a registered SDK Tool")
        self.assertIn(
            "tools=[search_tool, call_agent_tool, browser_tool]",
            source,
            "the search tool must be passed to the SDK session",
        )
        # The #398 completion retry re-creates the session; it must offer the
        # same tools, otherwise a retry would be less capable than the turn it
        # is retrying.
        self.assertEqual(
            source.count("tools=[search_tool, call_agent_tool, browser_tool]"),
            2,
            "both the initial turn and the #398 retry must register search",
        )

    def test_search_is_advertised_to_the_model(self):
        """An unadvertised tool is one the model will not reach for."""
        prompt = self.sm._wee_augment_system_prompt_with_tools("SYSTEM")

        self.assertIn("**search**", prompt)
        self.assertIn('"q"', prompt)
        # Steer away from web_fetch for discovery, which is the #397 confusion.
        self.assertIn("web_fetch", prompt)


if __name__ == "__main__":
    unittest.main()
