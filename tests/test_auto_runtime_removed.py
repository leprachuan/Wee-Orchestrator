"""
Tests for Issue #84: Remove non-functional auto runtime.

Verifies that the 'auto' runtime has been completely removed from the system.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("API_SHARED_KEY", "test_key_123")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_manager import check_runtime_available, get_available_runtimes


class TestAutoRuntimeRemoved(unittest.TestCase):
    """Issue #84: auto runtime must not appear anywhere in the system."""

    def test_auto_not_in_available_runtimes(self):
        """get_available_runtimes() must not include auto."""
        runtimes = get_available_runtimes()
        runtime_ids = [r["id"] for r in runtimes]
        self.assertNotIn("auto", runtime_ids,
                         "'auto' should not appear in available runtimes list")

    def test_check_runtime_available_rejects_auto(self):
        """check_runtime_available('auto') must return False."""
        self.assertFalse(check_runtime_available("auto"),
                         "'auto' runtime should not be available")

    def test_slash_runtime_set_rejects_auto(self):
        """The /runtime set handler must reject 'auto' as invalid."""
        from agent_manager import SessionManager
        mgr = SessionManager.__new__(SessionManager)
        mgr._slash_commands = {}
        mgr._agents_config = {"agents": []}
        mgr._skills_dir = None
        mgr._register_slash = lambda *a, **kw: None

        session_data = {"runtime": "copilot", "agent": "orchestrator"}
        result = mgr._slash_runtime("set auto", session_data, "test-session")
        self.assertIn("Unknown runtime", result,
                      "/runtime set auto should return 'Unknown runtime' error")

    def test_valid_runtimes_all_have_handlers(self):
        """Every runtime in get_available_runtimes() must have a check_runtime_available entry."""
        runtimes = get_available_runtimes()
        for rt in runtimes:
            rid = rt["id"]
            self.assertTrue(
                check_runtime_available(rid),
                f"Runtime {rid} is listed but check_runtime_available returns False"
            )


if __name__ == "__main__":
    unittest.main()
