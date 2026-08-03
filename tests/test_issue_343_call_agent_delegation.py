#!/usr/bin/env python3
"""Regression tests for GitHub issue #343.

Bug: wee runtime completes with 0 chars when final tool call is call_agent delegation.

Issue #443 removed the hand-rolled OpenAI-compatible fallback loop that the
original 0-char regression lived in — the Copilot SDK is now the only
execution path, and call_agent is passed to it as a Copilot SDK Tool rather
than a `_WEE_TOOLS` schema entry. What's still directly testable without
mocking the Copilot SDK's own internals:

- _wee_execute_tool dispatches call_agent to _wee_call_agent
- _wee_call_agent handles HTTP errors gracefully
"""
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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


# ===========================================================================
# Test: _wee_execute_tool routes call_agent
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
        # Issue #444: n8n_session_id is forwarded as origin_session_id so
        # background-task result routing survives delegation.
        mock_ca.assert_called_once_with(
            {"agent": "research", "prompt": "Find LLM info", "mode": "background"},
            origin_session_id=None,
        )

    def test_unknown_tool_error_includes_call_agent(self):
        """Error message for unknown tool should list call_agent as available."""
        mgr = _make_mgr()
        result = mgr._wee_execute_tool("nonexistent_tool", {}, "orchestrator")
        self.assertIn("call_agent", result)


# ===========================================================================
# Test: _wee_call_agent dispatches correctly
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
    unittest.main()
