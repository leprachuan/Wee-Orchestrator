"""Regression tests for Issue #128 QA fixes.

B01: __WEE_META__ lines must be stripped by strip_metadata() for wee runtime.
Without the fix, background wee tasks expose raw JSON metadata to the user.
"""
import sys
import threading
import unittest

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")
from agent_manager import SessionManager


def _make_mgr():
    """Create a minimal SessionManager for testing strip_metadata."""
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_map = {}
    mgr._session_map_lock = threading.Lock()
    mgr.command_timeout = 300
    mgr.AGENTS = {"orchestrator": {"path": "/opt", "description": "test", "name": "orchestrator"}}
    mgr._stream_buffers = {}
    return mgr


class TestIssue128B01WeeMetaLeak(unittest.TestCase):
    """Regression: strip_metadata must filter __WEE_META__ lines for wee runtime."""

    def test_issue_128_strip_metadata_removes_wee_meta(self):
        """strip_metadata should remove __WEE_META__ lines from wee output."""
        mgr = _make_mgr()
        test_output = "Hello from wee.\n__WEE_META__ {\"tokens\": 150, \"cost\": 0.0}\nDone."
        result = mgr.strip_metadata(test_output, "wee")
        self.assertNotIn("__WEE_META__", result)
        self.assertIn("Hello from wee.", result)
        self.assertIn("Done.", result)

    def test_issue_128_wee_meta_with_leading_whitespace(self):
        """strip_metadata handles __WEE_META__ with leading whitespace."""
        mgr = _make_mgr()
        test_output = "Response.\n  __WEE_META__ {\"tokens\": 42}\n"
        result = mgr.strip_metadata(test_output, "wee")
        self.assertNotIn("__WEE_META__", result)
        self.assertIn("Response.", result)

    def test_issue_128_only_wee_meta_returns_empty(self):
        """When output is only __WEE_META__, result should be empty."""
        mgr = _make_mgr()
        test_output = "__WEE_META__ {\"tokens\": 100}"
        result = mgr.strip_metadata(test_output, "wee")
        self.assertNotIn("__WEE_META__", result)
        self.assertEqual(result.strip(), "")


if __name__ == "__main__":
    unittest.main()
