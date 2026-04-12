#!/usr/bin/env python3
"""Regression tests for Issue #111: wee runtime tool & skill execution audit.

Tests:
1. build_agent_context_prompt called with correct arg order (agent, prompt, session_id)
2. System prompt contains explicit tool capability declaration
3. Tool schemas (bash, python) present in every Ollama API call
4. A bash tool_call is issued when prompted to run a shell command
5. _wee_augment_system_prompt_with_tools appends tool section
6. Skills/AGENTS.md context injected for wee runtime via build_agent_context_prompt
"""
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_manager import SessionManager


def _make_mgr():
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_map = {}
    mgr._session_map_lock = threading.Lock()
    mgr.command_timeout = 300
    mgr.AGENTS = {
        "orchestrator": {
            "path": "/opt",
            "description": "Orchestrator agent",
            "name": "orchestrator",
        }
    }
    mgr._stream_buffers = {}
    mgr.session_map_file = Path("/tmp/wee111_session_map.json")
    return mgr


def _make_text_chunk(content_text):
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = content_text
    chunk.choices[0].delta.tool_calls = None
    return chunk


def _make_tool_call_chunks(tool_id, func_name, arguments_json):
    chunks = []
    c1 = MagicMock()
    c1.choices = [MagicMock()]
    c1.choices[0].delta.content = None
    tc1 = MagicMock()
    tc1.index = 0
    tc1.id = tool_id
    tc1.function = MagicMock()
    tc1.function.name = func_name
    tc1.function.arguments = ""
    c1.choices[0].delta.tool_calls = [tc1]
    chunks.append(c1)

    c2 = MagicMock()
    c2.choices = [MagicMock()]
    c2.choices[0].delta.content = None
    tc2 = MagicMock()
    tc2.index = 0
    tc2.id = None
    tc2.function = MagicMock()
    tc2.function.name = None
    tc2.function.arguments = arguments_json
    c2.choices[0].delta.tool_calls = [tc2]
    chunks.append(c2)

    c3 = MagicMock()
    c3.choices = []
    chunks.append(c3)
    return chunks


def _run_wee_with_openai(mgr, session_id, mock_client, prompt="test",
                          model="ollama/qwen3:8b", resume=False, **kwargs):
    """Run wee native with a pre-configured mock OpenAI client."""
    session_data = {
        "runtime": "wee",
        "model": model,
        "channel": "api",
    }
    mgr.session_map[session_id] = session_data

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

    with patch.object(mgr, "get_or_create_session_data", return_value=session_data):
        with patch.object(mgr, "build_agent_context_prompt",
                          return_value="[sys] You are a helpful assistant."):
            with patch.object(mgr, "load_session_map", return_value={session_id: session_data}):
                with patch.object(mgr, "save_session_map"):
                    return mgr.run_wee_native(**defaults)


