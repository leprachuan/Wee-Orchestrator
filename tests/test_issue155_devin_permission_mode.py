"""Regression tests for Issue #155: Devin runtime invalid permission mode 'auto'.

Tests verify:
1. run_devin() accepts a `mode` parameter and uses it to set permission_mode
2. "auto" is never passed to Devin CLI (only normal, dangerous, bypass)
3. _dispatch_single_runtime() passes mode to run_devin()
4. Background task dispatch always uses "dangerous" for Devin (non-interactive)
5. Mode propagation chain: scheduler --mode elevated -> run_devin() -> --permission-mode dangerous
"""

import inspect
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")


def _get_bg_task_source():
    """Read _run_background_task source directly from agent_manager.py."""
    with open("/opt/n8n-copilot-shim-dev/agent_manager.py", "r") as f:
        content = f.read()
    start = content.find("def _run_background_task(")
    if start < 0:
        return ""
    line_start = content.rfind("\n", 0, start) + 1
    indent = start - line_start
    pos = start + 1
    while pos < len(content):
        nl = content.find("\n", pos)
        if nl < 0:
            break
        next_line = content[nl + 1 :]
        if next_line and not next_line[0].isspace():
            return content[start:nl]
        if next_line.startswith(" " * indent) and not next_line.startswith(
            " " * (indent + 1)
        ):
            stripped = next_line.strip()
            if stripped.startswith("def ") or stripped.startswith("class "):
                return content[start:nl]
        pos = nl + 1
    return content[start:]


def _strip_comments(source):
    """Remove comment lines from source for clean code-only assertions."""
    lines = source.split("\n")
    return "\n".join(
        line for line in lines if not line.strip().startswith("#")
    )


class TestIssue155DevinPermissionMode(unittest.TestCase):
    """Tests for Root Cause 1: Invalid --permission-mode 'auto'."""

    @classmethod
    def setUpClass(cls):
        from agent_manager import SessionManager

        cls.SessionManager = SessionManager

    def test_run_devin_signature_has_mode_parameter(self):
        """run_devin() must accept a 'mode' keyword parameter."""
        sig = inspect.signature(self.SessionManager.run_devin)
        params = list(sig.parameters.keys())
        self.assertIn("mode", params, "run_devin() must have 'mode' parameter")

    def test_run_devin_mode_default_is_restricted(self):
        """run_devin() 'mode' parameter should default to 'restricted'."""
        sig = inspect.signature(self.SessionManager.run_devin)
        mode_param = sig.parameters["mode"]
        self.assertEqual(mode_param.default, "restricted")

    def test_dispatch_single_runtime_signature_has_mode(self):
        """_dispatch_single_runtime must accept a 'mode' parameter."""
        sig = inspect.signature(self.SessionManager._dispatch_single_runtime)
        self.assertIn("mode", sig.parameters)

    def test_no_auto_in_devin_permission_assignments(self):
        """Ensure 'auto' does not appear as a Devin permission mode assignment."""
        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager.run_devin)
        assignments = re.findall(
            r'^\s*permission_mode\s*=\s*["\'](\w+)["\']',
            source,
            re.MULTILINE,
        )
        for val in assignments:
            self.assertNotEqual(
                val, "auto", "permission_mode must not be 'auto' in run_devin"
            )

    def test_no_auto_fallback_in_run_devin(self):
        """The ternary for devin permission mode must not produce 'auto'."""
        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager.run_devin)
        # Strip comments to avoid false positives
        code_only = _strip_comments(source)
        self.assertNotIn(
            'else "auto"',
            code_only,
            "run_devin must not use 'auto' as fallback permission mode",
        )


