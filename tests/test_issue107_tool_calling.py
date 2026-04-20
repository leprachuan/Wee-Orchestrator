"""Regression tests for Issue #107: wee runtime tool calling.

Tests the full tool-calling agentic loop:
1. Correct Ollama port (11434)
2. Tool call detection from streaming deltas
3. Tool execution (bash, python)
4. Follow-up request with tool results
5. Final response extraction
6. Max-rounds safety net
7. wee_runtime.py standalone tool calling
"""

import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class MockDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class MockToolCallDelta:
    def __init__(self, index=0, tc_id=None, name=None, arguments=None):
        self.index = index
        self.id = tc_id
        self.function = MagicMock()
        self.function.name = name
        self.function.arguments = arguments


class MockChoice:
    def __init__(self, delta):
        self.delta = delta


class MockChunk:
    def __init__(self, choices):
        self.choices = choices


def make_text_stream(text):
    chunks = []
    for char in text:
        chunks.append(MockChunk([MockChoice(MockDelta(content=char))]))
    return iter(chunks)


def make_tool_call_stream(tool_calls):
    chunks = []
    for idx, (tc_id, name, args_str) in enumerate(tool_calls):
        tc_delta = MockToolCallDelta(index=idx, tc_id=tc_id, name=name, arguments="")
        chunks.append(MockChunk([MockChoice(MockDelta(tool_calls=[tc_delta]))]))
        tc_delta2 = MockToolCallDelta(index=idx, arguments=args_str)
        tc_delta2.id = None
        tc_delta2.function.name = None
        chunks.append(MockChunk([MockChoice(MockDelta(tool_calls=[tc_delta2]))]))
    return iter(chunks)


def _create_test_manager():
    from agent_manager import SessionManager

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
    mgr.session_map_file = Path("/tmp/wee_test_session_map_107.json")
    mgr.session_map_ttl = 30 * 86400
    return mgr


class TestOllamaPortCorrectness(unittest.TestCase):

    def test_agent_manager_presets_port(self):
        with open(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "agent_manager.py")
        ) as f:
            content = f.read()
        self.assertIn('"http://192.168.1.101:11434/v1"', content)
        self.assertNotIn("11436", content)

    def test_wee_runtime_presets_port(self):
        with open(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "wee_runtime.py")
        ) as f:
            content = f.read()
        self.assertIn('"http://192.168.1.101:11434/v1"', content)
        self.assertNotIn("11436", content)


