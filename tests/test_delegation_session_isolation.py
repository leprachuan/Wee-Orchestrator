"""
Tests for issue #65: Auto-delegation session isolation.

Verifies that _execute_with_context with is_delegation=True uses an
ephemeral session key, preserving the caller's session_map entry.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch
from uuid import uuid4

# Ensure the repo root is importable
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

os.environ.setdefault("API_SHARED_KEY", "test_key_123")


class TestDelegationSessionIsolation(unittest.TestCase):
    """Tests that auto-delegation does not corrupt the caller's session."""

    def setUp(self):
        """Set up a minimal SessionManager with a temp session map."""
        import threading

        from agent_manager import SessionManager

        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        self.tmp.write("{}")
        self.tmp.flush()
        self.tmp_path = self.tmp.name

        self.mgr = SessionManager.__new__(SessionManager)
        self.mgr.session_map_file = Path(self.tmp_path)
        self.mgr._session_map_lock = threading.Lock()
        self.mgr.command_timeout = 300
        self.mgr.AGENTS = {
            "orchestrator": {
                "name": "orchestrator",
                "description": "Main orchestrator",
                "path": "/opt/orchestrator",
            },
            "devops": {
                "name": "devops",
                "description": "DevOps agent",
                "path": "/opt/devops",
            },
            "family": {
                "name": "family",
                "description": "Family knowledge",
                "path": "/opt/family",
            },
        }

    def tearDown(self):
        try:
            os.unlink(self.tmp_path)
        except OSError:
            pass

    def _seed_session(self, n8n_session_id, agent="orchestrator", **extra):
        """Write a session entry directly to the session map file."""
        data = {
            "session_id": str(uuid4()),
            "model": "gpt-5-mini",
            "agent": agent,
            "runtime": "copilot",
            "channel": "telegram",
            "identity": "8193231291",
            "render_type": "telegram_html",
            "bot_id": "1291",
            "last_activity": time.time(),
        }
        data.update(extra)
        with open(self.tmp_path, "r") as f:
            session_map = json.load(f)
        session_map[n8n_session_id] = data
        with open(self.tmp_path, "w") as f:
            json.dump(session_map, f)
        return data

    def _read_session_map(self):
        with open(self.tmp_path, "r") as f:
            return json.load(f)

    # ── Core test: delegation does NOT overwrite caller's agent ──

    @patch.object(
        __import__("agent_manager").SessionManager,
        "_dispatch_single_runtime",
        return_value="Delegation result from devops",
    )
    def test_delegation_preserves_caller_agent(self, mock_dispatch):
        """Issue #65: auto-delegation must not change the caller's agent field."""
        caller_sid = "telegram_8405010413_8193231291"
        original_data = self._seed_session(caller_sid, agent="orchestrator")

        delegation_data = {
            "session_id": str(uuid4()),
            "model": "gpt-5-mini",
            "agent": "devops",
            "runtime": "copilot",
            "is_delegation": True,
        }

        result = self.mgr._execute_with_context(
            "check disk usage", delegation_data, caller_sid
        )

        # Verify the caller's session is unchanged
        session_map = self._read_session_map()
        self.assertIn(caller_sid, session_map)
        self.assertEqual(
            session_map[caller_sid]["agent"],
            "orchestrator",
            "Caller's agent must remain 'orchestrator' after delegation",
        )

        # Verify delegation output is returned
        self.assertEqual(result, "Delegation result from devops")

    @patch.object(
        __import__("agent_manager").SessionManager,
        "_dispatch_single_runtime",
        return_value="Delegation result",
    )
    def test_delegation_uses_ephemeral_session_key(self, mock_dispatch):
        """Delegation should pass an ephemeral session key to _dispatch_single_runtime."""
        caller_sid = "telegram_8405010413_8193231291"
        self._seed_session(caller_sid, agent="orchestrator")

        delegation_sid = str(uuid4())
        delegation_data = {
            "session_id": delegation_sid,
            "model": "gpt-5-mini",
            "agent": "devops",
            "runtime": "copilot",
            "is_delegation": True,
        }

        self.mgr._execute_with_context("test prompt", delegation_data, caller_sid)

        # Verify _dispatch_single_runtime was called with ephemeral key, not caller's
        args, kwargs = mock_dispatch.call_args
        effective_sid = args[6]  # n8n_session_id is 7th positional arg
        self.assertNotEqual(
            effective_sid,
            caller_sid,
            "Delegation must NOT use the caller's n8n_session_id",
        )
        self.assertTrue(
            effective_sid.startswith("delegation_"),
            f"Ephemeral key should start with 'delegation_', got: {effective_sid}",
        )

    @patch.object(
        __import__("agent_manager").SessionManager,
        "_dispatch_single_runtime",
        return_value="Result",
    )
    def test_ephemeral_session_cleaned_up(self, mock_dispatch):
        """Ephemeral delegation session should be removed from session_map after execution."""
        caller_sid = "telegram_8405010413_8193231291"
        self._seed_session(caller_sid, agent="orchestrator")

        delegation_data = {
            "session_id": str(uuid4()),
            "model": "gpt-5-mini",
            "agent": "family",
            "runtime": "copilot",
            "is_delegation": True,
        }

        self.mgr._execute_with_context("what's for dinner", delegation_data, caller_sid)

        # Verify no delegation_* keys remain in session_map
        session_map = self._read_session_map()
        delegation_keys = [k for k in session_map if k.startswith("delegation_")]
        self.assertEqual(
            len(delegation_keys),
            0,
            f"Ephemeral delegation sessions must be cleaned up, found: {delegation_keys}",
        )

    @patch.object(
        __import__("agent_manager").SessionManager,
        "_dispatch_single_runtime",
        return_value="Result",
    )
    def test_delegation_inherits_channel_identity(self, mock_dispatch):
        """Ephemeral session should inherit channel/identity from the caller."""
        caller_sid = "telegram_8405010413_8193231291"
        self._seed_session(
            caller_sid,
            agent="orchestrator",
            channel="telegram",
            identity="8193231291",
            render_type="telegram_html",
            bot_id="1291",
        )

        delegation_sid = str(uuid4())
        delegation_data = {
            "session_id": delegation_sid,
            "model": "gpt-5-mini",
            "agent": "devops",
            "runtime": "copilot",
            "is_delegation": True,
        }

        # Capture the ephemeral session state before cleanup by patching
        captured_sessions = {}
        original_dispatch = self.mgr._dispatch_single_runtime

        def capture_dispatch(*args, **kwargs):
            effective_sid = args[6]
            with open(self.tmp_path, "r") as f:
                captured_sessions[effective_sid] = json.load(f).get(effective_sid, {})
            return "Result"

        with patch.object(
            self.mgr, "_dispatch_single_runtime", side_effect=capture_dispatch
        ):
            self.mgr._execute_with_context("check infra", delegation_data, caller_sid)

        ephemeral_key = f"delegation_{delegation_sid}"
        self.assertIn(ephemeral_key, captured_sessions)
        eph_data = captured_sessions[ephemeral_key]
        self.assertEqual(eph_data.get("channel"), "telegram")
        self.assertEqual(eph_data.get("identity"), "8193231291")
        self.assertEqual(eph_data.get("agent"), "devops")

    # ── Non-delegation calls should be unaffected ──

    @patch.object(
        __import__("agent_manager").SessionManager,
        "_dispatch_single_runtime",
        return_value="Normal result",
    )
    def test_non_delegation_uses_original_session_id(self, mock_dispatch):
        """When is_delegation is False, original n8n_session_id should be used."""
        caller_sid = "webui_session_abc"
        self._seed_session(caller_sid, agent="orchestrator")

        delegation_data = {
            "session_id": str(uuid4()),
            "model": "gpt-5-mini",
            "agent": "orchestrator",
            "runtime": "copilot",
            # is_delegation is NOT set (defaults to False)
        }

        self.mgr._execute_with_context("hello", delegation_data, caller_sid)

        args, _ = mock_dispatch.call_args
        effective_sid = args[6]
        self.assertEqual(
            effective_sid,
            caller_sid,
            "Non-delegation calls must use the original n8n_session_id",
        )

    # ── Agent invoke slash command (same pattern) ──

    @patch.object(
        __import__("agent_manager").SessionManager,
        "_dispatch_single_runtime",
        return_value="Invoke result",
    )
    def test_agent_invoke_preserves_caller_session(self, mock_dispatch):
        """'/agent invoke' uses same delegation path — must also preserve caller."""
        caller_sid = "webex_bot_123_456"
        self._seed_session(caller_sid, agent="orchestrator", channel="webex")

        # Simulate what /agent invoke does
        delegation_data = {
            "session_id": str(uuid4()),
            "model": "gpt-5-mini",
            "agent": "family",
            "runtime": "copilot",
            "is_delegation": True,
        }

        self.mgr._execute_with_context("get recipe", delegation_data, caller_sid)

        session_map = self._read_session_map()
        self.assertEqual(
            session_map[caller_sid]["agent"],
            "orchestrator",
            "/agent invoke must not change caller's agent",
        )

    # ── Concurrent delegation safety ──

    @patch.object(
        __import__("agent_manager").SessionManager,
        "_dispatch_single_runtime",
        return_value="Result",
    )
    def test_concurrent_delegations_isolated(self, mock_dispatch):
        """Multiple concurrent delegations from same session should each be isolated."""
        caller_sid = "telegram_8405010413_8193231291"
        self._seed_session(caller_sid, agent="orchestrator")

        sid_1 = str(uuid4())
        sid_2 = str(uuid4())

        delegation_data_1 = {
            "session_id": sid_1,
            "agent": "devops",
            "runtime": "copilot",
            "is_delegation": True,
        }
        delegation_data_2 = {
            "session_id": sid_2,
            "agent": "family",
            "runtime": "copilot",
            "is_delegation": True,
        }

        self.mgr._execute_with_context("task 1", delegation_data_1, caller_sid)
        self.mgr._execute_with_context("task 2", delegation_data_2, caller_sid)

        session_map = self._read_session_map()
        self.assertEqual(session_map[caller_sid]["agent"], "orchestrator")
        # No ephemeral keys remain
        delegation_keys = [k for k in session_map if k.startswith("delegation_")]
        self.assertEqual(len(delegation_keys), 0)


