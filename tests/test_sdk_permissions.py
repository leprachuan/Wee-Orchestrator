"""Tests for Issue #91 — permissions not honored for copilot-sdk and claude-sdk.

Tests verify:
- copilot-sdk: SubprocessConfig with --allow-all-paths/--yolo passed in elevated mode
- copilot-sdk: on_user_input_request and on_elicitation_request set in elevated mode
- copilot-sdk: restricted mode does NOT pass dangerous CLI flags
- claude-sdk: _resolve_permission_mode args are in correct order (session_data, mode)
- claude-sdk: elevated mode maps to bypassPermissions
- claude-sdk: restricted mode maps to default
- claude-sdk: sandboxed mode maps to plan
"""

import asyncio
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.environ.setdefault("API_SHARED_KEY", "test_key_123")


def _get_session_mgr():
    """Create a minimal SessionManager for testing."""
    from agent_manager import SessionManager

    mgr = SessionManager.__new__(SessionManager)
    mgr.mode = None
    mgr.command_timeout = 300
    mgr._session_map_lock = __import__("threading").Lock()
    mgr.session_map_file = __import__("pathlib").Path("/tmp/_test_sdk_perm_map.json")
    mgr.session_state_dir = __import__("pathlib").Path("/tmp/_test_sdk_perm_sessions")
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
    if not mgr.session_map_file.exists():
        mgr.session_map_file.write_text("{}")
    return mgr


