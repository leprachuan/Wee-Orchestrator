"""
Regression tests for Issue #128 BLOCKER B01:
__WEE_META__ markers must be stripped from user-facing output.

Background: wee_runtime.py prints __WEE_META__ {json} to stdout for token
usage reporting. strip_metadata(output, "wee") must filter these lines so
they never reach the user.

Filed by wee-qa as a mandatory regression test per standing policy.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_mgr():
    """Return a lightweight SessionManager for testing strip_metadata."""
    from agent_manager import SessionManager

    return SessionManager.__new__(SessionManager)


class TestIssue128B01WeeMetaStrip(unittest.TestCase):
    """Regression: strip_metadata must filter __WEE_META__ lines for wee runtime."""

    def test_wee_meta_stripped_from_output(self):
        """B01: __WEE_META__ line must not appear in stripped output."""
        mgr = _make_mgr()
        raw = (
            "Here is the answer to your question.\n"
            '__WEE_META__ {"tokens": 150, "cost": 0.0}\n'
        )
        result = mgr.strip_metadata(raw, "wee")
        self.assertNotIn("__WEE_META__", result)
        self.assertIn("Here is the answer", result)

    def test_wee_meta_only_output_returns_empty(self):
        """B01: If output is only __WEE_META__, result should be empty."""
        mgr = _make_mgr()
        raw = '__WEE_META__ {"tokens": 42}\n'
        result = mgr.strip_metadata(raw, "wee")
        self.assertEqual(result.strip(), "")

    def test_wee_meta_mixed_content_preserves_text(self):
        """B01: Normal lines must survive when __WEE_META__ is mixed in."""
        mgr = _make_mgr()
        raw = (
            "Line one\n"
            "Line two\n"
            '__WEE_META__ {"tokens": 200, "model": "gemma4"}\n'
            "Line three\n"
        )
        result = mgr.strip_metadata(raw, "wee")
        self.assertIn("Line one", result)
        self.assertIn("Line two", result)
        self.assertIn("Line three", result)
        self.assertNotIn("__WEE_META__", result)


if __name__ == "__main__":
    unittest.main()