class TestDetectAgentDelegation(unittest.TestCase):
    """Verify detect_agent_delegation patterns match correctly."""

    def setUp(self):
        import threading

        from agent_manager import SessionManager

        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        self.tmp.write("{}")
        self.tmp.flush()

        self.mgr = SessionManager.__new__(SessionManager)
        self.mgr.session_map_file = Path(self.tmp.name)
        self.mgr._session_map_lock = threading.Lock()
        self.mgr.AGENTS = {
            "orchestrator": {"name": "orchestrator", "description": "", "path": "/opt"},
            "devops": {"name": "devops", "description": "", "path": "/opt"},
            "family": {"name": "family", "description": "", "path": "/opt"},
        }

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_detects_ask_the_pattern(self):
        agent, prompt = self.mgr.detect_agent_delegation(
            "ask the devops agent to check disk"
        )
        self.assertEqual(agent, "devops")

    def test_detects_have_the_pattern(self):
        agent, prompt = self.mgr.detect_agent_delegation(
            "have the family agent find the recipe"
        )
        self.assertEqual(agent, "family")

    def test_no_delegation_for_plain_prompt(self):
        agent, prompt = self.mgr.detect_agent_delegation("what is the weather today")
        self.assertIsNone(agent)
        self.assertEqual(prompt, "what is the weather today")

    def test_cleaned_prompt_removes_delegation_phrase(self):
        agent, prompt = self.mgr.detect_agent_delegation(
            "ask the devops agent check disk usage"
        )
        self.assertEqual(agent, "devops")
        self.assertNotIn("ask the devops", prompt.lower())


if __name__ == "__main__":
    unittest.main()