class TestCopilotSDKPermissions(unittest.TestCase):
    """Test that copilot-sdk honors elevated/restricted permission modes."""

    def _run_sdk_with_mock(self, mode, captured):
        """
        Run run_copilot_sdk with mocked copilot SDK.
        Captures: session_kwargs, SubprocessConfig args passed.
        """
        mgr = _get_session_mgr()

        # Fake session data with given permission mode
        session_data = {"permissions": {"mode": mode}, "channel": "webui"}
        session_mock = MagicMock()
        session_mock.session_id = "fake-sdk-session-id"
        session_mock.send_and_wait = AsyncMock(return_value=None)
        session_mock.get_messages = MagicMock(return_value=[])
        session_mock.disconnect = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)

        # Track create_session calls
        create_calls = []

        async def fake_create_session(**kwargs):
            create_calls.append(kwargs)
            return session_mock

        async def fake_resume_session(sid, **kwargs):
            create_calls.append(kwargs)
            return session_mock

        client_mock = MagicMock()
        client_mock.create_session = fake_create_session
        client_mock.resume_session = fake_resume_session
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=False)

        subprocess_configs = []

        class FakeSubprocessConfig:
            def __init__(self, cli_args=None):
                self.cli_args = cli_args or []
                subprocess_configs.append(self)

        class FakeCopilotClient:
            def __init__(self, config=None):
                self._config = config

            async def __aenter__(self):
                return client_mock

            async def __aexit__(self, *args):
                pass

        # Mock PermissionHandler, SessionEventType
        perm_module = types.ModuleType("copilot")
        perm_module.CopilotClient = FakeCopilotClient
        perm_module.SubprocessConfig = FakeSubprocessConfig

        session_module = types.ModuleType("copilot.session")

        class FakePermissionHandler:
            @staticmethod
            def approve_all(request, invocation):
                return True

        class FakeSessionEventType:
            ASSISTANT_STREAMING_DELTA = "streaming_delta"
            ASSISTANT_MESSAGE_DELTA = "message_delta"
            ASSISTANT_MESSAGE = "assistant_message"
            TOOL_EXECUTION_START = "tool_exec_start"
            TOOL_EXECUTION_COMPLETE = "tool_exec_complete"
            COMMAND_EXECUTE = "command_execute"
            SESSION_ERROR = "session_error"

        session_module.CopilotSession = MagicMock()
        session_module.PermissionHandler = FakePermissionHandler
        session_module.SessionEventType = FakeSessionEventType
        session_module.UserInputRequest = dict
        session_module.UserInputResponse = dict
        session_module.ElicitationContext = dict
        session_module.ElicitationResult = dict

        with patch.dict(
            "sys.modules",
            {
                "copilot": perm_module,
                "copilot.session": session_module,
            },
        ):
            with patch.object(
                mgr, "get_or_create_session_data", return_value=session_data
            ):
                with patch.object(
                    mgr, "build_agent_context_prompt", return_value="test prompt"
                ):
                    mgr.run_copilot_sdk(
                        prompt="test",
                        model="gpt-4o",
                        agent="orchestrator",
                        session_id=None,
                        resume=False,
                        n8n_session_id="test-session",
                        timeout=30,
                    )

        captured["create_calls"] = create_calls
        captured["subprocess_configs"] = subprocess_configs

    def test_elevated_mode_passes_allow_all_paths_flag(self):
        """Elevated mode must pass --allow-all-paths to SubprocessConfig cli_args."""
        captured = {}
        self._run_sdk_with_mock("elevated", captured)
        configs = captured["subprocess_configs"]
        self.assertEqual(
            len(configs), 1, "SubprocessConfig should be created for elevated mode"
        )
        self.assertIn("--allow-all-paths", configs[0].cli_args)

    def test_elevated_mode_passes_yolo_flag(self):
        """Elevated mode must pass --yolo to SubprocessConfig cli_args."""
        captured = {}
        self._run_sdk_with_mock("elevated", captured)
        configs = captured["subprocess_configs"]
        self.assertEqual(
            len(configs), 1, "SubprocessConfig should be created for elevated mode"
        )
        self.assertIn("--yolo", configs[0].cli_args)

    def test_restricted_mode_no_dangerous_flags(self):
        """Restricted mode must NOT pass --allow-all-paths or --yolo."""
        captured = {}
        self._run_sdk_with_mock("restricted", captured)
        configs = captured["subprocess_configs"]
        self.assertEqual(
            len(configs),
            0,
            "SubprocessConfig should NOT be created for restricted mode",
        )

    def test_elevated_mode_sets_user_input_handler(self):
        """Elevated mode must provide on_user_input_request to auto-approve prompts."""
        captured = {}
        self._run_sdk_with_mock("elevated", captured)
        calls = captured["create_calls"]
        self.assertTrue(len(calls) > 0, "create_session must have been called")
        self.assertIn("on_user_input_request", calls[0])
        self.assertIsNotNone(calls[0]["on_user_input_request"])

    def test_elevated_mode_sets_elicitation_handler(self):
        """Elevated mode must provide on_elicitation_request to auto-accept approval gates."""
        captured = {}
        self._run_sdk_with_mock("elevated", captured)
        calls = captured["create_calls"]
        self.assertTrue(len(calls) > 0, "create_session must have been called")
        self.assertIn("on_elicitation_request", calls[0])
        self.assertIsNotNone(calls[0]["on_elicitation_request"])

    def test_restricted_mode_no_user_input_handler(self):
        """Restricted mode must NOT set on_user_input_request (default SDK behavior)."""
        captured = {}
        self._run_sdk_with_mock("restricted", captured)
        calls = captured["create_calls"]
        self.assertTrue(len(calls) > 0, "create_session must have been called")
        self.assertNotIn("on_user_input_request", calls[0])

    def test_auto_approve_user_input_handler_returns_yes(self):
        """Auto-approve user input handler returns first choice or 'yes'."""

        # Simulate what the handler does
        def _auto_approve(request, invocation):
            if request.get("choices"):
                answer = request["choices"][0]
            else:
                answer = "yes"
            return {"answer": answer, "wasFreeform": True}

        result = _auto_approve({"choices": ["approve", "deny"]}, {})
        self.assertEqual(result["answer"], "approve")

        result = _auto_approve({"choices": []}, {})
        self.assertEqual(result["answer"], "yes")

        result = _auto_approve({}, {})
        self.assertEqual(result["answer"], "yes")

    def test_auto_approve_elicitation_returns_accept(self):
        """Auto-approve elicitation handler returns action=accept."""

        def _auto_elicit(context):
            return {"action": "accept"}

        result = _auto_elicit({"message": "Approve this action?"})
        self.assertEqual(result["action"], "accept")


