"""Tests for Claude Agent SDK runtime integration (Issue #77).

Tests cover:
- Import handling (missing package)
- Mode parsing (elevated, sandboxed, restricted)
- Permission mode mapping (bypassPermissions, plan, default)
- Session resumption
- Streaming message collection
- Error handling (CLINotFound, CLIConnection, ProcessError, generic)
- Runtime registration across all integration points
- Dispatch routing
"""

import asyncio
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.environ.setdefault("API_SHARED_KEY", "test_key_123")


# --- Fake claude_agent_sdk module ---

_TextBlock = type("TextBlock", (), {
    "__init__": lambda self, text="": setattr(self, "text", text),
})
_AssistantMessage = type("AssistantMessage", (), {
    "__init__": lambda self, content=None: setattr(self, "content", content or []),
})
_ResultMessage = type("ResultMessage", (), {
    "__init__": lambda self, session_id="": setattr(self, "session_id", session_id),
})

_ClaudeAgentOptions = type("ClaudeAgentOptions", (), {
    "__init__": lambda self, **kw: self.__dict__.update(kw),
})


def _build_fake_module(query_fn=None):
    """Build a fake claude_agent_sdk module."""
    mod = types.ModuleType("claude_agent_sdk")

    async def _default_query(prompt, options=None):
        return
        yield

    mod.query = query_fn or _default_query
    mod.ClaudeAgentOptions = _ClaudeAgentOptions
    mod.AssistantMessage = _AssistantMessage
    mod.TextBlock = _TextBlock
    mod.ResultMessage = _ResultMessage
    return mod


def _make_manager():
    """Create a minimal SessionManager for testing."""
    from agent_manager import SessionManager
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_state_dir = MagicMock()
    mgr.session_map_file = MagicMock()
    mgr.session_map = {}
    mgr._session_map_lock = MagicMock()
    mgr.command_timeout = 300
    mgr.mode = None
    mgr.claude_bin = "/usr/bin/claude"
    mgr.AGENTS = {
        "orchestrator": {"path": "/opt/n8n-copilot-shim-dev", "name": "orchestrator"},
        "wee-dev": {"path": "/opt/wee-dev", "name": "wee-dev"},
    }
    mgr.get_or_create_session_data = MagicMock(return_value={
        "channel": "api",
        "runtime": "claude-agent-sdk",
        "model": "haiku",
        "session_id": "test-session-id",
        "mode": None,
    })
    mgr.build_agent_context_prompt = MagicMock(return_value="[context] test prompt")
    mgr._parse_mode_command = MagicMock(return_value=("test prompt", None))
    mgr._resolve_permission_mode = MagicMock(side_effect=lambda m, s: m)
    mgr.strip_metadata = MagicMock(side_effect=lambda text, rt: text)
    mgr.update_session_field = MagicMock()
    mgr.touch_session = MagicMock()
    return mgr


class TestImportError(unittest.TestCase):
    """Test import handling when claude-agent-sdk is not installed."""

    def test_import_error_returns_install_message(self):
        mgr = _make_manager()
        # Remove any cached module and make import fail
        saved = sys.modules.pop("claude_agent_sdk", None)
        try:
            with patch.dict("sys.modules", {"claude_agent_sdk": None}):
                result = mgr.run_claude_agent_sdk(
                    "test", "haiku", "orchestrator", None, False, "sess1"
                )
                self.assertIn("claude-agent-sdk not installed", result)
                self.assertIn("pip install claude-agent-sdk", result)
        finally:
            if saved:
                sys.modules["claude_agent_sdk"] = saved


