#!/usr/bin/env python3
"""Regression tests for GitHub issue #444.

Bug: agent delegation fell back to a stale hard-coded token when
WEE_ORCHESTRATOR_TOKEN/API_SHARED_KEY were unset, and background-task
payloads dropped origin_session_id, breaking authenticated delegation
and result routing back to the originating chat session.
"""
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("API_SHARED_KEY", "test_key_444")

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from agent_manager import SessionManager  # noqa: E402


def _make_mgr():
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_map = {}
    mgr._session_map_lock = threading.Lock()
    mgr.command_timeout = 300
    mgr.AGENTS = {
        "research": {
            "path": "/opt",
            "description": "Research agent",
            "name": "research",
            "primary_runtime": "copilot",
            "primary_model": "claude-haiku-4.5",
        },
    }
    mgr._stream_buffers = {}
    mgr.session_map_file = Path("/tmp/wee_test_444_session_map.json")
    return mgr


def _mock_urlopen(response_body: dict):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_body).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestIssue444FallbackToken(unittest.TestCase):
    """No stale hard-coded token may ever be sent as the Authorization header."""

    STALE_TOKEN = "shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU"

    def test_token_derived_from_api_shared_key_when_no_override(self):
        mgr = _make_mgr()
        mock_resp = _mock_urlopen({"id": "bg_1", "status": "queued"})
        captured = {}

        def fake_urlopen(req, data=None, context=None, timeout=None):
            captured["auth_header"] = req.get_header("Authorization")
            return mock_resp

        env = {"API_SHARED_KEY": "current_live_key"}
        with patch.dict(os.environ, env, clear=False), \
                patch("urllib.request.urlopen", side_effect=fake_urlopen):
            os.environ.pop("WEE_ORCHESTRATOR_TOKEN", None)
            mgr._wee_call_agent(
                {"agent": "research", "prompt": "hi", "mode": "background"}
            )

        self.assertEqual(captured["auth_header"], "Bearer shared_current_live_key")
        self.assertNotEqual(captured["auth_header"], self.STALE_TOKEN)
        self.assertNotIn("R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU", captured["auth_header"])

    def test_no_hardcoded_token_leaks_when_key_rotates(self):
        """Even if API_SHARED_KEY is rotated, the old stale literal must never
        appear anywhere in the module's source as a usable fallback."""
        import agent_manager
        import inspect

        source = inspect.getsource(agent_manager.SessionManager._wee_call_agent)
        self.assertNotIn("R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU", source)

    def test_missing_shared_key_and_override_errors_cleanly(self):
        mgr = _make_mgr()
        env = os.environ.copy()
        env.pop("WEE_ORCHESTRATOR_TOKEN", None)
        env.pop("API_SHARED_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            result = mgr._wee_call_agent(
                {"agent": "research", "prompt": "hi", "mode": "background"}
            )
        self.assertIn("Error", result)
        self.assertIn("API_SHARED_KEY", result)

    def test_explicit_override_token_still_honored(self):
        mgr = _make_mgr()
        mock_resp = _mock_urlopen({"id": "bg_2", "status": "queued"})
        captured = {}

        def fake_urlopen(req, data=None, context=None, timeout=None):
            captured["auth_header"] = req.get_header("Authorization")
            return mock_resp

        env = {"WEE_ORCHESTRATOR_TOKEN": "shared_explicit_override", "API_SHARED_KEY": "irrelevant"}
        with patch.dict(os.environ, env, clear=False), \
                patch("urllib.request.urlopen", side_effect=fake_urlopen):
            mgr._wee_call_agent(
                {"agent": "research", "prompt": "hi", "mode": "background"}
            )

        self.assertEqual(captured["auth_header"], "Bearer shared_explicit_override")


class TestIssue444OriginSessionPropagation(unittest.TestCase):
    """origin_session_id must survive from the chat session through to the
    background-task payload so result routing/notifications work."""

    def test_wee_call_agent_includes_origin_session_id_in_payload(self):
        mgr = _make_mgr()
        mock_resp = _mock_urlopen({"id": "bg_3", "status": "queued"})
        captured = {}

        def fake_urlopen(req, data=None, context=None, timeout=None):
            captured["payload"] = json.loads(data.decode())
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            mgr._wee_call_agent(
                {"agent": "research", "prompt": "hi", "mode": "background"},
                origin_session_id="chat-session-abc123",
            )

        self.assertEqual(captured["payload"].get("origin_session_id"), "chat-session-abc123")

    def test_wee_execute_tool_forwards_n8n_session_id_to_call_agent(self):
        mgr = _make_mgr()
        expected = "Task dispatched to research agent.\nTask ID: bg_4"

        with patch.object(mgr, "_wee_call_agent", return_value=expected) as mock_ca:
            mgr._wee_execute_tool(
                "call_agent",
                {"agent": "research", "prompt": "hi", "mode": "background"},
                "orchestrator",
                "chat-session-xyz789",
            )

        mock_ca.assert_called_once_with(
            {"agent": "research", "prompt": "hi", "mode": "background"},
            origin_session_id="chat-session-xyz789",
        )

    def test_missing_n8n_session_id_propagates_as_none_not_omitted(self):
        """Even without a chat session, origin_session_id key must be present
        (as None) rather than silently dropped from the payload."""
        mgr = _make_mgr()
        mock_resp = _mock_urlopen({"id": "bg_5", "status": "queued"})
        captured = {}

        def fake_urlopen(req, data=None, context=None, timeout=None):
            captured["payload"] = json.loads(data.decode())
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            mgr._wee_call_agent(
                {"agent": "research", "prompt": "hi", "mode": "background"}
            )

        self.assertIn("origin_session_id", captured["payload"])
        self.assertIsNone(captured["payload"]["origin_session_id"])


if __name__ == "__main__":
    unittest.main()
