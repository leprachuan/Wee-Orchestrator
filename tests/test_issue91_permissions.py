"""Tests for Issue #91: Permissions not honored for copilot-sdk and claude-sdk runtimes.

Tests cover:
- _resolve_permission_mode handles None permissions (Bug 1)
- execute() handles None permissions (Bug 1 mirror)
- run_copilot_sdk accepts and uses mode parameter (Bug 2+3)
- _dispatch_single_runtime passes mode to copilot-sdk (Bug 3)
- Background tasks get elevated permissions (Bug 4)
- /mode current shows correct mode when permissions is None
"""

import os
import pathlib
import sys
import threading
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.environ.setdefault("API_SHARED_KEY", "test_key_123")


def _get_session_mgr():
    """Create a minimal SessionManager for testing."""
    from agent_manager import SessionManager

    mgr = SessionManager.__new__(SessionManager)
    mgr.mode = None
    mgr.command_timeout = 300
    mgr._session_map_lock = threading.Lock()
    mgr.session_map_file = pathlib.Path("/tmp/_test_issue91_session_map.json")
    mgr.session_state_dir = pathlib.Path("/tmp/_test_issue91_sessions")
    mgr.session_state_dir.mkdir(parents=True, exist_ok=True)
    mgr.copilot_bin = "/usr/local/bin/copilot"
    mgr.AGENTS = {
        "orchestrator": {
            # Real directory — dispatch refuses a missing agent workspace (iOS #8).
            "path": __import__("tempfile").gettempdir(),
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


# =========================================================================
# Bug 1: _resolve_permission_mode handles None permissions
# =========================================================================
class TestResolvePermissionModeNone(unittest.TestCase):
    """_resolve_permission_mode must handle permissions=None correctly."""

    def test_none_permissions_falls_through_to_restricted(self):
        """When permissions is None, should default to 'restricted'."""
        mgr = _get_session_mgr()
        session_data = {"permissions": None}
        result = mgr._resolve_permission_mode(session_data)
        self.assertEqual(result, "restricted")

    def test_none_permissions_with_yolo_on(self):
        """When permissions is None but yolo_mode is on, should return elevated."""
        mgr = _get_session_mgr()
        session_data = {"permissions": None, "yolo_mode": "on"}
        result = mgr._resolve_permission_mode(session_data)
        self.assertEqual(result, "elevated")

    def test_missing_permissions_key(self):
        """When permissions key is missing entirely, should default to restricted."""
        mgr = _get_session_mgr()
        session_data = {}
        result = mgr._resolve_permission_mode(session_data)
        self.assertEqual(result, "restricted")

    def test_valid_permissions_dict_elevated(self):
        """When permissions has elevated mode, should return elevated."""
        mgr = _get_session_mgr()
        session_data = {"permissions": {"mode": "elevated"}}
        result = mgr._resolve_permission_mode(session_data)
        self.assertEqual(result, "elevated")

    def test_valid_permissions_dict_sandboxed(self):
        """When permissions has sandboxed mode, should return sandboxed."""
        mgr = _get_session_mgr()
        session_data = {"permissions": {"mode": "sandboxed"}}
        result = mgr._resolve_permission_mode(session_data)
        self.assertEqual(result, "sandboxed")

    def test_prompt_mode_overrides_none_permissions(self):
        """Explicit prompt_mode should override even when permissions is None."""
        mgr = _get_session_mgr()
        session_data = {"permissions": None}
        result = mgr._resolve_permission_mode(session_data, "elevated")
        self.assertEqual(result, "elevated")

    def test_empty_dict_permissions_falls_through(self):
        """Empty dict permissions should fall through to restricted."""
        mgr = _get_session_mgr()
        session_data = {"permissions": {}}
        result = mgr._resolve_permission_mode(session_data)
        self.assertEqual(result, "restricted")


# =========================================================================
# Bug 2+3: run_copilot_sdk accepts mode parameter from dispatcher
# =========================================================================
class TestCopilotSdkModeParameter(unittest.TestCase):
    """run_copilot_sdk must accept mode from dispatcher."""

    def test_copilot_sdk_accepts_mode_param(self):
        """run_copilot_sdk signature should include mode parameter."""
        import inspect

        from agent_manager import SessionManager

        sig = inspect.signature(SessionManager.run_copilot_sdk)
        param_names = list(sig.parameters.keys())
        self.assertIn(
            "mode", param_names, "run_copilot_sdk must accept 'mode' parameter"
        )

    def test_copilot_sdk_mode_default_is_none(self):
        """mode parameter should default to None."""
        import inspect

        from agent_manager import SessionManager

        sig = inspect.signature(SessionManager.run_copilot_sdk)
        mode_param = sig.parameters["mode"]
        self.assertIs(mode_param.default, None)


# =========================================================================
# Bug 4: Background tasks get elevated permissions
# =========================================================================
class TestBackgroundTaskElevatedPermissions(unittest.TestCase):
    """_execute_background_task must set elevated permissions."""

    def test_bg_task_sets_elevated_permissions(self):
        """_execute_background_task should set permissions to elevated."""
        mgr = _get_session_mgr()
        field_updates = {}

        def mock_update(sid, field, value):
            field_updates[field] = value

        mgr.get_or_create_session_data = MagicMock(return_value={})
        mgr.update_session_field = MagicMock(side_effect=mock_update)
        mgr.execute = MagicMock(return_value="test result")
        mgr._bg_task_mgr = MagicMock()
        mgr._bg_task_mgr.get_task.return_value = None

        mgr._execute_background_task(
            "task-123",
            "sess-123",
            "test prompt",
            "orchestrator",
            "copilot",
            "gpt-5",
            "api",
        )

        # Verify permissions were set to elevated
        perm_calls = [
            call
            for call in mgr.update_session_field.call_args_list
            if call[0][1] == "permissions"
        ]
        self.assertTrue(len(perm_calls) > 0, "permissions should be set")
        self.assertEqual(
            perm_calls[0][0][2],
            {"mode": "elevated"},
            "Background tasks must have elevated permissions",
        )

    def test_bg_task_permissions_set_before_execute(self):
        """execute() should be called after permissions are set."""
        mgr = _get_session_mgr()
        call_order = []

        def mock_update(sid, field, value):
            call_order.append(f"set:{field}")

        def mock_execute(prompt, sid):
            call_order.append("execute")
            return "ok"

        mgr.get_or_create_session_data = MagicMock(return_value={})
        mgr.update_session_field = MagicMock(side_effect=mock_update)
        mgr.execute = MagicMock(side_effect=mock_execute)
        mgr._bg_task_mgr = MagicMock()
        mgr._bg_task_mgr.get_task.return_value = None

        mgr._execute_background_task(
            "task-456",
            "sess-456",
            "test prompt",
            "orchestrator",
            "copilot-sdk",
            "gpt-5",
            "telegram",
        )

        perm_idx = call_order.index("set:permissions")
        exec_idx = call_order.index("execute")
        self.assertLess(perm_idx, exec_idx, "permissions must be set before execute()")


# =========================================================================
# Bug 1 mirror: /mode current handles None permissions
# =========================================================================
class TestSlashModeWithNonePermissions(unittest.TestCase):
    """_slash_mode must handle None permissions without crashing."""

    def test_mode_current_with_none_permissions(self):
        """'/mode current' should show restricted when permissions is None."""
        mgr = _get_session_mgr()
        session_data = {"permissions": None}
        result = mgr._slash_mode("current", session_data, "test-session")
        self.assertIn("restricted", result.lower())

    def test_mode_current_with_elevated_permissions(self):
        """'/mode current' should show elevated when set."""
        mgr = _get_session_mgr()
        session_data = {"permissions": {"mode": "elevated"}}
        result = mgr._slash_mode("current", session_data, "test-session")
        self.assertIn("elevated", result.lower())


# =========================================================================
# Bug 3: _dispatch_single_runtime passes mode to SDK runtimes
# =========================================================================
class TestDispatchPassesModeToSdkRuntimes(unittest.TestCase):
    """_dispatch_single_runtime must pass mode to both SDK runtimes."""

    def _make_dispatch_mgr(self):
        """Create a SessionManager with mocks for dispatch testing."""
        mgr = _get_session_mgr()
        mgr.touch_session = MagicMock()
        mgr.session_exists = MagicMock(return_value=False)
        mgr.get_most_recent_session_id = MagicMock(return_value=None)
        mgr.update_session_field = MagicMock()
        mgr.sanitize_telegram_html = MagicMock(side_effect=lambda x: x)
        return mgr

    def test_dispatch_passes_mode_to_copilot_sdk(self):
        """copilot-sdk dispatch call should include mode argument."""
        mgr = self._make_dispatch_mgr()
        mgr.run_copilot_sdk = MagicMock(return_value="sdk output")

        # Signature: _dispatch_single_runtime(runtime, prompt, model, agent,
        #     session_id, can_resume, n8n_session_id, effective_timeout,
        #     render_type, mode)
        mgr._dispatch_single_runtime(
            "copilot-sdk",
            "test prompt",
            "gpt-5",
            "orchestrator",
            None,
            False,
            "n8n-sess-1",
            300,
            "text",
            "elevated",
        )

        call_args = mgr.run_copilot_sdk.call_args
        self.assertIsNotNone(call_args, "run_copilot_sdk should be called")
        all_args = call_args[0]  # positional args
        self.assertIn(
            "elevated", all_args, "mode='elevated' must be passed to run_copilot_sdk"
        )

    def test_dispatch_passes_mode_to_claude_sdk(self):
        """claude-sdk dispatch call should include mode argument."""
        mgr = self._make_dispatch_mgr()
        mgr.run_claude_sdk = MagicMock(return_value="claude output")

        mgr._dispatch_single_runtime(
            "claude-sdk",
            "test prompt",
            "claude-sonnet-4.6",
            "orchestrator",
            None,
            False,
            "n8n-sess-2",
            300,
            "text",
            "elevated",
        )

        call_args = mgr.run_claude_sdk.call_args
        self.assertIsNotNone(call_args, "run_claude_sdk should be called")
        all_args = call_args[0]
        self.assertIn(
            "elevated", all_args, "mode='elevated' must be passed to run_claude_sdk"
        )

    def test_dispatch_restricted_mode_to_copilot_sdk(self):
        """copilot-sdk should receive restricted mode when set."""
        mgr = self._make_dispatch_mgr()
        mgr.run_copilot_sdk = MagicMock(return_value="output")

        mgr._dispatch_single_runtime(
            "copilot-sdk",
            "test prompt",
            "gpt-5",
            "orchestrator",
            None,
            False,
            "n8n-sess-3",
            300,
            "text",
            "restricted",
        )

        call_args = mgr.run_copilot_sdk.call_args
        all_args = call_args[0]
        self.assertIn(
            "restricted",
            all_args,
            "mode='restricted' must be passed to run_copilot_sdk",
        )


if __name__ == "__main__":
    unittest.main()
