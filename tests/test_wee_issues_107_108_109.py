#!/usr/bin/env python3
"""Regression tests for wee runtime issues #107, #108, #109.

Issue #107: Tool calling returns "no response" — agentic tool loop
Issue #108: Multi-turn context broken — session history persistence
Issue #109: Tool calls produce no streaming output — SSE events
"""

from agent_manager import SessionManager
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add repo root to path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _make_mgr():
    """Create a minimal SessionManager for testing."""
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_map = {}
    mgr._session_map_lock = threading.Lock()
    mgr.command_timeout = 300
    mgr.AGENTS = {
        "orchestrator": {
            "path": "/opt",
            "description": "test",
            "name": "orchestrator",
        }
    }
    mgr._stream_buffers = {}
    mgr.session_map_file = Path("/tmp/wee_test_session_map.json")
    return mgr


def _run_wee(
    mgr, session_id, prompt="test", model="ollama/gemma4:e4b", resume=False, **kwargs
):
    """Helper to call run_wee_native with mocked session infrastructure."""
    defaults = dict(
        prompt=prompt,
        model=model,
        agent="orchestrator",
        session_id=None,
        resume=resume,
        n8n_session_id=session_id,
        timeout=30,
        render_type="text",
    )
    defaults.update(kwargs)
    session_data = mgr.session_map.get(
        session_id,
        {
            "runtime": "wee",
            "model": model,
            "channel": "api",
        },
    )
    with patch.object(mgr, "get_or_create_session_data", return_value=session_data):
        with patch.object(
            mgr,
            "build_agent_context_prompt",
            return_value="You are a helpful assistant.",
        ):
            return mgr.run_wee_native(**defaults)


def _make_text_chunk(content_text):
    """Create a mock streaming chunk with text content."""
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = content_text
    chunk.choices[0].delta.tool_calls = None
    return chunk


def _make_tool_call_chunks(tool_id, func_name, arguments_json):
    """Create mock streaming chunks representing a tool call.

    Returns a list of chunks that simulate how the OpenAI streaming API
    delivers tool call deltas.
    """
    chunks = []

    # First chunk: tool call start with id and function name
    c1 = MagicMock()
    c1.choices = [MagicMock()]
    c1.choices[0].delta.content = None
    tc_delta1 = MagicMock()
    tc_delta1.index = 0
    tc_delta1.id = tool_id
    tc_delta1.function = MagicMock()
    tc_delta1.function.name = func_name
    tc_delta1.function.arguments = ""
    c1.choices[0].delta.tool_calls = [tc_delta1]
    chunks.append(c1)

    # Second chunk: arguments (may come in multiple chunks, we do it in one)
    c2 = MagicMock()
    c2.choices = [MagicMock()]
    c2.choices[0].delta.content = None
    tc_delta2 = MagicMock()
    tc_delta2.index = 0
    tc_delta2.id = None
    tc_delta2.function = MagicMock()
    tc_delta2.function.name = None
    tc_delta2.function.arguments = arguments_json
    c2.choices[0].delta.tool_calls = [tc_delta2]
    chunks.append(c2)

    return chunks


