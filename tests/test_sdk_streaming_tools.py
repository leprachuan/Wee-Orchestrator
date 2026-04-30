"""New tests for Issue #87 — streaming + tool calls for copilot-sdk and claude-sdk.

Tests verify:
- Streaming text chunks pushed to _stream_buffers (copilot-sdk + claude-sdk)
- Tool call events (detected/started/completed) pushed to _stream_buffers
- 'done' sentinel pushed on normal completion and errors
- Graceful behavior when no stream buffer exists
- Tool event structure validation (required fields)
"""

import asyncio
import os
import sys
import time
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.environ.setdefault("API_SHARED_KEY", "test_key_123")


# ─── Shared helpers ───


def _make_stream_buffer():
    """Create a mock stream buffer that records pushes."""
    pushes = []
    buf = MagicMock()
    buf.push = MagicMock(side_effect=lambda kind, data: pushes.append((kind, data)))
    return buf, pushes


def _get_session_mgr():
    """Create a minimal SessionManager for testing (same as existing test pattern)."""
    from agent_manager import SessionManager

    mgr = SessionManager.__new__(SessionManager)
    mgr.mode = None
    mgr.command_timeout = 300
    mgr._session_map_lock = __import__("threading").Lock()
    mgr.session_map_file = __import__("pathlib").Path("/tmp/_test_sdk_stream_map.json")
    mgr.session_state_dir = __import__("pathlib").Path("/tmp/_test_sdk_stream_sessions")
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


# ═══════════════════════════════════════════════════════
#  COPILOT-SDK Streaming + Tool Call Tests
# ═══════════════════════════════════════════════════════


