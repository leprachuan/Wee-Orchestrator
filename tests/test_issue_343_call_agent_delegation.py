#!/usr/bin/env python3
"""Regression tests for GitHub issue #343.

Bug: wee runtime completes with 0 chars when final tool call is call_agent delegation.

Validates:
- call_agent is included in _WEE_TOOLS schema
- _wee_execute_tool dispatches call_agent to _wee_call_agent
- Empty synthesis after call_agent surfaces the task dispatch result (not 0 chars)
- Successful call_agent dispatch returns non-empty task confirmation
- _wee_call_agent handles HTTP errors gracefully
"""
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

os.environ.setdefault("API_SHARED_KEY", "test_key_343")

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from agent_manager import SessionManager  # noqa: E402


def _make_mgr():
    """Create a minimal SessionManager for issue #343 testing."""
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_map = {}
    mgr._session_map_lock = threading.Lock()
    mgr.command_timeout = 300
    mgr.AGENTS = {
        "orchestrator": {
            "path": "/opt",
            "description": "Orchestrator",
            "name": "orchestrator",
        },
        "research": {
            "path": "/opt",
            "description": "Research agent",
            "name": "research",
            "primary_runtime": "copilot",
            "primary_model": "claude-haiku-4.5",
        },
    }
    mgr._stream_buffers = {}
    mgr.session_map_file = Path("/tmp/wee_test_343_session_map.json")
    return mgr


def _run_wee(mgr, session_id, prompt="test", model="ollama/gemma4:e4b", **kwargs):
    """Helper to call run_wee_native with mocked session infrastructure."""
    defaults = dict(
        prompt=prompt,
        model=model,
        agent="orchestrator",
        session_id=None,
        resume=False,
        n8n_session_id=session_id,
        timeout=30,
        render_type="text",
    )
    defaults.update(kwargs)
    session_data = mgr.session_map.get(
        session_id, {"runtime": "wee", "model": model, "channel": "api"}
    )
    with patch.object(mgr, "get_or_create_session_data", return_value=session_data):
        with patch.object(
            mgr, "build_agent_context_prompt", return_value="You are helpful."
        ):
            return mgr.run_wee_native(**defaults)


def _make_text_chunk(text):
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = text
    chunk.choices[0].delta.tool_calls = None
    return chunk


def _make_empty_chunk():
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = None
    chunk.choices[0].delta.tool_calls = None
    return chunk


def _make_tool_call_chunk(tool_id, func_name, arguments_json):
    """Return two chunks simulating a streaming tool call for func_name."""
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

    return chunks


# ===========================================================================
# Test 1: call_agent in _WEE_TOOLS
# ===========================================================================
class TestCallAgentInToolsSchema(unittest.TestCase):
    """call_agent must be registered in _WEE_TOOLS schema."""

    @patch("openai.OpenAI")
    def test_call_agent_in_wee_tools_schema(self, mock_openai_cls):
        """run_wee_native should pass call_agent in the tools parameter."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        # Return a single text response so the loop exits cleanly
        mock_client.chat.completions.create.return_value = iter(
            [_make_text_chunk("OK")]
        )
        _run_wee(mgr, "sess_343_schema", prompt="test")

        # Inspect the tools= kwarg passed to the API
        create_kwargs = mock_client.chat.completions.create.call_args[1]
        tools = create_kwargs.get("tools", [])
        tool_names = [t["function"]["name"] for t in tools]
        self.assertIn(
            "call_agent", tool_names,
            "call_agent must be included in _WEE_TOOLS so models can use it as a structured tool call",
        )


# ===========================================================================
# Test 2: 0-char output regression — call_agent + empty synthesis
# ===========================================================================
class TestCallAgentEmptySynthesisFallback(unittest.TestCase):
    """After call_agent tool execution, if model returns 0 chars, surface task result."""

    @patch("openai.OpenAI")
    def test_no_zero_char_output_after_call_agent(self, mock_openai_cls):
        """Output must not be 0 chars when call_agent is the final tool call."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        call_agent_args = json.dumps({"agent": "research", "prompt": "Find info", "mode": "background"})
        task_result = "Task dispatched to research agent.\nTask ID: bg_abc12345\nCheck status: /background status bg_abc12345"

        # Round 1: model calls call_agent
        round1 = _make_tool_call_chunk("tc_ca_1", "call_agent", call_agent_args)
        # Round 2: model produces 0 chars (the bug scenario)
        round2 = [_make_empty_chunk()]

        mock_client.chat.completions.create.side_effect = [
            iter(round1),
            iter(round2),
        ]

        with patch.object(mgr, "_wee_call_agent", return_value=task_result):
            with patch.object(mgr, "_wee_save_messages"):
                result = _run_wee(mgr, "sess_343_empty", prompt="Research local LLMs")

        self.assertGreater(
            len(result), 0,
            "Output must not be 0 chars after call_agent delegation",
        )
        self.assertIn("bg_abc12345", result, "Task ID should be surfaced in response")

    @patch("openai.OpenAI")
    def test_normal_text_synthesis_not_overridden(self, mock_openai_cls):
        """When model produces normal text after call_agent, keep it unchanged."""
        mgr = _make_mgr()
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        call_agent_args = json.dumps({"agent": "research", "prompt": "Find info"})
        task_result = "Task dispatched to research agent.\nTask ID: bg_xyz99"
        synthesis = "I've dispatched the research task. Check /background status bg_xyz99."

        round1 = _make_tool_call_chunk("tc_ca_2", "call_agent", call_agent_args)
        round2 = [_make_text_chunk(synthesis)]

        mock_client.chat.completions.create.side_effect = [
            iter(round1),
            iter(round2),
        ]

        with patch.object(mgr, "_wee_call_agent", return_value=task_result):
            with patch.object(mgr, "_wee_save_messages"):
                result = _run_wee(mgr, "sess_343_text", prompt="Research LLMs")

        self.assertEqual(result, synthesis, "Model-generated synthesis should be preserved as-is")