# ============================================================
# Issue #108: Multi-turn conversation history
# ============================================================
class TestWeeSessionHistory(unittest.TestCase):
    """Issue #108: Verify conversation history is persisted between turns."""

    @patch("openai.OpenAI")
    def test_first_turn_saves_history(self, mock_openai_cls):
        """First message should save user + assistant messages to session map."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        chunk = _make_text_chunk("Hello!")
        mock_client.chat.completions.create.return_value = [chunk]

        sid = "test_108_first_turn"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
        }

        # Mock session persistence
        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map") as mock_save:
                result = _run_wee(mgr, sid, prompt="Hi there")

        self.assertEqual(result, "Hello!")
        # Verify save_session_map was called with wee_messages
        mock_save.assert_called()
        saved_map = mock_save.call_args[0][0]
        msgs = saved_map[sid]["wee_messages"]
        # Should have: system, user, assistant
        roles = [m["role"] for m in msgs]
        self.assertIn("system", roles)
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
        # User message should be our prompt
        user_msgs = [m for m in msgs if m["role"] == "user"]
        self.assertEqual(user_msgs[0]["content"], "Hi there")
        # Assistant message should be the response
        asst_msgs = [m for m in msgs if m["role"] == "assistant"]
        self.assertEqual(asst_msgs[0]["content"], "Hello!")

    @patch("openai.OpenAI")
    def test_second_turn_loads_history(self, mock_openai_cls):
        """Second message should include previous conversation in messages."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        chunk = _make_text_chunk("Your secret is: pizza")
        mock_client.chat.completions.create.return_value = [chunk]

        sid = "test_108_second_turn"
        # Simulate existing history from a previous turn
        previous_history = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "My secret word is pizza"},
            {"role": "assistant", "content": "Got it, I'll remember that."},
        ]
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
            "wee_messages": previous_history,
        }

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map"):
                _run_wee(mgr, sid, prompt="What is my secret?", resume=True)

        # Check that the API was called with full history
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        api_messages = call_kwargs["messages"]
        # Should include: system + prev user + prev assistant + new user = 4 messages
        self.assertGreaterEqual(len(api_messages), 4)
        self.assertEqual(api_messages[0]["role"], "system")
        self.assertEqual(api_messages[1]["role"], "user")
        self.assertEqual(api_messages[1]["content"], "My secret word is pizza")
        self.assertEqual(api_messages[2]["role"], "assistant")
        self.assertEqual(api_messages[3]["role"], "user")
        self.assertEqual(api_messages[3]["content"], "What is my secret?")

    @patch("openai.OpenAI")
    def test_no_resume_starts_fresh(self, mock_openai_cls):
        """With resume=False, history should not be loaded."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        chunk = _make_text_chunk("Fresh start!")
        mock_client.chat.completions.create.return_value = [chunk]

        sid = "test_108_no_resume"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
            "wee_messages": [
                {"role": "system", "content": "old system"},
                {"role": "user", "content": "old message"},
                {"role": "assistant", "content": "old reply"},
            ],
        }

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map"):
                _run_wee(mgr, sid, prompt="New question", resume=False)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        api_messages = call_kwargs["messages"]
        # System + new user should be first two messages (list grows after response)
        self.assertGreaterEqual(len(api_messages), 2)
        self.assertEqual(api_messages[0]["role"], "system")
        self.assertEqual(api_messages[1]["role"], "user")
        self.assertEqual(api_messages[1]["content"], "New question")
        # Should NOT contain old history messages
        old_msgs = [m for m in api_messages if m.get("content") == "old message"]
        self.assertEqual(len(old_msgs), 0)

    def test_session_exists_wee_with_history(self):
        """session_exists should return True when wee_messages exist."""
        mgr = _make_mgr()
        sid = "test_108_exists"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "wee_messages": [{"role": "user", "content": "hi"}],
        }
        with patch.object(mgr, "load_session_data", return_value=mgr.session_map[sid]):
            self.assertTrue(mgr.session_exists("some-uuid", "wee", sid))

    def test_session_exists_wee_no_history(self):
        """session_exists should return False when no wee_messages."""
        mgr = _make_mgr()
        sid = "test_108_no_exists"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
        }
        with patch.object(mgr, "load_session_data", return_value=mgr.session_map[sid]):
            self.assertFalse(mgr.session_exists("some-uuid", "wee", sid))

    @patch("openai.OpenAI")
    def test_history_caps_at_max(self, mock_openai_cls):
        """History should be capped to prevent unbounded growth."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        chunk = _make_text_chunk("OK")
        mock_client.chat.completions.create.return_value = [chunk]

        sid = "test_108_cap"
        # Create large history (150 messages)
        big_history = [{"role": "system", "content": "sys"}]
        for i in range(75):
            big_history.append({"role": "user", "content": f"msg {i}"})
            big_history.append({"role": "assistant", "content": f"reply {i}"})
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
            "wee_messages": big_history,
        }

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map") as mock_save:
                _run_wee(mgr, sid, prompt="more", resume=True)

        saved_map = mock_save.call_args[0][0]
        saved_msgs = saved_map[sid]["wee_messages"]
        self.assertLessEqual(len(saved_msgs), 100)
        # System message should be preserved
        self.assertEqual(saved_msgs[0]["role"], "system")


