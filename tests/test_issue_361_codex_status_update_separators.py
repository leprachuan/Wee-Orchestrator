"""
Regression tests for issue #361: Codex response formatting collapses status
updates and paragraphs in the WebUI.

Root cause: _execute_subprocess_with_tracking pushed each Codex
``item.completed`` agent_message text straight into a single SSE chunk with
no separator between consecutive chunks, and never stripped
``[STATUS_UPDATE: ...]`` markers embedded inside the agent_message text.
This caused adjacent chunks to concatenate mid-sentence (e.g.
"complete.The deploy...") and status markers to render inline
(e.g. "...]Production").
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_361")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9361")

from agent_manager import SessionManager  # noqa: E402
from session_manager_components import StreamBuffer  # noqa: E402


def _make_sm():
    """Return a SessionManager bound to a temporary agents.json file."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"agents": []}, tmp)
    tmp.close()
    return SessionManager(tmp.name), tmp.name


class TestIssue361CodexStatusUpdateSeparators(unittest.TestCase):
    """Codex agent_message chunks must be separated and status markers stripped."""

    @classmethod
    def setUpClass(cls):
        cls.sm, cls.config_path = _make_sm()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.config_path)

    def _run_with_codex_output(self, codex_lines):
        session_id = f"test-361-{threading.get_ident()}"
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
        return session_id, buf

    def _chunk_texts(self, buf):
        """Return the list of chunk text payloads, normalizing dict/str shapes."""
        texts = []
        for kind, data in buf.chunks:
            if kind != "chunk":
                continue
            if isinstance(data, dict):
                texts.append(data.get("text", ""))
            else:
                texts.append(data)
        return texts

    def test_consecutive_agent_messages_get_paragraph_separator(self):
        _, buf = self._run_with_codex_output(
            [
                json.dumps({"type": "thread.started", "thread_id": "abc"}),
                json.dumps({"type": "turn.started"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "item_0",
                            "type": "agent_message",
                            "text": "Production deploy complete.",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "item_1",
                            "type": "agent_message",
                            "text": "The deploy took 4 minutes.",
                        },
                    }
                ),
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
            ]
        )
        texts = self._chunk_texts(buf)
        # A separator chunk must appear between the two agent_message chunks.
        self.assertIn("Production deploy complete.", texts)
        self.assertIn("The deploy took 4 minutes.", texts)
        first_idx = texts.index("Production deploy complete.")
        second_idx = texts.index("The deploy took 4 minutes.")
        between = texts[first_idx + 1 : second_idx]
        self.assertTrue(
            any("\n\n" in t for t in between),
            f"expected a paragraph separator between chunks, got {between!r}",
        )

        # The concatenated stream must not glue the two sentences together.
        joined = "".join(texts)
        self.assertNotIn("complete.The deploy", joined)

    def test_status_update_marker_stripped_from_agent_message(self):
        session_id, buf = self._run_with_codex_output(
            [
                json.dumps({"type": "thread.started", "thread_id": "abc"}),
                json.dumps({"type": "turn.started"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "item_0",
                            "type": "agent_message",
                            "text": (
                                "Deploy starting.[STATUS_UPDATE: "
                                "Restarting service...]Production is live."
                            ),
                        },
                    }
                ),
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
            ]
        )
        texts = self._chunk_texts(buf)
        joined = "".join(texts)

        # Marker must not leak into the rendered transcript.
        self.assertNotIn("[STATUS_UPDATE", joined)
        # The marker must not glue the surrounding sentences together.
        self.assertNotIn("]Production", joined)
        self.assertIn("Deploy starting.", joined)
        self.assertIn("Production is live.", joined)


if __name__ == "__main__":
    unittest.main()
