"""Regression test for Issue #399: Orchestrator session loses conversation
history mid-session on the local Ollama (wee-native) runtime.

Root cause: ``SessionManager._wee_compact_context`` built its summarization
transcript only from messages where ``role in ("user", "assistant") and
content`` was truthy. That silently dropped:
  - ``role="tool"`` messages (e.g. web_search / SearXNG results), and
  - ``role="assistant"`` messages that only carried ``tool_calls`` with no
    text content (the turn where the model *decided* to call a tool).

So once context compaction fired (common on local Ollama models with small
context windows), all trace of a prior tool call and its results vanished
from the summarized history, and a follow-up question like "what was the
cost of these?" got a response claiming no prior context existed — even
though the search happened earlier in the same session.

This test simulates exactly that shape: a tool-call turn (assistant
tool_calls + tool result with real content) followed by a context-dependent
follow-up, and asserts the compacted summary sent to the LLM includes the
prior tool call and its result content.
"""

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_manager import SessionManager  # noqa: E402


def _make_mgr():
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_map = {}
    mgr.session_map_path = "/tmp/test_sessions_399.json"
    mgr._session_map_lock = threading.Lock()
    mgr._stream_buffers = {}
    mgr.AGENTS = {
        "orchestrator": {"name": "orchestrator", "path": "/tmp", "description": ""}
    }
    mgr.command_timeout = 30
    mgr.session_map_ttl = 30 * 86400
    mgr.session_map_file = Path("/tmp/wee_test_399_map.json")
    return mgr


class TestIssue399LocalRuntimeSessionHistory(unittest.TestCase):
    def setUp(self):
        self.mgr = _make_mgr()

    def test_issue_399_local_runtime_session_history(self):
        # Simulate: user asks about a boat, assistant calls web_search,
        # tool returns real price/result content, assistant summarizes it.
        messages = [
            {"role": "system", "content": "sys prompt"},
            {"role": "user", "content": "look up the cost of a targa 37.2 boat"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc_1",
                        "type": "function",
                        "function": {
                            "name": "search",
                            "arguments": '{"q": "targa 37.2 boat cost"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "tc_1",
                "content": "Targa 37.2 base price: $412,500 USD (2026 model).",
            },
            {
                "role": "assistant",
                "content": "The Targa 37.2 starts at $412,500.",
            },
            # Follow-up 2 turns later, referencing "the results above"
            {"role": "user", "content": "what was the cost of these?"},
        ]

        captured_prompts = []
        client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "summary"
        client.chat.completions.create.side_effect = lambda **kw: (
            captured_prompts.append(kw["messages"]),
            type("R", (), {"choices": [mock_choice]})(),
        )[-1]

        with patch.object(self.mgr, "_wee_save_transcript", return_value="/tmp/t.json"):
            self.mgr._wee_compact_context(
                client, "sess-399", messages, "gemma4:e4b", "system context"
            )

        self.assertTrue(captured_prompts, "LLM should have been called for compaction")
        summary_request = next(
            (m for m in captured_prompts[0] if m.get("role") == "user"), None
        )
        self.assertIsNotNone(summary_request)

        # The prior tool call and its real result content must survive
        # compaction and be part of what gets summarized -- this is what
        # was silently dropped before the fix.
        self.assertIn(
            "search",
            summary_request["content"],
            "Tool-call turn (which tool was invoked) must not be dropped "
            "during compaction",
        )
        self.assertIn(
            "412,500",
            summary_request["content"],
            "Tool result content (e.g. web search results) must not be "
            "dropped during compaction -- this was the root cause of "
            "issue #399 where follow-up questions lost all context about "
            "prior tool output",
        )


if __name__ == "__main__":
    unittest.main()