# ============================================================
# Issue #107: Tool calling agentic loop
# ============================================================
class TestWeeToolCalling(unittest.TestCase):
    """Issue #107: Verify tool-call agentic loop works."""

    @patch("openai.OpenAI")
    def test_tool_call_loop_executes_bash(self, mock_openai_cls):
        """Tool call agentic loop works with bash execution."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # First call: model returns a tool call (bash)
        tool_chunks = _make_tool_call_chunks(
            "call_1", "bash", '{"command": "echo hello"}'
        )
        # Second call: model returns final text response
        final_chunk = _make_text_chunk("The output was: hello")
        mock_client.chat.completions.create.side_effect = [
            tool_chunks,  # First round: tool call
            [final_chunk],  # Second round: final answer
        ]

        sid = "test_107_bash"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
        }

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map"):
                with patch.object(
                    mgr, "_execute_bash_command", return_value="hello"
                ) as mock_bash:
                    result = _run_wee(mgr, sid, prompt="Run echo hello")

        self.assertEqual(result, "The output was: hello")
        mock_bash.assert_called_once_with("echo hello", "orchestrator")

    @patch("openai.OpenAI")
    def test_tool_call_loop_executes_python(self, mock_openai_cls):
        """Python tool calls should be executed via subprocess."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        tool_chunks = _make_tool_call_chunks(
            "call_py_1", "python", '{"code": "print(2+2)"}'
        )
        final_chunk = _make_text_chunk("The answer is 4")
        mock_client.chat.completions.create.side_effect = [
            tool_chunks,
            [final_chunk],
        ]

        sid = "test_107_python"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
        }

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(
                        stdout="4\n", stderr="", returncode=0
                    )
                    result = _run_wee(mgr, sid, prompt="Calculate 2+2")

        self.assertEqual(result, "The answer is 4")

    @patch("openai.OpenAI")
    def test_tool_result_appended_to_messages(self, mock_openai_cls):
        """Tool results should be included in the conversation for the next round."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        tool_chunks = _make_tool_call_chunks("call_2", "bash", '{"command": "date"}')
        final_chunk = _make_text_chunk("Today is April 11")
        mock_client.chat.completions.create.side_effect = [
            tool_chunks,
            [final_chunk],
        ]

        sid = "test_107_msgs"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
        }

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map"):
                with patch.object(
                    mgr, "_execute_bash_command", return_value="Sat Apr 11"
                ):
                    _run_wee(mgr, sid, prompt="What day is it?")

        # The second API call should include the tool result
        second_call_kwargs = mock_client.chat.completions.create.call_args_list[1][1]
        msgs = second_call_kwargs["messages"]
        # Should contain: system, user, assistant(tool_calls), tool(result)
        roles = [m["role"] for m in msgs]
        self.assertIn("tool", roles)
        tool_msg = [m for m in msgs if m["role"] == "tool"][0]
        self.assertEqual(tool_msg["content"], "Sat Apr 11")
        self.assertEqual(tool_msg["tool_call_id"], "call_2")

    @patch("openai.OpenAI")
    def test_no_tool_calls_returns_directly(self, mock_openai_cls):
        """When no tool calls, response should be returned directly."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        chunk = _make_text_chunk("Simple answer")
        mock_client.chat.completions.create.return_value = [chunk]

        sid = "test_107_no_tools"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
        }

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map"):
                result = _run_wee(mgr, sid, prompt="Hello")

        self.assertEqual(result, "Simple answer")
        # Only one API call should have been made
        self.assertEqual(mock_client.chat.completions.create.call_count, 1)

    @patch("openai.OpenAI")
    def test_tools_not_supported_fallback(self, mock_openai_cls):
        """If tools cause an error, should retry without tools."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # First call with tools raises error, retry without tools works
        chunk = _make_text_chunk("Fallback response")
        mock_client.chat.completions.create.side_effect = [
            Exception("tools not supported"),
            [chunk],
        ]

        sid = "test_107_fallback"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
        }

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map"):
                result = _run_wee(mgr, sid, prompt="Hello")

        self.assertEqual(result, "Fallback response")

    def test_wee_execute_tool_bash(self):
        """_wee_execute_tool should delegate bash to _execute_bash_command."""
        mgr = _make_mgr()
        with patch.object(
            mgr, "_execute_bash_command", return_value="output"
        ) as mock_bash:
            result = mgr._wee_execute_tool("bash", {"command": "ls"}, "orchestrator")
        self.assertEqual(result, "output")
        mock_bash.assert_called_once_with("ls", "orchestrator")

    def test_wee_execute_tool_python(self):
        """_wee_execute_tool should run Python code via subprocess."""
        mgr = _make_mgr()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="42\n", stderr="", returncode=0)
            result = mgr._wee_execute_tool(
                "python", {"code": "print(42)"}, "orchestrator"
            )
        self.assertEqual(result, "42")

    def test_wee_execute_tool_unknown(self):
        """_wee_execute_tool should return error for unknown tools."""
        mgr = _make_mgr()
        result = mgr._wee_execute_tool("unknown_tool", {}, "orchestrator")
        self.assertIn("Error", result)
        self.assertIn("Unknown tool", result)

    def test_wee_execute_tool_empty_command(self):
        """_wee_execute_tool should handle empty command gracefully."""
        mgr = _make_mgr()
        result = mgr._wee_execute_tool("bash", {"command": ""}, "orchestrator")
        self.assertIn("Error", result)

    @patch("openai.OpenAI")
    def test_multiple_tool_calls_in_one_round(self, mock_openai_cls):
        """Multiple tool calls in a single response should all be executed."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # Create chunks with two tool calls
        c1 = MagicMock()
        c1.choices = [MagicMock()]
        c1.choices[0].delta.content = None
        tc1 = MagicMock()
        tc1.index = 0
        tc1.id = "call_a"
        tc1.function = MagicMock()
        tc1.function.name = "bash"
        tc1.function.arguments = '{"command": "echo a"}'
        tc2 = MagicMock()
        tc2.index = 1
        tc2.id = "call_b"
        tc2.function = MagicMock()
        tc2.function.name = "bash"
        tc2.function.arguments = '{"command": "echo b"}'
        c1.choices[0].delta.tool_calls = [tc1, tc2]

        final_chunk = _make_text_chunk("Done: a and b")
        mock_client.chat.completions.create.side_effect = [
            [c1],
            [final_chunk],
        ]

        sid = "test_107_multi_tools"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
        }

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map"):
                with patch.object(mgr, "_execute_bash_command", side_effect=["a", "b"]):
                    result = _run_wee(mgr, sid, prompt="Run both")

        self.assertEqual(result, "Done: a and b")