class TestCopilotSdkStreaming(unittest.TestCase):
    """Test copilot-sdk streaming chunks are pushed to stream buffer."""

    @patch("copilot.CopilotClient")
    def test_streaming_delta_pushes_chunks(self, mock_client_cls):
        """Verify on_event streaming deltas push text chunks to stream buffer."""
        mgr = _get_session_mgr()
        stream_buf, pushes = _make_stream_buffer()
        mgr._stream_buffers = {"sess1": stream_buf}

        mock_client = AsyncMock()
        mock_session = AsyncMock()
        mock_session.session_id = "sdk-sess-stream"
        mock_session.disconnect = AsyncMock()
        mock_session.get_messages = MagicMock(return_value=[])

        captured_on_event = None

        async def capture_create_session(**kwargs):
            nonlocal captured_on_event
            captured_on_event = kwargs.get("on_event")
            return mock_session

        mock_client.create_session = AsyncMock(side_effect=capture_create_session)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        # Import the real SessionEventType after patching
        from copilot.session import SessionEventType

        async def send_and_wait_with_events(prompt, timeout=300.0):
            if captured_on_event:
                # Fire streaming delta events
                evt1 = MagicMock()
                evt1.type = SessionEventType.ASSISTANT_STREAMING_DELTA
                evt1.data = "Hello "
                captured_on_event(evt1)

                evt2 = MagicMock()
                evt2.type = SessionEventType.ASSISTANT_STREAMING_DELTA
                evt2.data = "World"
                captured_on_event(evt2)

            result_data = MagicMock()
            result_data.content = "Hello World"
            result_event = MagicMock()
            result_event.data = result_data
            return result_event

        mock_session.send_and_wait = AsyncMock(side_effect=send_and_wait_with_events)

        with patch.object(
            mgr, "build_agent_context_prompt", return_value="test prompt"
        ):
            result = mgr.run_copilot_sdk(
                "test prompt", "gpt-5", "orchestrator", None, False, "sess1"
            )

        chunk_pushes = [(k, d) for k, d in pushes if k == "chunk"]
        self.assertGreaterEqual(len(chunk_pushes), 2)
        self.assertEqual(chunk_pushes[0][1], "Hello ")
        self.assertEqual(chunk_pushes[1][1], "World")

    @patch("copilot.CopilotClient")
    def test_done_sentinel_pushed_on_completion(self, mock_client_cls):
        """Verify 'done' sentinel is pushed when SDK completes."""
        mgr = _get_session_mgr()
        stream_buf, pushes = _make_stream_buffer()
        mgr._stream_buffers = {"sess1": stream_buf}

        mock_client = AsyncMock()
        mock_session = AsyncMock()
        mock_session.session_id = "sdk-sess-done"
        mock_session.disconnect = AsyncMock()
        mock_session.get_messages = MagicMock(return_value=[])

        mock_client.create_session = AsyncMock(return_value=mock_session)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result_data = MagicMock()
        result_data.content = "done result"
        result_event = MagicMock()
        result_event.data = result_data
        mock_session.send_and_wait = AsyncMock(return_value=result_event)

        with patch.object(mgr, "build_agent_context_prompt", return_value="test"):
            result = mgr.run_copilot_sdk(
                "test", "gpt-5", "orchestrator", None, False, "sess1"
            )

        done_pushes = [(k, d) for k, d in pushes if k == "done"]
        self.assertGreaterEqual(len(done_pushes), 1)

    @patch("copilot.CopilotClient")
    def test_no_crash_without_stream_buffer(self, mock_client_cls):
        """Verify function works when no _stream_buffers exists."""
        mgr = _get_session_mgr()
        # No _stream_buffers set — uses getattr fallback

        mock_client = AsyncMock()
        mock_session = AsyncMock()
        mock_session.session_id = "sdk-sess-no-buf"
        mock_session.disconnect = AsyncMock()
        mock_session.get_messages = MagicMock(return_value=[])

        mock_client.create_session = AsyncMock(return_value=mock_session)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result_data = MagicMock()
        result_data.content = "response"
        result_event = MagicMock()
        result_event.data = result_data
        mock_session.send_and_wait = AsyncMock(return_value=result_event)

        with patch.object(mgr, "build_agent_context_prompt", return_value="test"):
            result = mgr.run_copilot_sdk(
                "test", "gpt-5", "orchestrator", None, False, "sess1"
            )

        self.assertIn("response", result)

    @patch("copilot.CopilotClient")
    def test_done_pushed_on_session_error(self, mock_client_cls):
        """Verify 'done' sentinel pushed when session creation fails."""
        mgr = _get_session_mgr()
        stream_buf, pushes = _make_stream_buffer()
        mgr._stream_buffers = {"sess1": stream_buf}

        mock_client = AsyncMock()
        mock_client.create_session = AsyncMock(
            side_effect=ConnectionError("SDK not available")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with patch.object(mgr, "build_agent_context_prompt", return_value="test"):
            result = mgr.run_copilot_sdk(
                "test", "gpt-5", "orchestrator", None, False, "sess1"
            )

        self.assertIn("Error", result)
        done_pushes = [(k, d) for k, d in pushes if k == "done"]
        self.assertGreaterEqual(len(done_pushes), 1)


class TestCopilotSdkToolCalls(unittest.TestCase):
    """Test copilot-sdk tool call event tracking via on_event handler."""

    @patch("copilot.CopilotClient")
    def test_tool_execution_start_pushes_tool_call(self, mock_client_cls):
        """Verify TOOL_EXECUTION_START fires tool_call push with 'started' event."""
        mgr = _get_session_mgr()
        stream_buf, pushes = _make_stream_buffer()
        mgr._stream_buffers = {"sess1": stream_buf}

        mock_client = AsyncMock()
        mock_session = AsyncMock()
        mock_session.session_id = "sdk-tc-1"
        mock_session.disconnect = AsyncMock()
        mock_session.get_messages = MagicMock(return_value=[])

        captured_on_event = None

        async def capture_create_session(**kwargs):
            nonlocal captured_on_event
            captured_on_event = kwargs.get("on_event")
            return mock_session

        mock_client.create_session = AsyncMock(side_effect=capture_create_session)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from copilot.session import SessionEventType

        async def send_with_tool(prompt, timeout=300.0):
            if captured_on_event:
                tool_data = MagicMock()
                tool_data.name = "read_file"
                tool_data.tool_name = "read_file"
                tool_data.input = "/tmp/test.txt"
                tool_data.arguments = "/tmp/test.txt"

                evt = MagicMock()
                evt.type = SessionEventType.TOOL_EXECUTION_START
                evt.data = tool_data
                captured_on_event(evt)

            result_data = MagicMock()
            result_data.content = "done"
            result_event = MagicMock()
            result_event.data = result_data
            return result_event

        mock_session.send_and_wait = AsyncMock(side_effect=send_with_tool)

        with patch.object(mgr, "build_agent_context_prompt", return_value="test"):
            mgr.run_copilot_sdk("test", "gpt-5", "orchestrator", None, False, "sess1")

        tool_pushes = [(k, d) for k, d in pushes if k == "tool_call"]
        self.assertGreaterEqual(len(tool_pushes), 1)
        tc = tool_pushes[0][1]
        self.assertEqual(tc["event"], "started")
        self.assertEqual(tc["name"], "read_file")
        self.assertEqual(tc["runtime"], "copilot-sdk")
        self.assertIn("id", tc)
        self.assertIn("timestamp", tc)

    @patch("copilot.CopilotClient")
    def test_command_execute_pushes_shell_tool_call(self, mock_client_cls):
        """Verify COMMAND_EXECUTE fires tool_call with 'shell' name."""
        mgr = _get_session_mgr()
        stream_buf, pushes = _make_stream_buffer()
        mgr._stream_buffers = {"sess1": stream_buf}

        mock_client = AsyncMock()
        mock_session = AsyncMock()
        mock_session.session_id = "sdk-tc-cmd"
        mock_session.disconnect = AsyncMock()
        mock_session.get_messages = MagicMock(return_value=[])

        captured_on_event = None

        async def capture_create_session(**kwargs):
            nonlocal captured_on_event
            captured_on_event = kwargs.get("on_event")
            return mock_session

        mock_client.create_session = AsyncMock(side_effect=capture_create_session)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from copilot.session import SessionEventType

        async def send_with_cmd(prompt, timeout=300.0):
            if captured_on_event:
                cmd_data = MagicMock()
                cmd_data.command = "ls -la /tmp"
                cmd_data.text = "ls -la /tmp"

                evt = MagicMock()
                evt.type = SessionEventType.COMMAND_EXECUTE
                evt.data = cmd_data
                captured_on_event(evt)

            result_data = MagicMock()
            result_data.content = "done"
            result_event = MagicMock()
            result_event.data = result_data
            return result_event

        mock_session.send_and_wait = AsyncMock(side_effect=send_with_cmd)

        with patch.object(mgr, "build_agent_context_prompt", return_value="test"):
            mgr.run_copilot_sdk("test", "gpt-5", "orchestrator", None, False, "sess1")

        tool_pushes = [(k, d) for k, d in pushes if k == "tool_call"]
        self.assertGreaterEqual(len(tool_pushes), 1)
        tc = tool_pushes[0][1]
        self.assertEqual(tc["event"], "detected")
        self.assertEqual(tc["name"], "shell")
        self.assertIn("ls -la", tc["input"])

    @patch("copilot.CopilotClient")
    def test_tool_start_and_complete_sequence(self, mock_client_cls):
        """Verify TOOL_EXECUTION_START + COMPLETE push started/completed pair."""
        mgr = _get_session_mgr()
        stream_buf, pushes = _make_stream_buffer()
        mgr._stream_buffers = {"sess1": stream_buf}

        mock_client = AsyncMock()
        mock_session = AsyncMock()
        mock_session.session_id = "sdk-tc-seq"
        mock_session.disconnect = AsyncMock()
        mock_session.get_messages = MagicMock(return_value=[])

        captured_on_event = None

        async def capture_create_session(**kwargs):
            nonlocal captured_on_event
            captured_on_event = kwargs.get("on_event")
            return mock_session

        mock_client.create_session = AsyncMock(side_effect=capture_create_session)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from copilot.session import SessionEventType

        async def send_with_tool_lifecycle(prompt, timeout=300.0):
            if captured_on_event:
                start_data = MagicMock()
                start_data.name = "edit_file"
                start_data.tool_name = "edit_file"
                start_data.input = "path=/tmp/x"
                start_data.arguments = "path=/tmp/x"

                start_evt = MagicMock()
                start_evt.type = SessionEventType.TOOL_EXECUTION_START
                start_evt.data = start_data
                captured_on_event(start_evt)

                end_data = MagicMock()
                end_data.name = "edit_file"
                end_data.tool_name = "edit_file"

                end_evt = MagicMock()
                end_evt.type = SessionEventType.TOOL_EXECUTION_COMPLETE
                end_evt.data = end_data
                captured_on_event(end_evt)

            result_data = MagicMock()
            result_data.content = "done"
            result_event = MagicMock()
            result_event.data = result_data
            return result_event

        mock_session.send_and_wait = AsyncMock(side_effect=send_with_tool_lifecycle)

        with patch.object(mgr, "build_agent_context_prompt", return_value="test"):
            mgr.run_copilot_sdk("test", "gpt-5", "orchestrator", None, False, "sess1")

        tool_pushes = [(k, d) for k, d in pushes if k == "tool_call"]
        self.assertEqual(len(tool_pushes), 2)
        self.assertEqual(tool_pushes[0][1]["event"], "started")
        self.assertEqual(tool_pushes[1][1]["event"], "completed")


# ═══════════════════════════════════════════════════════
#  CLAUDE-SDK Streaming + Tool Call Tests
# ═══════════════════════════════════════════════════════

# --- Fake claude_agent_sdk types ---

_TextBlock = type(
    "TextBlock",
    (),
    {
        "__init__": lambda self, text="": setattr(self, "text", text),
    },
)
_AssistantMessage = type(
    "AssistantMessage",
    (),
    {
        "__init__": lambda self, content=None: setattr(self, "content", content or []),
    },
)
_ResultMessage = type(
    "ResultMessage",
    (),
    {
        "__init__": lambda self, session_id="": setattr(self, "session_id", session_id),
    },
)
_ToolUseBlock = type(
    "ToolUseBlock",
    (),
    {
        "__init__": lambda self, id="", name="", input=None: (
            setattr(self, "id", id)
            or setattr(self, "name", name)
            or setattr(self, "input", input)
        ),
    },
)
_ToolResultBlock = type(
    "ToolResultBlock",
    (),
    {
        "__init__": lambda self, tool_use_id="", content=None, is_error=False: (
            setattr(self, "tool_use_id", tool_use_id)
            or setattr(self, "content", content)
            or setattr(self, "is_error", is_error)
        ),
    },
)
_ClaudeAgentOptions = type(
    "ClaudeAgentOptions",
    (),
    {
        "__init__": lambda self, **kw: self.__dict__.update(kw),
    },
)


def _build_fake_claude_module(query_fn=None):
    """Build a fake claude_agent_sdk module with all required exports."""
    mod = types.ModuleType("claude_agent_sdk")

    async def _default_query(prompt, options=None):
        return
        yield

    mod.query = query_fn or _default_query
    mod.ClaudeAgentOptions = _ClaudeAgentOptions
    mod.AssistantMessage = _AssistantMessage
    mod.TextBlock = _TextBlock
    mod.ResultMessage = _ResultMessage
    mod.ToolUseBlock = _ToolUseBlock
    mod.ToolResultBlock = _ToolResultBlock
    return mod


def _make_claude_manager():
    """Create a minimal SessionManager for claude-sdk testing."""
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
    }
    mgr.get_or_create_session_data = MagicMock(
        return_value={
            "channel": "api",
            "runtime": "claude-sdk",
            "model": "haiku",
            "session_id": None,
            "mode": None,
        }
    )
    mgr.build_agent_context_prompt = MagicMock(return_value="[context] test prompt")
    mgr._parse_mode_command = MagicMock(return_value=("test prompt", None))
    mgr._resolve_permission_mode = MagicMock(side_effect=lambda m, s: m)
    mgr.strip_metadata = MagicMock(side_effect=lambda text, rt: text)
    mgr.update_session_field = MagicMock()
    mgr.touch_session = MagicMock()
    return mgr