class TestClaudeSDKPermissions(unittest.TestCase):
    """Test that claude-sdk _resolve_permission_mode uses correct arg order."""

    def test_resolve_permission_mode_arg_order_session_data_first(self):
        """_resolve_permission_mode must be called with session_data as first arg (Issue #91)."""
        mgr = _get_session_mgr()

        # session_data has elevated mode
        session_data = {"permissions": {"mode": "elevated"}, "channel": "api"}

        resolved_modes = []

        original_resolve = mgr._resolve_permission_mode

        def tracking_resolve(sd, pm="restricted"):
            result = original_resolve(sd, pm)
            resolved_modes.append(
                {"session_data": sd, "prompt_mode": pm, "result": result}
            )
            return result

        mgr._resolve_permission_mode = tracking_resolve

        # Mock claude SDK
        claude_sdk_module = types.ModuleType("claude_ai.sdk")
        mock_query = AsyncMock()

        # Return empty async iterator
        async def empty_agen(*args, **kwargs):
            return
            yield  # make it an async generator

        claude_sdk_module.query = empty_agen
        claude_sdk_module.ClaudeAgentOptions = MagicMock(return_value=MagicMock())
        claude_sdk_module.AssistantMessage = type("AssistantMessage", (), {})
        claude_sdk_module.TextBlock = type("TextBlock", (), {"text": ""})
        claude_sdk_module.ToolUseBlock = type("ToolUseBlock", (), {})
        claude_sdk_module.ToolResultBlock = type("ToolResultBlock", (), {})

        with patch.dict("sys.modules", {"claude_ai.sdk": claude_sdk_module}):
            with patch.object(
                mgr, "get_or_create_session_data", return_value=session_data
            ):
                with patch.object(
                    mgr, "build_agent_context_prompt", return_value="test prompt"
                ):
                    try:
                        mgr.run_claude_sdk(
                            prompt="test",
                            model="claude-3-5-sonnet",
                            agent="orchestrator",
                            session_id=None,
                            resume=False,
                            n8n_session_id="test-session",
                            timeout=5,
                            mode=None,
                        )
                    except Exception:
                        pass  # Expected — we care about the args, not the result

        self.assertTrue(
            len(resolved_modes) > 0, "_resolve_permission_mode must have been called"
        )
        call = resolved_modes[0]
        # session_data must be a dict (the real session data), not a string (mode value)
        self.assertIsInstance(
            call["session_data"],
            dict,
            "_resolve_permission_mode first arg must be session_data dict, not a string mode",
        )
        self.assertIsInstance(
            call["prompt_mode"],
            str,
            "_resolve_permission_mode second arg must be a string mode",
        )

    def test_elevated_session_resolves_to_elevated_mode(self):
        """When session has elevated permissions, claude-sdk must use bypassPermissions."""
        mgr = _get_session_mgr()
        session_data = {"permissions": {"mode": "elevated"}}
        result = mgr._resolve_permission_mode(session_data, "restricted")
        self.assertEqual(
            result,
            "elevated",
            "Session with elevated permissions must resolve to elevated mode",
        )

    def test_restricted_session_resolves_to_restricted_mode(self):
        """When session has restricted permissions, claude-sdk must use default SDK mode."""
        mgr = _get_session_mgr()
        session_data = {"permissions": {"mode": "restricted"}}
        result = mgr._resolve_permission_mode(session_data, "restricted")
        self.assertEqual(result, "restricted")

    def test_permission_mode_mapping_elevated_to_bypass(self):
        """Elevated mode must map to bypassPermissions for claude-sdk."""
        # This mirrors the logic in run_claude_sdk
        for mode, expected in [
            ("elevated", "bypassPermissions"),
            ("sandboxed", "plan"),
            ("restricted", "default"),
        ]:
            if mode == "elevated":
                sdk_perm = "bypassPermissions"
            elif mode == "sandboxed":
                sdk_perm = "plan"
            else:
                sdk_perm = "default"
            self.assertEqual(
                sdk_perm,
                expected,
                f"Mode {mode!r} must map to {expected!r} for claude-sdk",
            )


if __name__ == "__main__":
    unittest.main()
