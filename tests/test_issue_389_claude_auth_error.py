"""Regression test for Issue #389: Local chat surfaces opaque Claude runtime 401.

A Local agent configured with primary runtime `claude` returned the raw
Anthropic API error verbatim ("Failed to authenticate. API Error: 401 Invalid
authentication credentials") when the `claude` CLI had no configured
credential. Users had no indication that the failure was a runtime
credential problem rather than a Wee Orchestrator bug.

Verifies that `SessionManager.strip_metadata(..., "claude")` recognizes both
forms the Claude CLI can emit an authentication failure in:
  1. A structured stream-json `{"type":"error", ...}` event.
  2. Bare (non-JSON) stderr text.
and replaces the raw 401 text with a clear, actionable message instead of
passing it through unchanged.
"""

import os
import sys
import threading
import unittest
from pathlib import Path

os.environ.setdefault("API_SHARED_KEY", "test_key_123")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_manager import SessionManager  # noqa: E402


def _make_mgr():
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_map = {}
    mgr._session_map_lock = threading.Lock()
    mgr.command_timeout = 300
    return mgr


class TestIssue389ClaudeAuthError(unittest.TestCase):
    def setUp(self):
        self.mgr = _make_mgr()

    def test_plain_text_401_is_replaced_with_guidance(self):
        """The raw stderr text reported in the issue must not reach the user."""
        raw = "Failed to authenticate. API Error: 401 Invalid authentication credentials\n"
        result = self.mgr.strip_metadata(raw, "claude")

        self.assertNotIn("401", result)
        self.assertIn("claude login", result)
        self.assertIn("ANTHROPIC_API_KEY", result)

    def test_structured_error_event_is_replaced_with_guidance(self):
        """A stream-json {"type":"error"} auth failure must also be normalized."""
        raw = (
            '{"type":"error","error":{"type":"authentication_error",'
            '"message":"Invalid authentication credentials"}}\n'
        )
        result = self.mgr.strip_metadata(raw, "claude")

        self.assertNotIn("Invalid authentication credentials", result)
        self.assertIn("claude login", result)
        self.assertIn("ANTHROPIC_API_KEY", result)

    def test_non_auth_error_event_is_not_misclassified(self):
        """Unrelated API errors (e.g. rate limits) must keep passing through."""
        raw = (
            '{"type":"error","error":{"type":"rate_limit_error",'
            '"message":"You have hit the rate limit"}}\n'
        )
        result = self.mgr.strip_metadata(raw, "claude")

        self.assertIn("rate_limit_error", result)
        self.assertIn("rate limit", result)
        self.assertNotIn("claude login", result)


if __name__ == "__main__":
    unittest.main()
