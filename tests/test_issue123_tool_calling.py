"""Regression tests for Issue #123: wee runtime tool calling.

Issue #443 removed the hand-rolled OpenAI-compatible tool-calling loop this
file originally covered (raw tool schema passing, streaming tool-call
accumulation, multi-round dispatch, bash/python execution) — the Copilot SDK
is now the only execution path and owns tool execution directly. What's
still directly testable:

1. _wee_augment_system_prompt_with_tools adds the call_agent/browser section
2. _wee_load_messages / _wee_save_messages persist history
3. session_exists returns True for wee sessions with messages
4. Ollama port is 11434 (not 11436)
"""

import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

# Add project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")


def _make_mgr():
    """Create a minimal SessionManager for testing."""
    from agent_manager import SessionManager

    mgr = SessionManager.__new__(SessionManager)
    mgr.session_state_dir = Path("/tmp/test_sessions_123")
    mgr.session_state_dir.mkdir(exist_ok=True)
    mgr.command_timeout = 120
    mgr.gemini_session_dir = Path("/tmp/test_gemini_123")
    mgr.codex_session_dir = Path("/tmp/test_codex_123")
    mgr.devin_session_dir = Path("/tmp/test_devin_123")
    mgr.cursor_session_dir = Path("/tmp/test_cursor_123")
    mgr.AGENTS = {
        "orchestrator": {
            "name": "orchestrator",
            "path": "/opt/n8n-copilot-shim-dev",
            "description": "Orchestrator agent",
        },
        "devops": {
            "name": "devops",
            "path": "/opt/MyHomeDevops",
            "description": "DevOps agent",
        },
    }
    mgr._session_map_lock = MagicMock()
    mgr._session_map_lock.__enter__ = MagicMock(return_value=None)
    mgr._session_map_lock.__exit__ = MagicMock(return_value=False)
    mgr._stream_buffers = {}
    return mgr


# ── Test: Ollama port is 11434 ──────────────────────────────────────


class TestOllamaPort(unittest.TestCase):
    """Issue #123: Verify Ollama port is 11434, not 11436."""

    def test_agent_manager_presets_use_11434(self):
        """run_wee_native _PRESETS should use port 11434."""
        import agent_manager

        src = open(agent_manager.__file__).read()
        # Find _PRESETS inside run_wee_native
        idx = src.find("def run_wee_native")
        self.assertGreater(idx, 0, "run_wee_native not found")
        block = src[idx : idx + 5000]
        self.assertIn("11434", block, "Port 11434 should be in run_wee_native")
        self.assertNotIn("11436", block, "Port 11436 should NOT be in run_wee_native")

    def test_wee_runtime_presets_use_11434(self):
        """wee_runtime.py PROVIDER_PRESETS should use port 11434."""
        src = open("wee_runtime.py").read()
        self.assertIn("11434", src)
        self.assertNotIn("11436", src)

    def test_no_11436_anywhere(self):
        """No occurrence of port 11436 should remain in agent_manager.py."""
        import agent_manager

        src = open(agent_manager.__file__).read()
        self.assertNotIn("11436", src, "Port 11436 should not appear anywhere")


# ── Test: _wee_augment_system_prompt_with_tools ─────────────────────


class TestWeeAugmentSystemPrompt(unittest.TestCase):
    """Issue #123: System prompt gets tool capability section."""

    def setUp(self):
        self.mgr = _make_mgr()

    def test_augment_adds_tool_section(self):
        result = self.mgr._wee_augment_system_prompt_with_tools(
            "You are a helpful assistant."
        )
        self.assertIn("[Additional Tools]", result)
        self.assertIn("call_agent", result)
        self.assertIn("browser", result)
        self.assertIn("CRITICAL", result)

    def test_augment_preserves_original(self):
        original = "Original system prompt text."
        result = self.mgr._wee_augment_system_prompt_with_tools(original)
        self.assertTrue(result.startswith(original))

    def test_augment_empty_prompt(self):
        result = self.mgr._wee_augment_system_prompt_with_tools("")
        self.assertIn("[Additional Tools]", result)


# ── Test: _wee_load_messages / _wee_save_messages ───────────────────


