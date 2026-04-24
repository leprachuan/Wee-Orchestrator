"""Tests for Issue #94: claude-sdk elevated mode broken — args swapped.

Regression tests for the specific bug where _resolve_permission_mode()
was called with (mode, session_data) instead of (session_data, mode) in
run_claude_sdk(). This caused the session_data dict to land in the
prompt_mode parameter, making dict != "restricted" always True, so
the dict was returned as the mode string. Downstream, mode == "elevated"
was always False, so bypassPermissions was never set.

Fix: commit a9ccce4 on dev (part of Issue #91).
"""

import os
import pathlib
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.environ.setdefault("API_SHARED_KEY", "test_key_123")


def _get_session_mgr():
    """Create a minimal SessionManager for testing."""
    from agent_manager import SessionManager

    mgr = SessionManager.__new__(SessionManager)
    mgr.mode = None
    mgr.command_timeout = 300
    mgr._session_map_lock = threading.Lock()
    mgr.session_map_file = pathlib.Path("/tmp/_test_issue94_session_map.json")
    mgr.session_state_dir = pathlib.Path("/tmp/_test_issue94_sessions")
    mgr.session_state_dir.mkdir(parents=True, exist_ok=True)
    mgr.copilot_bin = "/usr/local/bin/copilot"
    mgr.AGENTS = {
        "orchestrator": {
            "path": "/opt/n8n-copilot-shim-dev",
            "description": "Test",
            "name": "orchestrator",
        }
    }
    mgr.skill_repositories = []
    mgr._bg_identity = None
    mgr._bg_task_mgr = None
    mgr._stream_buffers = {}
    if not mgr.session_map_file.exists():
        mgr.session_map_file.write_text("{}")
    return mgr


class TestIssue94ArgsSwapBug(unittest.TestCase):
    """Regression: _resolve_permission_mode must never return a dict.

    When args were swapped (mode, session_data), the session_data dict
    landed in prompt_mode. Since dict != "restricted" is True, the function
    returned the dict object as the "mode", which is never == "elevated",
    so bypassPermissions was never set for claude-sdk.
    """

    def test_swapped_args_would_return_dict(self):
        """Demonstrate the bug: swapped args cause dict to be returned as mode."""
        mgr = _get_session_mgr()
        session_data = {"permissions": {"mode": "elevated"}}
        mode_str = "restricted"

        # WRONG call order (the bug): mode string in first arg, dict in second
        result = mgr._resolve_permission_mode(mode_str, session_data)
        # With swapped args, session_data (a dict) lands in prompt_mode.
        # dict != "restricted" is True, so it returns the dict — a dict, not a string.
        self.assertIsInstance(result, dict,
            "Bug scenario: swapped args should return dict (demonstrating the bug)")

    def test_correct_args_returns_string(self):
        """With correct arg order, mode is always a string."""
        mgr = _get_session_mgr()
        session_data = {"permissions": {"mode": "elevated"}}
        mode_str = "restricted"

        # CORRECT call order
        result = mgr._resolve_permission_mode(session_data, mode_str)
        self.assertIsInstance(result, str,
            "Correct arg order must always return a string mode")
        self.assertEqual(result, "elevated",
            "Elevated session permissions must resolve to 'elevated'")

    def test_elevated_mode_never_dict(self):
        """mode == 'elevated' comparison must succeed (not dict == str)."""
        mgr = _get_session_mgr()
        session_data = {"permissions": {"mode": "elevated"}}

        result = mgr._resolve_permission_mode(session_data, "restricted")
        # This is the key check: mode must equal the string "elevated"
        self.assertEqual(result, "elevated")
        # And the downstream mapping must work
        sdk_permission_mode = (
            "bypassPermissions" if result == "elevated"
            else "plan" if result == "sandboxed"
            else "default"
        )
        self.assertEqual(sdk_permission_mode, "bypassPermissions",
            "Elevated mode must map to bypassPermissions for claude-sdk")

    def test_background_task_elevated_resolves_correctly(self):
        """Background task with elevated permissions resolves to bypassPermissions."""
        mgr = _get_session_mgr()
        # Simulate what _execute_background_task does: sets permissions.mode = elevated
        session_data = {"permissions": {"mode": "elevated"}, "channel": "api"}

        mode = mgr._resolve_permission_mode(session_data, "restricted")
        self.assertEqual(mode, "elevated")

        # Verify downstream mapping (mirrors run_claude_sdk logic)
        if mode == "elevated":
            sdk_permission_mode = "bypassPermissions"
        elif mode == "sandboxed":
            sdk_permission_mode = "plan"
        else:
            sdk_permission_mode = "default"
        self.assertEqual(sdk_permission_mode, "bypassPermissions")

    def test_sandboxed_mode_correct_with_proper_args(self):
        """Sandboxed permissions map to 'plan' in claude-sdk."""
        mgr = _get_session_mgr()
        session_data = {"permissions": {"mode": "sandboxed"}}

        result = mgr._resolve_permission_mode(session_data, "restricted")
        self.assertEqual(result, "sandboxed")

    def test_restricted_default_mode(self):
        """Default restricted mode maps to 'default' in claude-sdk."""
        mgr = _get_session_mgr()
        session_data = {"permissions": {"mode": "restricted"}}

        result = mgr._resolve_permission_mode(session_data, "restricted")
        self.assertEqual(result, "restricted")

    def test_prompt_mode_elevated_overrides_session(self):
        """If prompt_mode is 'elevated' (not default), it takes priority."""
        mgr = _get_session_mgr()
        session_data = {"permissions": {"mode": "restricted"}}

        result = mgr._resolve_permission_mode(session_data, "elevated")
        self.assertEqual(result, "elevated",
            "prompt_mode 'elevated' must override session permissions")

    def test_return_type_is_always_string(self):
        """_resolve_permission_mode must ALWAYS return a string, never a dict."""
        mgr = _get_session_mgr()
        test_cases = [
            ({"permissions": {"mode": "elevated"}}, "restricted"),
            ({"permissions": {"mode": "restricted"}}, "restricted"),
            ({"permissions": {"mode": "sandboxed"}}, "restricted"),
            ({"permissions": None}, "restricted"),
            ({}, "restricted"),
            ({"permissions": {"mode": "elevated"}}, "elevated"),
            ({"yolo_mode": "on"}, "restricted"),
        ]
        for session_data, prompt_mode in test_cases:
            result = mgr._resolve_permission_mode(session_data, prompt_mode)
            self.assertIsInstance(result, str,
                f"Must return str, got {type(result).__name__} for "
                f"session_data={session_data}, prompt_mode={prompt_mode}")


if __name__ == "__main__":
    unittest.main()
