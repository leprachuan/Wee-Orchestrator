"""
Regression tests for issue #298: Codex WebUI streamed raw JSONL transport
frames instead of assistant text.

Root cause: _execute_subprocess_with_tracking pushed Codex --json stdout lines
straight into SSE chunks. The WebUI prefers streamed chunks over the final
stripped response, so transport events like thread.started rendered directly in
chat.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_298")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9298")

from agent_manager import SessionManager  # noqa: E402
from session_manager_components import StreamBuffer  # noqa: E402


def _make_sm():
    """Return a SessionManager bound to a temporary agents.json file."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"agents": []}, tmp)
    tmp.close()
    return SessionManager(tmp.name), tmp.name


class TestIssue298CodexStreamingJsonl(unittest.TestCase):
    """Codex JSONL metadata must not leak into SSE chunk output."""

    @classmethod
    def setUpClass(cls):
        cls.sm, cls.config_path = _make_sm()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.config_path)

    def _run_with_codex_output(self, codex_lines):
        session_id = f"test-298-{threading.get_ident()}"
        buf = StreamBuffer()
        self.sm._stream_buffers[session_id] = buf
        self.sm._stream_queues[session_id] = (MagicMock(), MagicMock())

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
        return buf

    def _chunk_text(self, buf):
        return " ".join(str(data) for kind, data in buf.chunks if kind == "chunk")

    def test_item_completed_agent_message_streams_text_only(self):
        buf = self._run_with_codex_output(
            [
                json.dumps(
                    {
                        "type": "thread.started",
                        "thread_id": "019de0db-247b-7cd1-a8cc-48aef081eccd",
                    }
                ),
                json.dumps({"type": "turn.started"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "item_0",
                            "type": "agent_message",
                            "text": "Running fine. What do you need me to handle?",
                        },
                    }
                ),
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
            ]
        )
        chunks = self._chunk_text(buf)
        self.assertIn("Running fine. What do you need me to handle?", chunks)
        self.assertNotIn("thread.started", chunks)
        self.assertNotIn("turn.started", chunks)
        self.assertNotIn("turn.completed", chunks)

    def test_metadata_only_events_produce_no_visible_chunks(self):
        buf = self._run_with_codex_output(
            [
                json.dumps({"type": "thread.started", "thread_id": "abc"}),
                json.dumps({"type": "turn.started"}),
                json.dumps({"type": "turn.completed", "usage": {"output_tokens": 3}}),
            ]
        )
        self.assertEqual(self._chunk_text(buf).strip(), "")


if __name__ == "__main__":
    unittest.main()
