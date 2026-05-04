"""
Issue #333 - Bug: SearXNG search tool not exposed to Wee runtime

The search tool (SearXNG) added in Issue #255 (PR #264) is defined in wee_runtime.py
but NOT included in the Wee runtime's system prompt augmentation in agent_manager.py.

This test ensures the 'search' tool is now properly exposed in the system prompt.
"""

import unittest
from pathlib import Path
from agent_manager import SessionManager


def _make_mgr():
    """Create a minimal SessionManager for testing."""
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_state_dir = Path("/tmp/test_sessions_333")
    return mgr


class TestIssue333SearchToolExposed(unittest.TestCase):
    """Issue #333: Search tool must be exposed in system prompt augmentation."""

    def setUp(self):
        self.mgr = _make_mgr()

    def test_search_tool_in_system_prompt(self):
        """The 'search' tool should be declared in the augmented system prompt."""
        result = self.mgr._wee_augment_system_prompt_with_tools(
            "You are a helpful assistant."
        )
        self.assertIn("**search**", result, 
                     "Search tool declaration missing from system prompt")

    def test_search_tool_description(self):
        """The search tool description should mention SearXNG."""
        result = self.mgr._wee_augment_system_prompt_with_tools("")
        self.assertIn("SearXNG", result, 
                     "SearXNG description missing")
        self.assertIn("web search results", result, 
                     "Search tool description incomplete")

    def test_search_tool_parameters(self):
        """The search tool parameters should be documented."""
        result = self.mgr._wee_augment_system_prompt_with_tools("")
        self.assertIn("q", result, "Search query parameter missing")
        self.assertIn("count", result, "Count parameter missing")
        self.assertIn("format", result, "Format parameter missing")

    def test_search_tool_env_requirement(self):
        """The search tool environment requirement should be documented."""
        result = self.mgr._wee_augment_system_prompt_with_tools("")
        self.assertIn("WEE_SEARXNG_URL", result, 
                     "WEE_SEARXNG_URL environment variable not documented")
        self.assertIn("http://localhost:8888", result, 
                     "Default SearXNG URL not documented")

    def test_all_tools_present(self):
        """All documented tools should be present: call_agent, bash, python, search."""
        result = self.mgr._wee_augment_system_prompt_with_tools("")
        self.assertIn("**call_agent**", result)
        self.assertIn("**bash**", result)
        self.assertIn("**python**", result)
        self.assertIn("**search**", result)

    def test_search_tool_after_python(self):
        """Search tool should appear in the tools list."""
        result = self.mgr._wee_augment_system_prompt_with_tools("")
        python_idx = result.find("**python**")
        search_idx = result.find("**search**")
        self.assertGreater(python_idx, 0, "python tool not found")
        self.assertGreater(search_idx, 0, "search tool not found")
        self.assertLess(python_idx, search_idx, 
                       "Search tool should appear after python tool in the list")


if __name__ == "__main__":
    unittest.main()