class TestToolCallDetection(unittest.TestCase):

    def test_single_tool_call_detected(self):
        tool_calls_acc = {}
        tc_counter = 0
        stream = make_tool_call_stream([("call_1", "bash", '{"command": "date"}')])
        for chunk in stream:
            delta = chunk.choices[0].delta
            if getattr(delta, "tool_calls", None):
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_acc:
                        tc_counter += 1
                        tool_calls_acc[idx] = {
                            "id": getattr(tc_delta, "id", None) or f"tc_{tc_counter}",
                            "name": "",
                            "arguments": "",
                        }
                    if tc_delta.id:
                        tool_calls_acc[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_acc[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_acc[idx][
                                "arguments"
                            ] += tc_delta.function.arguments
        self.assertEqual(len(tool_calls_acc), 1)
        self.assertEqual(tool_calls_acc[0]["name"], "bash")
        self.assertEqual(tool_calls_acc[0]["arguments"], '{"command": "date"}')

    def test_multiple_tool_calls(self):
        tool_calls_acc = {}
        tc_counter = 0
        stream = make_tool_call_stream(
            [
                ("c1", "bash", '{"command": "date"}'),
                ("c2", "python", '{"code": "print(42)"}'),
            ]
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            if getattr(delta, "tool_calls", None):
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_acc:
                        tc_counter += 1
                        tool_calls_acc[idx] = {
                            "id": getattr(tc_delta, "id", None) or f"tc_{tc_counter}",
                            "name": "",
                            "arguments": "",
                        }
                    if tc_delta.id:
                        tool_calls_acc[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_acc[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_acc[idx][
                                "arguments"
                            ] += tc_delta.function.arguments
        self.assertEqual(len(tool_calls_acc), 2)
        self.assertEqual(tool_calls_acc[0]["name"], "bash")
        self.assertEqual(tool_calls_acc[1]["name"], "python")

    def test_no_tool_calls_returns_content(self):
        stream = make_text_stream("Hello!")
        parts = []
        tca = {}
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                parts.append(delta.content)
            if getattr(delta, "tool_calls", None):
                for tc_delta in delta.tool_calls:
                    tca[tc_delta.index] = True
        self.assertEqual("".join(parts), "Hello!")
        self.assertEqual(len(tca), 0)


class TestToolExecution(unittest.TestCase):

    def test_bash_tool(self):
        from wee_runtime import execute_tool

        self.assertEqual(execute_tool("bash", {"command": "echo hello"}), "hello")

    def test_python_tool(self):
        from wee_runtime import execute_tool

        self.assertEqual(execute_tool("python", {"code": "print(2 + 2)"}), "4")

    def test_unknown_tool(self):
        from wee_runtime import execute_tool

        self.assertIn("Error: Unknown tool", execute_tool("unknown", {}))

    def test_empty_command(self):
        from wee_runtime import execute_tool

        self.assertIn("Error: No command", execute_tool("bash", {"command": ""}))

    def test_empty_code(self):
        from wee_runtime import execute_tool

        self.assertIn("Error: No code", execute_tool("python", {"code": ""}))

    def test_bash_stderr(self):
        from wee_runtime import execute_tool

        result = execute_tool("bash", {"command": "echo err >&2 && exit 1"})
        self.assertIn("STDERR", result)


class TestAgenticLoop(unittest.TestCase):

    @patch("openai.OpenAI")
    def test_tool_call_then_text(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = [
            iter(list(make_tool_call_stream([("c1", "bash", '{"command": "date"}')]))),
            iter(list(make_text_stream("Today is Friday"))),
        ]
        mgr = _create_test_manager()
        with (
            patch.object(
                mgr, "get_or_create_session_data", return_value={"channel": "api"}
            ),
            patch.object(mgr, "build_agent_context_prompt", return_value="ctx"),
            patch.object(mgr, "_wee_execute_tool", return_value="Fri Jul 11"),
            patch.object(
                mgr,
                "_wee_load_messages",
                return_value=[{"role": "system", "content": "ctx"}],
            ),
            patch.object(mgr, "_wee_save_messages"),
        ):
            result = mgr.run_wee_native(
                model="qwen3:8b",
                prompt="What day?",
                agent="orchestrator",
                session_id=None,
                resume=True,
                n8n_session_id="t107_1",
            )
        self.assertEqual(result, "Today is Friday")
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)

    @patch("openai.OpenAI")
    def test_no_tool_calls_direct(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            list(make_text_stream("Simple"))
        )
        mgr = _create_test_manager()
        with (
            patch.object(
                mgr, "get_or_create_session_data", return_value={"channel": "api"}
            ),
            patch.object(mgr, "build_agent_context_prompt", return_value=""),
            patch.object(mgr, "_wee_load_messages", return_value=[]),
            patch.object(mgr, "_wee_save_messages"),
        ):
            result = mgr.run_wee_native(
                model="qwen3:8b",
                prompt="Hi",
                agent="orchestrator",
                session_id=None,
                resume=True,
                n8n_session_id="t107_2",
            )
        self.assertEqual(result, "Simple")
        self.assertEqual(mock_client.chat.completions.create.call_count, 1)

    @patch("openai.OpenAI")
    def test_tool_result_message_format(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = [
            iter(
                list(
                    make_tool_call_stream(
                        [("call_42", "bash", '{"command": "whoami"}')]
                    )
                )
            ),
            iter(list(make_text_stream("root"))),
        ]
        mgr = _create_test_manager()
        saved = []

        def capture(sid, msgs):
            saved.extend(msgs)

        with (
            patch.object(
                mgr, "get_or_create_session_data", return_value={"channel": "api"}
            ),
            patch.object(mgr, "build_agent_context_prompt", return_value=""),
            patch.object(mgr, "_wee_execute_tool", return_value="root"),
            patch.object(mgr, "_wee_load_messages", return_value=[]),
            patch.object(mgr, "_wee_save_messages", side_effect=capture),
        ):
            mgr.run_wee_native(
                model="qwen3:8b",
                prompt="whoami",
                agent="orchestrator",
                session_id=None,
                resume=True,
                n8n_session_id="t107_3",
            )
        tool_msgs = [m for m in saved if m.get("role") == "tool"]
        self.assertTrue(len(tool_msgs) >= 1)
        self.assertEqual(tool_msgs[0]["tool_call_id"], "call_42")
        self.assertEqual(tool_msgs[0]["content"], "root")

    @patch("openai.OpenAI")
    def test_multiple_tools_one_round(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = [
            iter(
                list(
                    make_tool_call_stream(
                        [
                            ("ca", "bash", '{"command": "date"}'),
                            ("cb", "python", '{"code": "print(42)"}'),
                        ]
                    )
                )
            ),
            iter(list(make_text_stream("Done"))),
        ]
        mgr = _create_test_manager()
        calls = []

        def track(fn, fa, ag):
            calls.append(fn)
            return "r"

        with (
            patch.object(
                mgr, "get_or_create_session_data", return_value={"channel": "api"}
            ),
            patch.object(mgr, "build_agent_context_prompt", return_value=""),
            patch.object(mgr, "_wee_execute_tool", side_effect=track),
            patch.object(mgr, "_wee_load_messages", return_value=[]),
            patch.object(mgr, "_wee_save_messages"),
        ):
            result = mgr.run_wee_native(
                model="qwen3:8b",
                prompt="two things",
                agent="orchestrator",
                session_id=None,
                resume=True,
                n8n_session_id="t107_4",
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(result, "Done")


class TestMaxRoundsSafetyNet(unittest.TestCase):

    @patch("openai.OpenAI")
    def test_max_rounds_fallback(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = [
            iter(list(make_tool_call_stream([("cx", "bash", '{"command": "echo r"}')])))
            for _ in range(12)
        ]
        mgr = _create_test_manager()
        with (
            patch.object(
                mgr, "get_or_create_session_data", return_value={"channel": "api"}
            ),
            patch.object(mgr, "build_agent_context_prompt", return_value=""),
            patch.object(mgr, "_wee_execute_tool", return_value="round result"),
            patch.object(mgr, "_wee_load_messages", return_value=[]),
            patch.object(mgr, "_wee_save_messages"),
        ):
            result = mgr.run_wee_native(
                model="qwen3:8b",
                prompt="loop",
                agent="orchestrator",
                session_id=None,
                resume=True,
                n8n_session_id="t107_5",
            )
        self.assertIn("Tool execution completed", result)
        self.assertIn("round result", result)


class TestToolsRetryFallback(unittest.TestCase):

    @patch("openai.OpenAI")
    def test_retry_without_tools(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = [
            Exception("tools not supported"),
            iter(list(make_text_stream("Fallback"))),
        ]
        mgr = _create_test_manager()
        with (
            patch.object(
                mgr, "get_or_create_session_data", return_value={"channel": "api"}
            ),
            patch.object(mgr, "build_agent_context_prompt", return_value=""),
            patch.object(mgr, "_wee_load_messages", return_value=[]),
            patch.object(mgr, "_wee_save_messages"),
        ):
            result = mgr.run_wee_native(
                model="qwen3:8b",
                prompt="Hi",
                agent="orchestrator",
                session_id=None,
                resume=True,
                n8n_session_id="t107_6",
            )
        self.assertEqual(result, "Fallback")


class TestWeeRuntimeStandalone(unittest.TestCase):

    def test_resolve_ollama_prefix(self):
        from wee_runtime import resolve_model_and_endpoint

        m, b, k = resolve_model_and_endpoint("ollama/qwen3:8b")
        self.assertEqual(m, "qwen3:8b")
        self.assertIn("11434", b)

    def test_resolve_no_prefix(self):
        from wee_runtime import resolve_model_and_endpoint

        m, b, k = resolve_model_and_endpoint("gemma4:e4b")
        self.assertEqual(m, "gemma4:e4b")
        self.assertIn("11434", b)

    def test_tool_definitions(self):
        from wee_runtime import _WEE_TOOLS

        names = [t["function"]["name"] for t in _WEE_TOOLS]
        self.assertIn("bash", names)
        self.assertIn("python", names)

    def test_constants(self):
        import wee_runtime

        self.assertEqual(wee_runtime.MAX_TOOL_ROUNDS, 10)
        self.assertEqual(wee_runtime.TOOL_TIMEOUT, 120)


if __name__ == "__main__":
    unittest.main()