class TestIssue111ToolAudit(unittest.TestCase):
    """Issue #111: wee runtime tool and skill execution audit + fix."""

    @patch("openai.OpenAI")
    def test_build_agent_context_prompt_correct_arg_order(self, mock_openai_cls):
        """Issue #111: build_agent_context_prompt must be called with (agent, prompt, session_id, ...)
        
        The old buggy call was build_agent_context_prompt(prompt, agent, channel, session_id)
        which passed user text as agent name and vice versa.
        """
        mgr = _make_mgr()
        sid = "test_111_arg_order"
        session_data = {"runtime": "wee", "model": "ollama/qwen3:8b", "channel": "api"}

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter([_make_text_chunk("done")])
        mock_openai_cls.return_value = mock_client

        captured_calls = []

        def capture_context_prompt(agent, prompt, n8n_session_id, **kw):
            captured_calls.append({
                "agent": agent,
                "prompt": prompt,
                "n8n_session_id": n8n_session_id,
                "kwargs": kw,
            })
            return "system prompt for testing"

        with patch.object(mgr, "get_or_create_session_data", return_value=session_data):
            with patch.object(mgr, "build_agent_context_prompt", side_effect=capture_context_prompt):
                with patch.object(mgr, "load_session_map", return_value={sid: session_data}):
                    with patch.object(mgr, "save_session_map"):
                        mgr.run_wee_native(
                            prompt="run ls /opt",
                            model="ollama/qwen3:8b",
                            agent="orchestrator",
                            session_id=None,
                            resume=False,
                            n8n_session_id=sid,
                            timeout=30,
                        )

        self.assertEqual(len(captured_calls), 1, "build_agent_context_prompt must be called once")
        call_args = captured_calls[0]

        # The first positional arg must be the AGENT name, not the user prompt
        self.assertEqual(
            call_args["agent"],
            "orchestrator",
            f"First arg must be agent='orchestrator', got {call_args['agent']!r}",
        )
        # The second positional arg must be the user PROMPT, not the agent name
        self.assertEqual(
            call_args["prompt"],
            "run ls /opt",
            f"Second arg must be prompt='run ls /opt', got {call_args['prompt']!r}",
        )
        # session_id must be the actual session ID string
        self.assertEqual(
            call_args["n8n_session_id"],
            sid,
            f"Third arg must be n8n_session_id='{sid}', got {call_args['n8n_session_id']!r}",
        )

    @patch("openai.OpenAI")
    def test_tool_schemas_in_every_api_call(self, mock_openai_cls):
        """Issue #111: tools parameter must be present in every Ollama API call."""
        mgr = _make_mgr()
        sid = "test_111_tool_schemas"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter([_make_text_chunk("Hello!")])
        mock_openai_cls.return_value = mock_client

        _run_wee_with_openai(mgr, sid, mock_client, prompt="Hello")

        api_calls = mock_client.chat.completions.create.call_args_list
        self.assertGreater(len(api_calls), 0, "At least one API call must be made")

        for i, api_call in enumerate(api_calls):
            kwargs = api_call[1] if api_call[1] else {}
            self.assertIn(
                "tools",
                kwargs,
                f"API call #{i+1} must include 'tools' parameter",
            )
            tools = kwargs["tools"]
            self.assertIsInstance(tools, list, "'tools' must be a list")
            tool_names = [t["function"]["name"] for t in tools]
            self.assertIn("bash", tool_names, "bash tool must be in tool schemas")
            self.assertIn("python", tool_names, "python tool must be in tool schemas")

    def test_system_prompt_contains_tool_declaration(self):
        """Issue #111: System prompt must explicitly declare available tools.
        
        Many Ollama models ignore JSON tool schemas without a text declaration.
        """
        mgr = _make_mgr()

        base_prompt = "You are a helpful assistant."
        augmented = mgr._wee_augment_system_prompt_with_tools(base_prompt)

        self.assertIn(
            "bash",
            augmented.lower(),
            "System prompt must mention 'bash' tool",
        )
        self.assertIn(
            "python",
            augmented.lower(),
            "System prompt must mention 'python' tool",
        )
        # Must include some guidance about actually using the tools
        tool_keywords = ["tool", "command", "execute", "call"]
        self.assertTrue(
            any(kw in augmented.lower() for kw in tool_keywords),
            f"System prompt must include tool usage guidance (one of: {tool_keywords})",
        )
        # Original content must be preserved
        self.assertIn(base_prompt, augmented, "Original prompt content must be preserved")
        # New content must be appended (not prepended)
        self.assertTrue(
            augmented.startswith(base_prompt),
            "Tool declaration must be appended, not prepended",
        )

    def test_wee_augment_method_exists_and_callable(self):
        """Issue #111: _wee_augment_system_prompt_with_tools method must exist."""
        mgr = _make_mgr()
        self.assertTrue(
            hasattr(mgr, "_wee_augment_system_prompt_with_tools"),
            "SessionManager must have _wee_augment_system_prompt_with_tools method",
        )
        result = mgr._wee_augment_system_prompt_with_tools("base prompt")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), len("base prompt"))

    @patch("openai.OpenAI")
    def test_bash_tool_call_issued_for_shell_prompt(self, mock_openai_cls):
        """Issue #111: A prompt to run 'ls /opt' must result in a bash tool call."""
        mgr = _make_mgr()
        sid = "test_111_bash_call"
        session_data = {"runtime": "wee", "model": "ollama/qwen3:8b", "channel": "api"}

        tool_call_args = json.dumps({"command": "ls /opt"})
        tool_chunks = _make_tool_call_chunks("tc_ls_01", "bash", tool_call_args)
        final_chunk = _make_text_chunk("Here are the contents of /opt: ...")

        call_count = [0]
        def mock_create(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return iter(tool_chunks)
            return iter([final_chunk])

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = mock_create
        mock_openai_cls.return_value = mock_client

        tool_executed = []

        def mock_execute_tool(func_name, func_args, agent):
            tool_executed.append({"name": func_name, "args": func_args})
            return "bin  etc  opt  usr"

        with patch.object(mgr, "get_or_create_session_data", return_value=session_data):
            with patch.object(mgr, "build_agent_context_prompt",
                              return_value="[sys] bash python tools available"):
                with patch.object(mgr, "load_session_map", return_value={sid: session_data}):
                    with patch.object(mgr, "save_session_map"):
                        with patch.object(mgr, "_wee_execute_tool", side_effect=mock_execute_tool):
                            mgr.run_wee_native(
                                prompt="run ls /opt and tell me what you see",
                                model="ollama/qwen3:8b",
                                agent="orchestrator",
                                session_id=None,
                                resume=False,
                                n8n_session_id=sid,
                                timeout=30,
                            )

        self.assertGreater(
            len(tool_executed),
            0,
            "At least one tool must be executed when prompted to run a command",
        )
        bash_calls = [t for t in tool_executed if t["name"] == "bash"]
        self.assertGreater(
            len(bash_calls),
            0,
            "bash tool must be called for 'run ls /opt' prompt",
        )
        self.assertIn(
            "ls",
            bash_calls[0]["args"].get("command", ""),
            "bash tool call must include 'ls' in command argument",
        )

    @patch("openai.OpenAI")
    def test_tool_schemas_have_correct_structure(self, mock_openai_cls):
        """Issue #111: Tool schemas must have correct OpenAI function-calling structure."""
        mgr = _make_mgr()
        sid = "test_111_schema_structure"
        session_data = {"runtime": "wee", "model": "ollama/qwen3:8b", "channel": "api"}

        captured_tools = []

        def capture_create(**kwargs):
            if "tools" in kwargs:
                captured_tools.extend(kwargs["tools"])
            return iter([_make_text_chunk("ok")])

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = capture_create
        mock_openai_cls.return_value = mock_client

        with patch.object(mgr, "get_or_create_session_data", return_value=session_data):
            with patch.object(mgr, "build_agent_context_prompt", return_value="sys"):
                with patch.object(mgr, "load_session_map", return_value={sid: session_data}):
                    with patch.object(mgr, "save_session_map"):
                        mgr.run_wee_native(
                            prompt="test",
                            model="ollama/qwen3:8b",
                            agent="orchestrator",
                            session_id=None,
                            resume=False,
                            n8n_session_id=sid,
                            timeout=30,
                        )

        self.assertGreater(len(captured_tools), 0, "Tools must be captured from API calls")

        for tool in captured_tools:
            self.assertEqual(tool.get("type"), "function", "Tool type must be 'function'")
            self.assertIn("function", tool, "Tool must have 'function' key")
            func = tool["function"]
            self.assertIn("name", func, "Tool function must have 'name'")
            self.assertIn("description", func, "Tool function must have 'description'")
            self.assertIn("parameters", func, "Tool function must have 'parameters'")
            params = func["parameters"]
            self.assertEqual(params.get("type"), "object", "Parameters type must be 'object'")
            self.assertIn("properties", params, "Parameters must have 'properties'")
            self.assertIn("required", params, "Parameters must have 'required'")

    @patch("openai.OpenAI")
    def test_context_prompt_passed_as_system_message(self, mock_openai_cls):
        """Issue #111: context_prompt must appear as role=system in first message."""
        mgr = _make_mgr()
        sid = "test_111_sys_msg"
        session_data = {"runtime": "wee", "model": "ollama/qwen3:8b", "channel": "api"}

        expected_sys_content = "[sys] You are a helpful assistant with bash/python tools."

        captured_messages = []

        def capture_create(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            return iter([_make_text_chunk("ok")])

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = capture_create
        mock_openai_cls.return_value = mock_client

        with patch.object(mgr, "get_or_create_session_data", return_value=session_data):
            with patch.object(mgr, "build_agent_context_prompt", return_value=expected_sys_content):
                with patch.object(mgr, "load_session_map", return_value={sid: session_data}):
                    with patch.object(mgr, "save_session_map"):
                        mgr.run_wee_native(
                            prompt="test user message",
                            model="ollama/qwen3:8b",
                            agent="orchestrator",
                            session_id=None,
                            resume=False,
                            n8n_session_id=sid,
                            timeout=30,
                        )

        system_msgs = [m for m in captured_messages if m.get("role") == "system"]
        self.assertGreater(len(system_msgs), 0, "At least one system message must be in API call")
        sys_content = system_msgs[0]["content"]
        # The augmented prompt must contain the base content
        self.assertIn(
            expected_sys_content,
            sys_content,
            "System message must contain base context prompt",
        )
        # And the tool declaration
        self.assertIn(
            "bash",
            sys_content.lower(),
            "System message must include bash tool declaration",
        )


if __name__ == "__main__":
    unittest.main()