class TestPermissionModeMapping(unittest.TestCase):
    """Test permission mode mapping."""

    def _run_and_capture(self, mode):
        mgr = _make_manager()
        mgr._resolve_permission_mode = MagicMock(return_value=mode)

        captured = {}

        async def capturing_query(prompt, options=None):
            captured["permission_mode"] = options.permission_mode
            captured["prompt"] = prompt
            return
            yield

        fake_mod = _build_fake_module(capturing_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            result = mgr.run_claude_agent_sdk(
                "test prompt", "haiku", "orchestrator", None, False, "sess1",
                mode=mode,
            )
        return captured, result

    def test_elevated_maps_to_bypass_permissions(self):
        captured, _ = self._run_and_capture("elevated")
        self.assertEqual(captured["permission_mode"], "bypassPermissions")

    def test_sandboxed_maps_to_plan(self):
        captured, _ = self._run_and_capture("sandboxed")
        self.assertEqual(captured["permission_mode"], "plan")

    def test_restricted_maps_to_default(self):
        captured, _ = self._run_and_capture("restricted")
        self.assertEqual(captured["permission_mode"], "default")

    def test_elevated_adds_mode_instruction(self):
        captured, _ = self._run_and_capture("elevated")
        self.assertIn("ELEVATED MODE", captured["prompt"])

    def test_sandboxed_adds_mode_instruction(self):
        captured, _ = self._run_and_capture("sandboxed")
        self.assertIn("SANDBOXED MODE", captured["prompt"])


class TestStreamingCollection(unittest.TestCase):
    """Test streaming message collection."""

    def test_collects_text_blocks(self):
        mgr = _make_manager()

        msg1 = _AssistantMessage([_TextBlock("Hello "), _TextBlock("World")])
        msg2 = _AssistantMessage([_TextBlock("!")])

        async def mock_query(prompt, options=None):
            for m in [msg1, msg2]:
                yield m

        fake_mod = _build_fake_module(mock_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            result = mgr.run_claude_agent_sdk(
                "test", "haiku", "orchestrator", None, False, "sess1"
            )

        self.assertIn("Hello ", result)
        self.assertIn("World", result)
        self.assertIn("!", result)

    def test_empty_response_returns_error(self):
        mgr = _make_manager()

        async def mock_query(prompt, options=None):
            return
            yield

        fake_mod = _build_fake_module(mock_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            result = mgr.run_claude_agent_sdk(
                "test", "haiku", "orchestrator", None, False, "sess1"
            )

        self.assertIn("No response received", result)

    def test_fallback_content_extraction(self):
        """Test fallback for non-AssistantMessage types with content attr."""
        mgr = _make_manager()

        # Create a message type that isn't AssistantMessage but has content
        OtherMsg = type("OtherMessage", (), {
            "__init__": lambda self, content=None: setattr(self, "content", content or []),
        })
        msg = OtherMsg([_TextBlock("fallback text")])

        async def mock_query(prompt, options=None):
            yield msg

        fake_mod = _build_fake_module(mock_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            result = mgr.run_claude_agent_sdk(
                "test", "haiku", "orchestrator", None, False, "sess1"
            )

        self.assertIn("fallback text", result)


class TestErrorHandling(unittest.TestCase):
    """Test error handling for various SDK exceptions."""

    def _run_with_error(self, error_class_name, error_msg="test error"):
        mgr = _make_manager()

        error_cls = type(error_class_name, (Exception,), {})

        async def error_query(prompt, options=None):
            raise error_cls(error_msg)
            yield  # noqa: unreachable — needed for async generator

        fake_mod = _build_fake_module(error_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            return mgr.run_claude_agent_sdk(
                "test", "haiku", "orchestrator", None, False, "sess1"
            )

    def test_cli_not_found_error(self):
        result = self._run_with_error("CLINotFoundError")
        self.assertIn("Claude Code CLI not found", result)
        self.assertIn("pip install --force-reinstall", result)

    def test_cli_connection_error(self):
        result = self._run_with_error("CLIConnectionError")
        self.assertIn("Not authenticated", result)
        self.assertIn("claude login", result)

    def test_auth_keyword_in_message(self):
        result = self._run_with_error("SomeError", "authentication required")
        self.assertIn("Not authenticated", result)

    def test_process_error(self):
        result = self._run_with_error("ProcessError")
        self.assertIn("process error", result)

    def test_generic_error(self):
        result = self._run_with_error("RandomError", "something broke")
        self.assertIn("Claude Agent SDK", result)
        self.assertIn("RandomError", result)


class TestSessionResumption(unittest.TestCase):
    """Test session resumption behavior."""

    def test_resume_sets_session_id(self):
        mgr = _make_manager()
        captured = {}

        async def cap_query(prompt, options=None):
            captured["session_id"] = getattr(options, "session_id", None)
            captured["prompt"] = prompt
            return
            yield

        fake_mod = _build_fake_module(cap_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            mgr.run_claude_agent_sdk(
                "test", "haiku", "orchestrator",
                "existing-sess-123", True, "sess1",
            )

        self.assertEqual(captured["session_id"], "existing-sess-123")
        # Resuming should use raw prompt
        self.assertEqual(captured["prompt"], "test prompt")

    def test_no_resume_uses_full_context(self):
        mgr = _make_manager()
        captured = {}

        async def cap_query(prompt, options=None):
            captured["session_id"] = getattr(options, "session_id", None)
            captured["prompt"] = prompt
            return
            yield

        fake_mod = _build_fake_module(cap_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            mgr.run_claude_agent_sdk(
                "test", "haiku", "orchestrator",
                None, False, "sess1",
            )

        self.assertIsNone(captured["session_id"])
        self.assertEqual(captured["prompt"], "[context] test prompt")

    def test_model_passed_to_options(self):
        mgr = _make_manager()
        captured = {}

        async def cap_query(prompt, options=None):
            captured["model"] = getattr(options, "model", None)
            return
            yield

        fake_mod = _build_fake_module(cap_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            mgr.run_claude_agent_sdk(
                "test", "opus", "orchestrator", None, False, "sess1",
            )

        self.assertEqual(captured["model"], "opus")


    def test_result_message_stores_session_id(self):
        """ResultMessage with session_id must call update_session_field."""
        mgr = _make_manager()
        result_msg = _ResultMessage(session_id="new-sdk-session-abc")
        text_msg = _AssistantMessage([_TextBlock("response text")])

        async def mock_query(prompt, options=None):
            yield text_msg
            yield result_msg

        fake_mod = _build_fake_module(mock_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            result = mgr.run_claude_agent_sdk(
                "test", "haiku", "orchestrator", None, False, "sess1"
            )

        mgr.update_session_field.assert_called_once_with(
            "sess1", "session_id", "new-sdk-session-abc"
        )
        self.assertIn("response text", result)

class TestRuntimeRegistration(unittest.TestCase):
    """Test that claude-agent-sdk is registered in all integration points."""

    def test_slash_runtime_list(self):
        mgr = _make_manager()
        result = mgr._slash_runtime("list", {}, "test-session")
        self.assertIn("claude-agent-sdk", result)
        self.assertIn("in-process tools", result)

    def test_slash_runtime_set_accepted(self):
        mgr = _make_manager()
        result = mgr._slash_runtime(
            "set claude-agent-sdk",
            {"runtime": "copilot", "session_id": "old-sess"},
            "test-session",
        )
        self.assertNotIn("Unknown runtime", str(result))

    def test_session_validation_source(self):
        """Verify source code includes claude-agent-sdk in session validation."""
        import inspect
        from agent_manager import SessionManager
        source = inspect.getsource(SessionManager._get_or_create_session_data_unlocked)
        self.assertIn("claude-agent-sdk", source)


class TestDispatch(unittest.TestCase):
    """Test dispatch routing."""

    def test_dispatch_routes_correctly(self):
        mgr = _make_manager()
        mgr.run_claude_agent_sdk = MagicMock(return_value="SDK response")

        from agent_manager import SessionManager
        result = SessionManager._dispatch_single_runtime(
            mgr,
            runtime="claude-agent-sdk",
            prompt="test",
            model="haiku",
            agent="orchestrator",
            session_id="sess-123",
            can_resume=True,
            n8n_session_id="n8n-123",
            effective_timeout=300,
            render_type="text",
            mode="restricted",
        )

        mgr.run_claude_agent_sdk.assert_called_once()
        self.assertEqual(result, "SDK response")

    def test_dispatch_passes_mode(self):
        mgr = _make_manager()
        mgr.run_claude_agent_sdk = MagicMock(return_value="resp")

        from agent_manager import SessionManager
        SessionManager._dispatch_single_runtime(
            mgr,
            runtime="claude-agent-sdk",
            prompt="test",
            model="haiku",
            agent="orchestrator",
            session_id=None,
            can_resume=False,
            n8n_session_id="n8n-123",
            effective_timeout=300,
            render_type="text",
            mode="elevated",
        )

        args, kwargs = mgr.run_claude_agent_sdk.call_args
        # The mode should appear in positional or keyword args
        all_args = str(args) + str(kwargs)
        self.assertIn("elevated", all_args)

    def test_other_runtimes_unaffected(self):
        """Ensure adding claude-agent-sdk doesn't break other runtime dispatch."""
        mgr = _make_manager()
        mgr.run_claude = MagicMock(return_value="claude response")

        from agent_manager import SessionManager
        result = SessionManager._dispatch_single_runtime(
            mgr,
            runtime="claude",
            prompt="test",
            model="haiku",
            agent="orchestrator",
            session_id=None,
            can_resume=False,
            n8n_session_id="n8n-123",
            effective_timeout=300,
            render_type="text",
            mode="restricted",
        )

        mgr.run_claude.assert_called_once()
        self.assertEqual(result, "claude response")


if __name__ == "__main__":
    unittest.main()