class TestWeeMessagePersistence(unittest.TestCase):
    """Issue #123: Conversation history persistence."""

    def setUp(self):
        self.mgr = _make_mgr()
        self.mgr.load_session_data = MagicMock(return_value=None)
        self.mgr.load_session_map = MagicMock(return_value={})
        self.mgr.save_session_map = MagicMock()

    def test_load_fresh_conversation(self):
        msgs = self.mgr._wee_load_messages("sess1", "system prompt", resume=False)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[0]["content"], "system prompt")

    def test_load_resume_with_history(self):
        self.mgr.load_session_data = MagicMock(
            return_value={
                "wee_messages": [
                    {"role": "system", "content": "old prompt"},
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "hi!"},
                ]
            }
        )
        msgs = self.mgr._wee_load_messages("sess1", "new prompt", resume=True)
        self.assertEqual(len(msgs), 3)
        # System prompt should be refreshed
        self.assertEqual(msgs[0]["content"], "new prompt")
        self.assertEqual(msgs[1]["content"], "hello")

    def test_load_resume_no_history(self):
        self.mgr.load_session_data = MagicMock(return_value={})
        msgs = self.mgr._wee_load_messages("sess1", "prompt", resume=True)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "system")

    def test_save_messages(self):
        self.mgr.load_session_map = MagicMock(
            return_value={"sess1": {"runtime": "wee"}}
        )
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        self.mgr._wee_save_messages("sess1", messages)
        self.mgr.save_session_map.assert_called_once()
        saved = self.mgr.save_session_map.call_args[0][0]
        self.assertIn("wee_messages", saved["sess1"])
        self.assertEqual(len(saved["sess1"]["wee_messages"]), 3)

    def test_save_messages_with_tool_calls(self):
        """Tool calls in assistant messages should be serialized cleanly."""
        self.mgr.load_session_map = MagicMock(
            return_value={"sess1": {"runtime": "wee"}}
        )
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "check disk"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc_1",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": '{"command": "df -h"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "tc_1", "content": "Filesystem ..."},
            {"role": "assistant", "content": "Disk usage looks good."},
        ]
        self.mgr._wee_save_messages("sess1", messages)
        saved = self.mgr.save_session_map.call_args[0][0]
        wee_msgs = saved["sess1"]["wee_messages"]
        # Find the assistant message with tool_calls
        tc_msg = [m for m in wee_msgs if m.get("tool_calls")]
        self.assertEqual(len(tc_msg), 1)
        self.assertEqual(tc_msg[0]["tool_calls"][0]["function"]["name"], "bash")

    def test_save_caps_at_max_messages(self):
        """Messages over MAX_WEE_MESSAGES should be trimmed."""
        self.mgr.load_session_map = MagicMock(
            return_value={"sess1": {"runtime": "wee"}}
        )
        messages = [{"role": "system", "content": "sys"}]
        for i in range(150):
            messages.append({"role": "user", "content": f"msg {i}"})
            messages.append({"role": "assistant", "content": f"reply {i}"})
        self.mgr._wee_save_messages("sess1", messages)
        saved = self.mgr.save_session_map.call_args[0][0]
        self.assertLessEqual(len(saved["sess1"]["wee_messages"]), 100)


# ── Test: session_exists for wee ────────────────────────────────────


class TestSessionExistsWee(unittest.TestCase):
    """Issue #123: session_exists should detect wee sessions."""

    def setUp(self):
        self.mgr = _make_mgr()

    def test_session_exists_with_messages(self):
        self.mgr.load_session_data = MagicMock(
            return_value={"wee_messages": [{"role": "system", "content": "hi"}]}
        )
        result = self.mgr.session_exists("sid", "wee", n8n_session_id="n8n_1")
        self.assertTrue(result)

    def test_session_not_exists_no_messages(self):
        self.mgr.load_session_data = MagicMock(return_value={})
        result = self.mgr.session_exists("sid", "wee", n8n_session_id="n8n_1")
        self.assertFalse(result)

    def test_session_not_exists_none(self):
        self.mgr.load_session_data = MagicMock(return_value=None)
        result = self.mgr.session_exists("sid", "wee", n8n_session_id="n8n_1")
        self.assertFalse(result)


# ── Test: build_agent_context_prompt arg order ──────────────────────


class TestBuildContextPromptArgOrder(unittest.TestCase):
    """Issue #123: build_agent_context_prompt called with correct arg order."""

    def test_arg_order_in_source(self):
        """The call in run_wee_native should use (agent, prompt, n8n_session_id, ...)."""
        import agent_manager

        src = open(agent_manager.__file__).read()
        idx = src.find("def run_wee_native")
        block = src[idx : idx + 5000]
        # Should find the correct arg order
        self.assertIn(
            "build_agent_context_prompt(\n            agent,\n            prompt,\n            n8n_session_id,",
            block,
        )
        # Should NOT have old wrong order
        self.assertNotIn(
            "build_agent_context_prompt(\n            prompt, agent, channel", block
        )


if __name__ == "__main__":
    unittest.main()
