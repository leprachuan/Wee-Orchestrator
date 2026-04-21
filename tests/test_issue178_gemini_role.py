"""
Regression tests for issue #178: Gemini runtime echoes raw JSON user messages
instead of AI responses.

Root cause: role checks used "assistant" but Gemini CLI uses "model" per Google's
API convention.  Two code paths were broken: strip_metadata and the streaming
path in _execute_subprocess_with_tracking.

These tests call the real production code in SessionManager to guard against
future regressions — if the fix is reverted, the tests will fail.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_178")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9178")

from agent_manager import SessionManager  # noqa: E402
from session_manager_components import StreamBuffer  # noqa: E402


def _make_sm():
    """Return a (SessionManager, config_path) pair using a temp agents.json."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"agents": []}, tmp)
    tmp.close()
    return SessionManager(tmp.name), tmp.name


class TestStripMetadataGeminiRole(unittest.TestCase):
    """Tests that SessionManager.strip_metadata handles role='model' (Fix 1 + Fix 4)."""

    @classmethod
    def setUpClass(cls):
        cls.sm, cls.config_path = _make_sm()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.config_path)

    def _strip(self, text):
        return self.sm.strip_metadata(text, "gemini")

    def test_model_role_extracted(self):
        """Gemini 'model' role messages must produce the AI response text."""
        line = json.dumps(
            {"type": "message", "role": "model", "content": "Hello from Gemini!"}
        )
        self.assertEqual(self._strip(line), "Hello from Gemini!")

    def test_assistant_role_still_works(self):
        """Existing 'assistant' role messages must still be extracted (backwards compat)."""
        line = json.dumps(
            {"type": "message", "role": "assistant", "content": "Hi there"}
        )
        self.assertEqual(self._strip(line), "Hi there")

    def test_user_role_not_echoed(self):
        """User messages must NOT appear in the strip_metadata output."""
        user_msg = json.dumps(
            {"type": "message", "role": "user", "content": "what happened?"}
        )
        model_msg = json.dumps(
            {"type": "message", "role": "model", "content": "Here is what happened."}
        )
        output = self._strip(user_msg + "\n" + model_msg)
        self.assertNotIn("what happened?", output)
        self.assertIn("Here is what happened.", output)

    def test_raw_user_json_not_echoed(self):
        """Bug scenario from #178: raw user-role JSON must not appear in output."""
        raw_user_json = json.dumps(
            {
                "type": "message",
                "timestamp": "2026-04-18T23:24:40.797Z",
                "role": "user",
                "content": "what happened?",
            }
        )
        result = self._strip(raw_user_json)
        self.assertNotIn('"role": "user"', result)
        self.assertNotIn("what happened?", result)

    def test_result_error_surfaced(self):
        """Gemini API error in result event must be surfaced (Fix 4)."""
        result_line = json.dumps(
            {"type": "result", "error": "RESOURCE_EXHAUSTED: Quota exceeded"}
        )
        output = self._strip(result_line)
        self.assertIn("[Gemini Error]", output)
        self.assertIn("RESOURCE_EXHAUSTED", output)

    def test_result_without_error_produces_no_output(self):
        """Normal result event (stats only) must not produce visible output."""
        result_line = json.dumps({"type": "result", "tokens": 123, "cost": 0.001})
        output = self._strip(result_line)
        self.assertEqual(output.strip(), "")

    def test_multiple_model_chunks_joined(self):
        """Multiple consecutive model messages must all appear in output."""
        lines = "\n".join(
            [
                json.dumps({"type": "message", "role": "model", "content": "Part 1"}),
                json.dumps({"type": "message", "role": "model", "content": "Part 2"}),
            ]
        )
        result = self._strip(lines)
        self.assertIn("Part 1", result)
        self.assertIn("Part 2", result)


class TestStreamingPathGeminiRole(unittest.TestCase):
    """
    Tests for the streaming path in _execute_subprocess_with_tracking (Fix 2 + Fix 3).

    A real subprocess outputs Gemini JSON lines; the test verifies that
    StreamBuffer receives the correct events — model messages become chunks,
    user-role messages are silently dropped instead of being echoed.
    """

    @classmethod
    def setUpClass(cls):
        cls.sm, cls.config_path = _make_sm()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.config_path)

    def _run_with_gemini_output(self, gemini_lines):
        """
        Run _execute_subprocess_with_tracking with a subprocess that writes the
        given Gemini JSON lines to stdout.  Returns the StreamBuffer populated
        during execution.
        """
        session_id = f"test-178-{threading.get_ident()}"
        buf = StreamBuffer()
        self.sm._stream_buffers[session_id] = buf
        # stream_info must be a truthy 2-tuple to activate the streaming code path
        self.sm._stream_queues[session_id] = (MagicMock(), MagicMock())

        output_text = "\n".join(gemini_lines) + "\n"
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
                runtime="gemini",
                agent="test-agent",
                prompt="test prompt",
                n8n_session_id=session_id,
            )

        self.sm._stream_buffers.pop(session_id, None)
        self.sm._stream_queues.pop(session_id, None)
        return buf

    def _chunk_text(self, buf):
        """Collect all chunk event payloads from a StreamBuffer as a single string."""
        return " ".join(str(data) for kind, data in buf.chunks if kind == "chunk")

    def test_model_role_produces_chunk(self):
        """Streaming: Gemini 'model' role must produce a chunk event."""
        buf = self._run_with_gemini_output(
            [json.dumps({"type": "message", "role": "model", "content": "AI says hi"})]
        )
        self.assertIn("AI says hi", self._chunk_text(buf))

    def test_assistant_role_produces_chunk(self):
        """Streaming: 'assistant' role still produces a chunk (backwards compat)."""
        buf = self._run_with_gemini_output(
            [
                json.dumps(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": "Compat response",
                    }
                )
            ]
        )
        self.assertIn("Compat response", self._chunk_text(buf))

    def test_user_role_message_not_echoed_as_chunk(self):
        """Fix 3: user-role message must be skipped, NOT pushed as a chunk."""
        user_json = json.dumps(
            {"type": "message", "role": "user", "content": "what happened?"}
        )
        model_json = json.dumps(
            {"type": "message", "role": "model", "content": "Answer here."}
        )
        buf = self._run_with_gemini_output([user_json, model_json])
        chunks = self._chunk_text(buf)
        self.assertNotIn(
            "what happened?",
            chunks,
            "User-role message was pushed as a chunk — echoing bug from #178 is present!",
        )
        self.assertIn("Answer here.", chunks)

    def test_init_event_not_pushed_as_chunk(self):
        """init events must be skipped (not pushed as chunks)."""
        buf = self._run_with_gemini_output(
            [
                json.dumps({"type": "init", "session": "abc123"}),
                json.dumps(
                    {"type": "message", "role": "model", "content": "Real response"}
                ),
            ]
        )
        chunks = self._chunk_text(buf)
        self.assertNotIn("abc123", chunks)
        self.assertIn("Real response", chunks)

    def test_result_event_not_pushed_as_chunk(self):
        """result events must be skipped in the streaming path."""
        buf = self._run_with_gemini_output(
            [json.dumps({"type": "result", "tokens": 99, "cost": 0.001})]
        )
        chunks = self._chunk_text(buf)
        self.assertNotIn('"tokens"', chunks)


if __name__ == "__main__":
    unittest.main(verbosity=2)