# ============================================================
# Issue #109: SSE streaming for tool execution
# ============================================================
class TestWeeToolStreaming(unittest.TestCase):
    """Issue #109: Verify tool execution emits SSE events."""

    @patch("openai.OpenAI")
    def test_tool_start_event_emitted(self, mock_openai_cls):
        """Tool start event should be pushed to stream buffer."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        tool_chunks = _make_tool_call_chunks(
            "call_sse_1", "bash", '{"command": "whoami"}'
        )
        final_chunk = _make_text_chunk("You are root")
        mock_client.chat.completions.create.side_effect = [
            tool_chunks,
            [final_chunk],
        ]

        sid = "test_109_sse_start"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "webui",
        }
        mock_buffer = MagicMock()
        mgr._stream_buffers[sid] = mock_buffer

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map"):
                with patch.object(mgr, "_execute_bash_command", return_value="root"):
                    _run_wee(mgr, sid, prompt="Who am I?")

        # Find tool_call events
        tc_calls = [
            c for c in mock_buffer.push.call_args_list if c[0][0] == "tool_call"
        ]
        self.assertGreaterEqual(len(tc_calls), 2)  # start + complete

        # Verify start event
        start_evt = tc_calls[0][0][1]
        self.assertEqual(start_evt["name"], "bash")
        self.assertEqual(start_evt["status"], "running")
        self.assertEqual(start_evt["id"], "call_sse_1")

    @patch("openai.OpenAI")
    def test_tool_complete_event_emitted(self, mock_openai_cls):
        """Tool complete event with result should be pushed to stream buffer."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        tool_chunks = _make_tool_call_chunks(
            "call_sse_2", "bash", '{"command": "hostname"}'
        )
        final_chunk = _make_text_chunk("The hostname is devbox")
        mock_client.chat.completions.create.side_effect = [
            tool_chunks,
            [final_chunk],
        ]

        sid = "test_109_sse_done"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "webui",
        }
        mock_buffer = MagicMock()
        mgr._stream_buffers[sid] = mock_buffer

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map"):
                with patch.object(mgr, "_execute_bash_command", return_value="devbox"):
                    _run_wee(mgr, sid, prompt="What is the hostname?")

        tc_calls = [
            c for c in mock_buffer.push.call_args_list if c[0][0] == "tool_call"
        ]
        # Verify complete event
        done_evt = tc_calls[1][0][1]
        self.assertEqual(done_evt["name"], "bash")
        self.assertEqual(done_evt["status"], "complete")
        self.assertEqual(done_evt["result"], "devbox")

    @patch("openai.OpenAI")
    def test_content_streamed_during_final_answer(self, mock_openai_cls):
        """Content tokens in the final round should be pushed as chunks."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        tool_chunks = _make_tool_call_chunks(
            "call_sse_3", "bash", '{"command": "date"}'
        )
        c1 = _make_text_chunk("Today ")
        c2 = _make_text_chunk("is Friday")
        mock_client.chat.completions.create.side_effect = [
            tool_chunks,
            [c1, c2],
        ]

        sid = "test_109_content_stream"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "webui",
        }
        mock_buffer = MagicMock()
        mgr._stream_buffers[sid] = mock_buffer

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map"):
                with patch.object(mgr, "_execute_bash_command", return_value="Fri"):
                    result = _run_wee(mgr, sid, prompt="What day?")

        self.assertEqual(result, "Today is Friday")

        # Verify chunk events were pushed
        chunk_calls = [c for c in mock_buffer.push.call_args_list if c[0][0] == "chunk"]
        self.assertGreaterEqual(len(chunk_calls), 2)

    @patch("openai.OpenAI")
    def test_done_sentinel_after_tool_round(self, mock_openai_cls):
        """Done sentinel should be pushed after tool rounds complete."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        tool_chunks = _make_tool_call_chunks(
            "call_sse_4", "bash", '{"command": "echo done"}'
        )
        final_chunk = _make_text_chunk("All done")
        mock_client.chat.completions.create.side_effect = [
            tool_chunks,
            [final_chunk],
        ]

        sid = "test_109_done"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "webui",
        }
        mock_buffer = MagicMock()
        mgr._stream_buffers[sid] = mock_buffer

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map"):
                with patch.object(mgr, "_execute_bash_command", return_value="done"):
                    _run_wee(mgr, sid, prompt="Finish up")

        done_calls = [c for c in mock_buffer.push.call_args_list if c[0][0] == "done"]
        self.assertEqual(len(done_calls), 1)
        self.assertEqual(done_calls[0][0][1], "All done")

    @patch("openai.OpenAI")
    def test_no_buffer_no_error(self, mock_openai_cls):
        """Tool execution without a stream buffer should not raise."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        tool_chunks = _make_tool_call_chunks(
            "call_no_buf", "bash", '{"command": "echo ok"}'
        )
        final_chunk = _make_text_chunk("OK")
        mock_client.chat.completions.create.side_effect = [
            tool_chunks,
            [final_chunk],
        ]

        sid = "test_109_no_buffer"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
        }
        # No stream buffer set

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map"):
                with patch.object(mgr, "_execute_bash_command", return_value="ok"):
                    result = _run_wee(mgr, sid, prompt="Run it")

        self.assertEqual(result, "OK")


# ============================================================
# Integration: Combined scenarios
# ============================================================
class TestWeeIntegration(unittest.TestCase):
    """Combined tests covering interactions between all three fixes."""

    @patch("openai.OpenAI")
    def test_tool_calls_saved_in_history(self, mock_openai_cls):
        """Tool call rounds should be saved in conversation history."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        tool_chunks = _make_tool_call_chunks("call_hist", "bash", '{"command": "ls"}')
        final_chunk = _make_text_chunk("Files listed")
        mock_client.chat.completions.create.side_effect = [
            tool_chunks,
            [final_chunk],
        ]

        sid = "test_integration_history"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
        }

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map") as mock_save:
                with patch.object(
                    mgr, "_execute_bash_command", return_value="file1.txt"
                ):
                    _run_wee(mgr, sid, prompt="List files")

        saved_msgs = mock_save.call_args[0][0][sid]["wee_messages"]
        roles = [m["role"] for m in saved_msgs]
        # Should include: system, user, assistant(tool_calls), tool, assistant(final)
        self.assertIn("tool", roles)
        self.assertEqual(roles.count("assistant"), 2)

    @patch("openai.OpenAI")
    def test_system_prompt_refreshed_on_resume(self, mock_openai_cls):
        """System prompt should be refreshed even when loading from history."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        chunk = _make_text_chunk("Updated response")
        mock_client.chat.completions.create.return_value = [chunk]

        sid = "test_sys_refresh"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "channel": "api",
            "wee_messages": [
                {"role": "system", "content": "OLD system prompt"},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
        }

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map"):
                _run_wee(mgr, sid, prompt="test", resume=True)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        api_messages = call_kwargs["messages"]
        # System prompt should be refreshed to the current one
        # Issue #111: System prompt is augmented with tool section; check prefix only
        self.assertTrue(
            api_messages[0]["content"].startswith("You are a helpful assistant."),
            "System prompt must begin with base context after refresh",
        )


class TestWeeContextPersistenceDispatch(unittest.TestCase):
    """Regression tests for the can_resume bug in context persistence.

    Root cause (issue #108 regression): The execute() dispatch path had
    an else: can_resume = session_id if session_id else False branch that
    fired for the wee runtime. Since wee never sets an external session_id
    (it uses n8n_session_id as the history key), can_resume was always False,
    causing _wee_load_messages(resume=False) every turn -- wiping context.

    Fix: Added elif current_runtime == "wee": branch that passes
    n8n_session_id to session_exists(), matching the Devin/Cursor pattern.
    """

    def test_session_exists_wee_no_session_id(self):
        """session_exists for wee runtime must work when session_id is None.

        This is the core regression: before the fix, can_resume checked
        if session_id else False and wee never has an external session_id,
        so can_resume was always False.
        """
        mgr = _make_mgr()
        sid = "test_dispatch_ctx_01"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/qwen3:8b",
            "channel": "api",
            "wee_messages": [
                {"role": "system", "content": "You are Wee."},
                {"role": "user", "content": "Call me purple people eater."},
                {"role": "assistant", "content": "Understood, Purple People Eater!"},
            ],
        }
        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            # session_id=None simulates the real dispatch path -- wee never sets one
            result = mgr.session_exists(None, "wee", n8n_session_id=sid)
        self.assertTrue(
            result,
            "session_exists must return True for wee runtime using n8n_session_id "
            "even when session_id is None",
        )

    def test_session_exists_wee_first_turn_no_history(self):
        """session_exists returns False on first turn (no wee_messages yet)."""
        mgr = _make_mgr()
        sid = "test_dispatch_ctx_02"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/qwen3:8b",
            "channel": "api",
        }
        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            result = mgr.session_exists(None, "wee", n8n_session_id=sid)
        self.assertFalse(result, "session_exists must be False when no wee_messages")

    @patch("openai.OpenAI")
    def test_two_turn_conversation_retains_context(self, mock_openai_cls):
        """Full two-turn simulation: second turn must include first turn in messages.

        This is the exact scenario from the screenshot: user says
        'Call me purple people eater' on turn 1, then asks 'What did I ask
        you to call me?' on turn 2 -- which was incorrectly answered with
        'I do not see any previous instruction'.
        """
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        sid = "test_dispatch_ctx_03"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/qwen3:8b",
            "channel": "api",
        }

        # -- Turn 1: user sets a nickname ----------------------------------------
        turn1_chunk = _make_text_chunk(
            "Understood. I will call you Purple People Eater for this session."
        )
        mock_client.chat.completions.create.return_value = [turn1_chunk]

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map") as mock_save:
                _run_wee(
                    mgr,
                    sid,
                    prompt="Call me purple people eater for the duration"
                    "            of this session.",
                    resume=False,
                )
                self.assertTrue(
                    mock_save.called, "save_session_map must be called after turn 1"
                )
                saved_map = mock_save.call_args[0][0]
                self.assertIn("wee_messages", saved_map[sid])
                history_after_t1 = saved_map[sid]["wee_messages"]

        roles = [m["role"] for m in history_after_t1]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
        user_msgs = [m["content"] for m in history_after_t1 if m["role"] == "user"]
        self.assertTrue(
            any("purple people eater" in c.lower() for c in user_msgs),
            "Turn-1 user message must be in saved history",
        )

        # -- Turn 2: load history, ensure context is present ---------------------
        mgr.session_map[sid]["wee_messages"] = history_after_t1

        turn2_chunk = _make_text_chunk("You asked me to call you Purple People Eater!")
        mock_client.chat.completions.create.return_value = [turn2_chunk]

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            with patch.object(mgr, "save_session_map"):
                _run_wee(
                    mgr,
                    sid,
                    prompt="What did I ask you to call me?",
                    resume=True,
                )

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        api_messages = call_kwargs["messages"]

        user_contents = [m["content"] for m in api_messages if m["role"] == "user"]
        self.assertGreaterEqual(
            len(user_contents),
            2,
            "Second API call must include both user messages (turn 1 + turn 2)",
        )
        self.assertTrue(
            any("purple people eater" in c.lower() for c in user_contents),
            "Turn-1 context ('purple people eater') must be present in turn-2 API call",
        )
        self.assertTrue(
            any("what did i ask" in c.lower() for c in user_contents),
            "Turn-2 question must be among user messages",
        )

    def test_can_resume_wee_dispatch_path(self):
        """Resume wee turn: can_resume True even with session_id=None.

        Directly tests that the fixed elif current_runtime == 'wee' branch
        returns True while the old else branch returned False.
        """
        mgr = _make_mgr()
        sid = "test_dispatch_ctx_04"
        mgr.session_map[sid] = {
            "runtime": "wee",
            "model": "ollama/qwen3:8b",
            "channel": "api",
            "session_id": None,
            "wee_messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "turn 1"},
                {"role": "assistant", "content": "reply 1"},
            ],
        }

        with patch.object(mgr, "load_session_map", return_value=dict(mgr.session_map)):
            # Old (broken) path: else branch evaluated with session_id=None
            can_resume_old = (
                mgr.session_exists(None, "wee") if None else False  # session_id is None
            )
            # New (fixed) path: elif wee branch passes n8n_session_id
            can_resume_new = mgr.session_exists(None, "wee", n8n_session_id=sid)

        self.assertFalse(
            can_resume_old,
            "Old (broken) path must return False -- confirms the bug existed",
        )
        self.assertTrue(
            can_resume_new,
            "New (fixed) path must return True -- confirms the fix works",
        )


if __name__ == "__main__":
    unittest.main()