class TestClaudeSdkStreaming(unittest.TestCase):
    """Test claude-sdk streaming text chunks to stream buffer."""

    def test_text_blocks_pushed_as_chunks(self):
        """Verify TextBlock content is pushed as 'chunk' to stream buffer."""
        mgr = _make_claude_manager()
        stream_buf, pushes = _make_stream_buffer()
        mgr._stream_buffers = {"sess1": stream_buf}

        msg1 = _AssistantMessage([_TextBlock("Hello ")])
        msg2 = _AssistantMessage([_TextBlock("World")])

        async def mock_query(prompt, options=None):
            for m in [msg1, msg2]:
                yield m

        fake_mod = _build_fake_claude_module(mock_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            result = mgr.run_claude_sdk(
                "test", "haiku", "orchestrator", None, False, "sess1"
            )

        chunk_pushes = [(k, d) for k, d in pushes if k == "chunk"]
        self.assertGreaterEqual(len(chunk_pushes), 2)
        self.assertEqual(chunk_pushes[0][1], "Hello ")
        self.assertEqual(chunk_pushes[1][1], "World")

    def test_done_sentinel_on_completion(self):
        """Verify 'done' sentinel pushed after all messages collected."""
        mgr = _make_claude_manager()
        stream_buf, pushes = _make_stream_buffer()
        mgr._stream_buffers = {"sess1": stream_buf}

        msg = _AssistantMessage([_TextBlock("Result")])

        async def mock_query(prompt, options=None):
            yield msg

        fake_mod = _build_fake_claude_module(mock_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            result = mgr.run_claude_sdk(
                "test", "haiku", "orchestrator", None, False, "sess1"
            )

        done_pushes = [(k, d) for k, d in pushes if k == "done"]
        self.assertGreaterEqual(len(done_pushes), 1)
        self.assertIn("Result", done_pushes[0][1])

    def test_done_pushed_on_error(self):
        """Verify 'done' sentinel pushed even when SDK errors."""
        mgr = _make_claude_manager()
        stream_buf, pushes = _make_stream_buffer()
        mgr._stream_buffers = {"sess1": stream_buf}

        async def mock_query(prompt, options=None):
            raise RuntimeError("test error")
            yield  # make it a generator

        fake_mod = _build_fake_claude_module(mock_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            result = mgr.run_claude_sdk(
                "test", "haiku", "orchestrator", None, False, "sess1"
            )

        self.assertIn("Error", result)
        done_pushes = [(k, d) for k, d in pushes if k == "done"]
        self.assertGreaterEqual(len(done_pushes), 1)

    def test_no_crash_without_stream_buffer(self):
        """Verify function works without _stream_buffers attribute."""
        mgr = _make_claude_manager()
        # No _stream_buffers

        msg = _AssistantMessage([_TextBlock("works")])

        async def mock_query(prompt, options=None):
            yield msg

        fake_mod = _build_fake_claude_module(mock_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            result = mgr.run_claude_sdk(
                "test", "haiku", "orchestrator", None, False, "sess1"
            )

        self.assertIn("works", result)


class TestClaudeSdkToolCalls(unittest.TestCase):
    """Test claude-sdk tool call event tracking."""

    def test_tool_use_block_pushes_detected_event(self):
        """Verify ToolUseBlock in content pushes 'detected' tool_call."""
        mgr = _make_claude_manager()
        stream_buf, pushes = _make_stream_buffer()
        mgr._stream_buffers = {"sess1": stream_buf}

        tool_block = _ToolUseBlock(
            id="tu_123", name="read_file", input={"path": "/tmp/x"}
        )
        text_block = _TextBlock("I'll read that file")
        msg = _AssistantMessage([text_block, tool_block])

        async def mock_query(prompt, options=None):
            yield msg

        fake_mod = _build_fake_claude_module(mock_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            result = mgr.run_claude_sdk(
                "test", "haiku", "orchestrator", None, False, "sess1"
            )

        tool_pushes = [(k, d) for k, d in pushes if k == "tool_call"]
        self.assertGreaterEqual(len(tool_pushes), 1)
        tc = tool_pushes[0][1]
        self.assertEqual(tc["event"], "detected")
        self.assertEqual(tc["name"], "read_file")
        self.assertEqual(tc["id"], "tu_123")
        self.assertEqual(tc["runtime"], "claude-sdk")
        self.assertIn("timestamp", tc)

    def test_tool_result_block_pushes_completed_event(self):
        """Verify ToolResultBlock pushes 'completed' tool_call."""
        mgr = _make_claude_manager()
        stream_buf, pushes = _make_stream_buffer()
        mgr._stream_buffers = {"sess1": stream_buf}

        tool_use = _ToolUseBlock(id="tu_456", name="bash", input={"cmd": "ls"})
        tool_result = _ToolResultBlock(
            tool_use_id="tu_456", content="file1.txt\nfile2.txt", is_error=False
        )
        msg1 = _AssistantMessage([tool_use])
        msg2 = _AssistantMessage([tool_result])

        async def mock_query(prompt, options=None):
            yield msg1
            yield msg2

        fake_mod = _build_fake_claude_module(mock_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            result = mgr.run_claude_sdk(
                "test", "haiku", "orchestrator", None, False, "sess1"
            )

        tool_pushes = [(k, d) for k, d in pushes if k == "tool_call"]
        self.assertEqual(len(tool_pushes), 2)
        self.assertEqual(tool_pushes[0][1]["event"], "detected")
        self.assertEqual(tool_pushes[0][1]["name"], "bash")
        self.assertEqual(tool_pushes[1][1]["event"], "completed")
        self.assertEqual(tool_pushes[1][1]["id"], "tu_456")
        self.assertEqual(tool_pushes[1][1]["output"], "file1.txt\nfile2.txt")
        self.assertEqual(tool_pushes[1][1]["status"], "completed")

    def test_tool_result_error_tracked(self):
        """Verify ToolResultBlock with is_error=True has is_error field."""
        mgr = _make_claude_manager()
        stream_buf, pushes = _make_stream_buffer()
        mgr._stream_buffers = {"sess1": stream_buf}

        tool_result = _ToolResultBlock(
            tool_use_id="tu_err", content="Permission denied", is_error=True
        )
        msg = _AssistantMessage([tool_result])

        async def mock_query(prompt, options=None):
            yield msg

        fake_mod = _build_fake_claude_module(mock_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            result = mgr.run_claude_sdk(
                "test", "haiku", "orchestrator", None, False, "sess1"
            )

        tool_pushes = [(k, d) for k, d in pushes if k == "tool_call"]
        self.assertGreaterEqual(len(tool_pushes), 1)
        tc = tool_pushes[0][1]
        self.assertEqual(tc["event"], "completed")
        self.assertTrue(tc["is_error"])
        self.assertEqual(tc["status"], "error")
        self.assertEqual(tc["output"], "Permission denied")

    def test_multiple_tools_tracked_with_unique_ids(self):
        """Verify multiple tool calls get unique IDs."""
        mgr = _make_claude_manager()
        stream_buf, pushes = _make_stream_buffer()
        mgr._stream_buffers = {"sess1": stream_buf}

        tool1 = _ToolUseBlock(id="tu_1", name="read_file", input={"path": "a"})
        tool2 = _ToolUseBlock(id="tu_2", name="write_file", input={"path": "b"})
        text = _TextBlock("Done")
        msg = _AssistantMessage([tool1, tool2, text])

        async def mock_query(prompt, options=None):
            yield msg

        fake_mod = _build_fake_claude_module(mock_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            result = mgr.run_claude_sdk(
                "test", "haiku", "orchestrator", None, False, "sess1"
            )

        tool_pushes = [(k, d) for k, d in pushes if k == "tool_call"]
        self.assertEqual(len(tool_pushes), 2)
        ids = [tp[1]["id"] for tp in tool_pushes]
        self.assertEqual(len(set(ids)), 2)

    def test_result_message_stores_session_id(self):
        """Verify ResultMessage.session_id stored in session data."""
        mgr = _make_claude_manager()

        msg = _AssistantMessage([_TextBlock("hi")])
        result_msg = _ResultMessage(session_id="new-session-abc")

        async def mock_query(prompt, options=None):
            yield msg
            yield result_msg

        fake_mod = _build_fake_claude_module(mock_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            mgr.run_claude_sdk("test", "haiku", "orchestrator", None, False, "sess1")

        mgr.update_session_field.assert_any_call(
            "sess1", "session_id", "new-session-abc"
        )


# ═══════════════════════════════════════════════════════
#  Tool Event Structure Validation
# ═══════════════════════════════════════════════════════


class TestToolEventStructure(unittest.TestCase):
    """Validate required fields in tool call events for both runtimes."""

    REQUIRED_FIELDS = {"event", "id", "name", "input", "runtime", "timestamp"}

    @patch("copilot.CopilotClient")
    def test_copilot_sdk_event_has_required_fields(self, mock_client_cls):
        """All copilot-sdk tool events must have the standard field set."""
        mgr = _get_session_mgr()
        stream_buf, pushes = _make_stream_buffer()
        mgr._stream_buffers = {"sess1": stream_buf}

        mock_client = AsyncMock()
        mock_session = AsyncMock()
        mock_session.session_id = "sdk-structure"
        mock_session.disconnect = AsyncMock()
        mock_session.get_messages = MagicMock(return_value=[])

        captured_on_event = None

        async def capture_create(**kwargs):
            nonlocal captured_on_event
            captured_on_event = kwargs.get("on_event")
            return mock_session

        mock_client.create_session = AsyncMock(side_effect=capture_create)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from copilot.session import SessionEventType

        async def send_with_tool(prompt, timeout=300.0):
            if captured_on_event:
                td = MagicMock()
                td.name = "grep"
                td.tool_name = "grep"
                td.input = "pattern"
                td.arguments = "pattern"
                evt = MagicMock()
                evt.type = SessionEventType.TOOL_EXECUTION_START
                evt.data = td
                captured_on_event(evt)
            rd = MagicMock()
            rd.content = "done"
            re_ = MagicMock()
            re_.data = rd
            return re_

        mock_session.send_and_wait = AsyncMock(side_effect=send_with_tool)

        with patch.object(mgr, "build_agent_context_prompt", return_value="test"):
            mgr.run_copilot_sdk("test", "gpt-5", "orchestrator", None, False, "sess1")

        tool_pushes = [(k, d) for k, d in pushes if k == "tool_call"]
        self.assertTrue(len(tool_pushes) > 0, "Expected at least one tool_call push")
        for _, tc in tool_pushes:
            missing = self.REQUIRED_FIELDS - tc.keys()
            self.assertEqual(missing, set(), f"Missing fields: {missing}")

    def test_claude_sdk_event_has_required_fields(self):
        """All claude-sdk tool events must have the standard field set."""
        mgr = _make_claude_manager()
        stream_buf, pushes = _make_stream_buffer()
        mgr._stream_buffers = {"sess1": stream_buf}

        tool = _ToolUseBlock(id="tu_x", name="bash", input={"cmd": "echo"})
        msg = _AssistantMessage([tool])

        async def mock_query(prompt, options=None):
            yield msg

        fake_mod = _build_fake_claude_module(mock_query)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_mod}):
            mgr.run_claude_sdk("test", "haiku", "orchestrator", None, False, "sess1")

        tool_pushes = [(k, d) for k, d in pushes if k == "tool_call"]
        self.assertTrue(len(tool_pushes) > 0, "Expected at least one tool_call push")
        for _, tc in tool_pushes:
            missing = self.REQUIRED_FIELDS - tc.keys()
            self.assertEqual(missing, set(), f"Missing fields: {missing}")


if __name__ == "__main__":
    unittest.main()
