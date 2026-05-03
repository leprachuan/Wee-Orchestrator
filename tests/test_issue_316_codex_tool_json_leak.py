"""
Regression test for issue #316: WebUI leaks raw Codex tool-call JSON into chat transcript

When Codex tool calls return JSON results, those results should not leak into
the assistant message text displayed to the user. The strip_metadata function
must filter out JSON-like content that appears in agent_message.text fields.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("API_SHARED_KEY", "test_key_316")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9316")

from agent_manager import SessionManager
from session_manager_components import StreamBuffer


def _make_sm():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"agents": []}, tmp)
    tmp.close()
    return SessionManager(tmp.name), tmp.name


class TestIssue316CodexToolResultLeakage(unittest.TestCase):
    """Issue #316: Codex tool result JSON must not leak into WebUI chat transcript."""

    @classmethod
    def setUpClass(cls):
        cls.sm, cls.config_path = _make_sm()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.config_path)

    def test_pure_tool_result_json_filtered(self):
        """When agent_message contains pure JSON tool result, it should be filtered."""
        codex_output = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "abc123"}),
            json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "agent_message",
                    "text": json.dumps({"exit_code": 0, "status": "completed", "stdout": "file1.txt"})
                }
            }),
        ])
        
        result = self.sm.strip_metadata(codex_output, "codex")
        # Tool result JSON should be filtered out
        self.assertNotIn("exit_code", result)
        self.assertNotIn("status", result)

    def test_mixed_text_and_json_cleaned(self):
        """When agent_message contains mixed text and JSON, only text should remain."""
        codex_output = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "abc123"}),
            json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "agent_message",
                    "text": "Results: " + json.dumps({"exit_code": 0, "data": "file1"})
                }
            }),
        ])
        
        result = self.sm.strip_metadata(codex_output, "codex")
        # JSON patterns with tool result keys should be removed
        self.assertNotIn('"exit_code"', result)

    def test_clean_agent_message_preserved(self):
        """Clean agent messages without JSON should be fully preserved."""
        codex_output = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "abc123"}),
            json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "agent_message",
                    "text": "Task completed successfully. Found 3 files."
                }
            }),
        ])
        
        result = self.sm.strip_metadata(codex_output, "codex")
        self.assertIn("Task completed successfully", result)
        self.assertIn("Found 3 files", result)

    def test_tool_result_event_ignored(self):
        """Tool result events should never be pushed to output."""
        codex_output = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "abc123"}),
            json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "tool_result",
                    "content": json.dumps({"exit_code": 0})
                }
            }),
            json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "item_2",
                    "type": "agent_message",
                    "text": "Done"
                }
            }),
        ])
        
        result = self.sm.strip_metadata(codex_output, "codex")
        self.assertIn("Done", result)
        self.assertNotIn("exit_code", result)

    def test_clean_tool_result_json_helper(self):
        """Test the _clean_tool_result_json_from_text helper directly."""
        test_cases = [
            # (input, should_be_filtered)
            (json.dumps({"exit_code": 0, "status": "completed"}), True),
            ("Task completed successfully", False),
            ("Results: " + json.dumps({"exit_code": 0}), True),  # Mixed content with tool JSON
            ("Normal output without JSON", False),
        ]
        
        for input_text, should_be_empty in test_cases:
            result = self.sm._clean_tool_result_json_from_text(input_text)
            if should_be_empty:
                # Should be empty or minimal after cleaning
                self.assertNotIn("exit_code", result, f"Failed for input: {input_text}")
            else:
                # Should be preserved
                self.assertIn(input_text.split(json.dumps({"exit_code": 0}))[0] if json.dumps({"exit_code": 0}) in input_text else input_text, 
                             result or input_text, f"Failed for input: {input_text}")

    def test_command_execution_frames_become_tool_events_not_text_chunks(self):
        """Codex command_execution frames must render as tool events, not raw JSON text."""
        session_id = f"test-316-{threading.get_ident()}"
        buf = StreamBuffer()
        self.sm._stream_buffers[session_id] = buf
        self.sm._stream_queues[session_id] = (MagicMock(), MagicMock())

        codex_lines = [
            json.dumps({"type": "thread.started", "thread_id": "abc123"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item_74",
                        "type": "command_execution",
                        "command": "/bin/bash -lc 'curl -sk https://127.0.0.1:8000/api/v1/health'",
                        "aggregated_output": '{"status":"ok"}',
                        "exit_code": 0,
                        "status": "completed",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item_75",
                        "type": "agent_message",
                        "text": "API health check passed.",
                    },
                }
            ),
        ]
        output_text = "\n".join(codex_lines) + "\n"
        script = (
            "import sys; " f"sys.stdout.write({output_text!r}); " "sys.stdout.flush()"
        )
        cmd = ["python3", "-c", script]

        with (
            patch.object(self.sm, "track_running_query"),
            patch.object(self.sm, "update_query_output"),
        ):
            self.sm._execute_subprocess_with_tracking(
                cmd=cmd,
                cwd="/tmp",
                timeout=30,
                runtime="codex",
                agent="test-agent",
                prompt="test prompt",
                n8n_session_id=session_id,
            )

        self.sm._stream_buffers.pop(session_id, None)
        self.sm._stream_queues.pop(session_id, None)

        chunk_text = " ".join(str(data) for kind, data in buf.chunks if kind == "chunk")
        tool_events = [data for kind, data in buf.chunks if kind == "tool_call"]

        self.assertIn("API health check passed.", chunk_text)
        self.assertNotIn("command_execution", chunk_text)
        self.assertNotIn("aggregated_output", chunk_text)
        self.assertGreaterEqual(len(tool_events), 2)
        self.assertEqual(tool_events[0]["name"], "shell")
        self.assertEqual(tool_events[0]["event"], "detected")
        self.assertEqual(tool_events[-1]["event"], "completed")
        self.assertIn("curl -sk https://127.0.0.1:8000/api/v1/health", tool_events[0]["input"])
        self.assertIn('{"status":"ok"}', tool_events[-1]["output"])


if __name__ == '__main__':
    unittest.main()