class TestIssue155ModePropagation(unittest.TestCase):
    """Tests for Root Cause 2: mode not propagated to run_devin()."""

    @classmethod
    def setUpClass(cls):
        from agent_manager import SessionManager

        cls.SessionManager = SessionManager

    def _make_manager(self):
        mgr = self.SessionManager.__new__(self.SessionManager)
        mgr.session_map = {}
        mgr._session_map_lock = __import__("asyncio").Lock()
        mgr.AGENTS = {"orchestrator": {"path": "/opt"}}
        mgr.command_timeout = 300
        mgr.devin_bin = "/usr/bin/devin"
        mgr.devin_home = Path("/tmp/devin-test")
        mgr.devin_session_dir = Path("/tmp/devin-test/sessions")
        mgr._stream_queues = {}
        return mgr

    def _run_devin_with_mode(self, mode_arg, mock_parse_mode_result="restricted"):
        """Helper: call run_devin with given mode and capture the Popen cmd."""
        mgr = self._make_manager()

        with (
            patch.object(
                mgr,
                "_parse_mode_command",
                return_value=("test prompt", mock_parse_mode_result),
            ),
            patch.object(
                mgr,
                "get_or_create_session_data",
                return_value={"channel": "api"},
            ),
            patch.object(
                mgr, "_resolve_permission_mode", return_value="restricted"
            ),
            patch.object(
                mgr, "_get_devin_session_id", return_value=None
            ),
            patch.object(mgr, "build_agent_context_prompt", return_value="ctx"),
            patch.object(
                mgr,
                "_execute_subprocess_with_tracking",
                return_value="done",
            ) as mock_exec,
            patch.object(mgr, "update_session_field"),
            patch.object(mgr, "strip_metadata", side_effect=lambda t, r: t),
        ):
            kwargs = {
                "prompt": "test prompt",
                "model": "model",
                "agent": "orchestrator",
                "session_id": None,
                "resume": False,
                "n8n_session_id": "n8n-123",
                "timeout": 300,
                "render_type": "text",
            }
            if mode_arg is not None:
                kwargs["mode"] = mode_arg

            mgr.run_devin(**kwargs)

            # Extract the cmd arg from _execute_subprocess_with_tracking call
            call_args = mock_exec.call_args
            # First positional arg is cmd
            cmd = call_args[0][0] if call_args[0] else call_args[1].get("cmd", [])
            return cmd

    def test_dispatch_passes_mode_to_run_devin(self):
        """_dispatch_single_runtime must pass mode arg when calling run_devin."""
        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager._dispatch_single_runtime)
        devin_block = source[source.find('runtime == "devin"') :]
        devin_call_end = devin_block.find("elif runtime")
        if devin_call_end < 0:
            devin_call_end = len(devin_block)
        devin_call = devin_block[:devin_call_end]
        self.assertIn(
            "mode",
            devin_call,
            "_dispatch_single_runtime must pass mode to run_devin()",
        )

    def test_run_devin_elevated_maps_to_dangerous(self):
        """When mode='elevated' is passed, permission_mode must be 'dangerous'."""
        cmd = self._run_devin_with_mode("elevated")
        perm_idx = cmd.index("--permission-mode")
        actual_perm = cmd[perm_idx + 1]
        self.assertEqual(
            actual_perm,
            "dangerous",
            "Expected 'dangerous' for elevated mode, got " + repr(actual_perm),
        )

    def test_run_devin_restricted_maps_to_normal(self):
        """When mode='restricted', permission_mode must be 'normal' (not 'auto')."""
        cmd = self._run_devin_with_mode("restricted")
        perm_idx = cmd.index("--permission-mode")
        actual_perm = cmd[perm_idx + 1]
        self.assertEqual(
            actual_perm,
            "normal",
            "Expected 'normal' for restricted mode, got " + repr(actual_perm),
        )

    def test_run_devin_explicit_mode_not_overridden_by_session(self):
        """When mode='elevated' is explicitly passed, session data must NOT override it."""
        cmd = self._run_devin_with_mode("elevated")
        perm_idx = cmd.index("--permission-mode")
        actual_perm = cmd[perm_idx + 1]
        self.assertEqual(
            actual_perm,
            "dangerous",
            "Explicit elevated mode must not be overridden by session data",
        )

    def test_run_devin_prompt_mode_elevated_when_no_explicit_mode(self):
        """When mode=restricted (default) but prompt has /mode elevated, use elevated."""
        cmd = self._run_devin_with_mode(None, mock_parse_mode_result="elevated")
        perm_idx = cmd.index("--permission-mode")
        actual_perm = cmd[perm_idx + 1]
        self.assertEqual(
            actual_perm,
            "dangerous",
            "/mode elevated in prompt should map to dangerous",
        )

    def test_run_devin_sandboxed_maps_to_normal(self):
        """When mode='sandboxed', permission_mode must be 'normal'."""
        cmd = self._run_devin_with_mode("sandboxed")
        perm_idx = cmd.index("--permission-mode")
        actual_perm = cmd[perm_idx + 1]
        self.assertEqual(
            actual_perm,
            "normal",
            "Expected 'normal' for sandboxed mode, got " + repr(actual_perm),
        )

    def test_run_devin_default_mode_falls_back_to_session(self):
        """When no explicit mode and no /mode in prompt, session data is consulted."""
        mgr = self._make_manager()

        with (
            patch.object(
                mgr,
                "_parse_mode_command",
                return_value=("test prompt", "restricted"),
            ),
            patch.object(
                mgr,
                "get_or_create_session_data",
                return_value={
                    "channel": "api",
                    "permissions": {"mode": "elevated"},
                },
            ),
            patch.object(
                mgr, "_resolve_permission_mode", return_value="elevated"
            ),
            patch.object(
                mgr, "_get_devin_session_id", return_value=None
            ),
            patch.object(mgr, "build_agent_context_prompt", return_value="ctx"),
            patch.object(
                mgr,
                "_execute_subprocess_with_tracking",
                return_value="done",
            ) as mock_exec,
            patch.object(mgr, "update_session_field"),
            patch.object(mgr, "strip_metadata", side_effect=lambda t, r: t),
        ):
            mgr.run_devin(
                "test prompt",
                "model",
                "orchestrator",
                None,
                False,
                "n8n-123",
                300,
                "text",
            )

            call_args = mock_exec.call_args
            cmd = call_args[0][0] if call_args[0] else call_args[1].get("cmd", [])
            perm_idx = cmd.index("--permission-mode")
            actual_perm = cmd[perm_idx + 1]
            self.assertEqual(
                actual_perm,
                "dangerous",
                "Session elevated should map to dangerous when no explicit mode",
            )