# ===========================================================================
# Test 3: _wee_execute_tool routes call_agent
# ===========================================================================
class TestWeeExecuteToolCallAgent(unittest.TestCase):
    """_wee_execute_tool must route call_agent to _wee_call_agent."""

    def test_call_agent_dispatched(self):
        mgr = _make_mgr()
        expected = "Task dispatched to research agent.\nTask ID: bg_deadbeef"

        with patch.object(mgr, "_wee_call_agent", return_value=expected) as mock_ca:
            result = mgr._wee_execute_tool(
                "call_agent",
                {"agent": "research", "prompt": "Find LLM info", "mode": "background"},
                "orchestrator",
            )

        self.assertEqual(result, expected)
        mock_ca.assert_called_once_with(
            {"agent": "research", "prompt": "Find LLM info", "mode": "background"}
        )

    def test_unknown_tool_error_includes_call_agent(self):
        """Error message for unknown tool should list call_agent as available."""
        mgr = _make_mgr()
        result = mgr._wee_execute_tool("nonexistent_tool", {}, "orchestrator")
        self.assertIn("call_agent", result)


# ===========================================================================
# Test 4: _wee_call_agent dispatches correctly
# ===========================================================================
class TestWeeCallAgentMethod(unittest.TestCase):
    """_wee_call_agent should call the background-tasks API and return task ID."""

    def _mock_urlopen(self, response_body: dict):
        """Return a context manager mock for urllib.request.urlopen."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_body).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_background_mode_returns_task_id(self):
        mgr = _make_mgr()
        mock_resp = self._mock_urlopen({"id": "bg_12345678", "status": "queued"})

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = mgr._wee_call_agent(
                {"agent": "research", "prompt": "Find info", "mode": "background"}
            )

        self.assertIn("bg_12345678", result)
        self.assertIn("research", result)

    def test_missing_agent_param(self):
        mgr = _make_mgr()
        result = mgr._wee_call_agent({"prompt": "Find info"})
        self.assertIn("Error", result)
        self.assertIn("agent", result)

    def test_missing_prompt_param(self):
        mgr = _make_mgr()
        result = mgr._wee_call_agent({"agent": "research"})
        self.assertIn("Error", result)
        self.assertIn("prompt", result)

    def test_http_error_returns_error_message(self):
        mgr = _make_mgr()
        import urllib.error
        mock_err = urllib.error.HTTPError(
            url="http://test", code=503, msg="Service Unavailable", hdrs=None, fp=None
        )

        with patch("urllib.request.urlopen", side_effect=mock_err):
            result = mgr._wee_call_agent(
                {"agent": "research", "prompt": "test", "mode": "background"}
            )

        self.assertIn("Error", result)
        self.assertIn("503", result)

    def test_quick_mode_returns_response(self):
        mgr = _make_mgr()
        mock_resp = self._mock_urlopen({"response": "Found 5 relevant papers.", "status": "completed"})

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = mgr._wee_call_agent(
                {"agent": "research", "prompt": "Find LLM papers", "mode": "quick"}
            )

        self.assertIn("Found 5 relevant papers", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