class TestIssue155BackgroundTaskDevin(unittest.TestCase):
    """Tests for background task Devin dispatch always using 'dangerous'.

    _run_background_task is a nested function inside agent_manager.py and
    cannot be directly imported, so we read the source from file.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = _get_bg_task_source()
        assert "runtime ==" in cls.source, (
            "_run_background_task source not found"
        )

    def _get_devin_section(self):
        devin_idx = self.source.find('runtime == "devin"')
        self.assertGreater(devin_idx, 0, "Devin dispatch block must exist")
        devin_section = self.source[devin_idx:]
        next_elif = devin_section.find("elif runtime")
        if next_elif > 0:
            devin_section = devin_section[:next_elif]
        return devin_section

    def test_background_task_source_no_auto_in_code(self):
        """_run_background_task code must not use 'auto' for Devin permission mode."""
        devin_section = self._get_devin_section()
        # Strip comments to avoid false positive from "(NOT 'auto')" in comment
        code_only = _strip_comments(devin_section)
        self.assertNotIn(
            '"auto"',
            code_only,
            "_run_background_task Devin code must not use 'auto'",
        )

    def test_background_task_devin_always_dangerous(self):
        """Background task Devin dispatch should always use 'dangerous'."""
        devin_section = self._get_devin_section()
        self.assertIn(
            '_devin_perm = "dangerous"',
            devin_section,
            "Background task Devin must always use 'dangerous' permission",
        )

    def test_background_task_devin_no_conditional_perm(self):
        """Background Devin perm should NOT be conditional (was ternary before)."""
        devin_section = self._get_devin_section()
        code_only = _strip_comments(devin_section)
        ternary = re.findall(
            r"_devin_perm\s*=.*\bif\b.*\belse\b", code_only
        )
        self.assertEqual(
            len(ternary),
            0,
            "Background task Devin perm should not use conditional",
        )


class TestIssue155ValidPermissionValues(unittest.TestCase):
    """Verify only valid Devin CLI permission values are used."""

    VALID_DEVIN_PERMS = {"normal", "dangerous", "bypass"}

    def test_run_devin_only_valid_perms(self):
        """run_devin must only produce valid Devin CLI permission modes."""
        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager.run_devin)
        strings_in_perm = re.findall(
            r'permission_mode\s*=\s*"(\w+)"(?:\s+if\b.*\belse\s+"(\w+)")?',
            source,
        )
        for match in strings_in_perm:
            for val in match:
                if val:
                    self.assertIn(
                        val,
                        self.VALID_DEVIN_PERMS,
                        "Invalid Devin CLI permission mode: "
                        + repr(val)
                        + " (valid: "
                        + repr(self.VALID_DEVIN_PERMS)
                        + ")",
                    )

    def test_background_task_devin_valid_perms(self):
        """Background task must only use valid Devin CLI permission modes."""
        source = _get_bg_task_source()
        devin_idx = source.find('runtime == "devin"')
        devin_section = source[devin_idx:]
        next_elif = devin_section.find("elif runtime")
        if next_elif > 0:
            devin_section = devin_section[:next_elif]
        perms = re.findall(r'_devin_perm\s*=\s*"(\w+)"', devin_section)
        for val in perms:
            self.assertIn(
                val,
                self.VALID_DEVIN_PERMS,
                "Invalid Devin CLI permission in bg task: " + repr(val),
            )


if __name__ == "__main__":
    unittest.main()
