"""
Regression tests for issue #374: Codex runtime stream proxy forwards raw
protocol JSON instead of parsed text.

Root cause: After _parse_codex_transport_line processed a Codex JSON frame,
there was no `continue` to skip the generic text push — raw JSON fell through
and got pushed as chunk text. Also, dead code in the else branch referenced
undefined variables (_cx_text, _cx_type, _cx_obj).

Additionally, turn.failed and error events were silently suppressed instead
of being rendered as error messages.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_374")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9374")

from agent_manager import (  # noqa: E402
    SessionManager,
    _parse_codex_transport_line,
)
from session_manager_components import StreamBuffer  # noqa: E402


def _make_sm():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"agents": []}, tmp)
    tmp.close()
    return SessionManager(tmp.name), tmp.name


class TestParseCodexTransportLine(unittest.TestCase):
    """Unit tests for _parse_codex_transport_line protocol parsing."""

    def test_thread_started_suppressed(self):
        line = json.dumps({"type": "thread.started", "thread_id": "abc"})
        result = _parse_codex_transport_line(line)
        self.assertEqual(result, [])

    def test_turn_started_suppressed(self):
        line = json.dumps({"type": "turn.started"})
        result = _parse_codex_transport_line(line)
        self.assertEqual(result, [])

    def test_agent_message_extracts_text(self):
        line = json.dumps({
            "type": "item.completed",
            "item": {
                "id": "item_0",
                "type": "agent_message",
                "text": "Hello, world!",
            },
        })
        result = _parse_codex_transport_line(line)
        self.assertEqual(result, [("chunk", "Hello, world!")])

    def test_turn_failed_returns_error_chunk(self):
        line = json.dumps({
            "type": "turn.failed",
            "error": {"message": "model not supported"},
        })
        result = _parse_codex_transport_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        kind, text = result[0]
        self.assertEqual(kind, "chunk")
        self.assertIn("Codex turn failed", text)
        self.assertIn("model not supported", text)

    def test_error_event_returns_error_chunk(self):
        line = json.dumps({
            "type": "error",
            "message": "rate limit exceeded",
        })
        result = _parse_codex_transport_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        kind, text = result[0]
        self.assertEqual(kind, "chunk")
        self.assertIn("Codex error", text)
        self.assertIn("rate limit exceeded", text)

    def test_non_json_returns_none(self):
        result = _parse_codex_transport_line("plain text output")
        self.assertIsNone(result)

    def test_item_completed_tool_returns_tool_events(self):
        line = json.dumps({
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "function_call",
                "name": "shell",
                "arguments": "ls -la",
                "output": "file1.txt\nfile2.txt",
            },
        })
        result = _parse_codex_transport_line(line)
        self.assertIsNotNone(result)
        self.assertTrue(len(result) > 0)
        kinds = [k for k, _ in result]
        self.assertIn("tool_call", kinds)


class TestIssue374CodexStreamProxy(unittest.TestCase):
    """Codex stream proxy must not forward raw protocol JSON as text chunks."""

    @classmethod
    def setUpClass(cls):
        cls.sm, cls.config_path = _make_sm()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.config_path)

    def _run_with_codex_output(self, codex_lines):
        session_id = f"test-374-{threading.get_ident()}"
        buf = StreamBuffer()
        self.sm._stream_buffers[session_id] = buf
        self.sm._stream_queues[session_id] = (MagicMock(), MagicMock())

        output_text = "\n".join(codex_lines) + "\n"
        script = (
            "import sys; "
            f"sys.stdout.write({output_text!r}); "
            "sys.stdout.flush()"
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

    def _chunk_texts(self, buf):
        texts = []
        for kind, data in buf.chunks:
            if kind == "chunk":
                if isinstance(data, dict):
                    texts.append(data.get("text", ""))
                elif isinstance(data, str):
                    texts.append(data)
        return texts

    def test_raw_protocol_json_not_in_chunks(self):
        """The core bug: raw JSON frames must not appear as chunk text."""
        buf = self._run_with_codex_output([
            json.dumps({"type": "thread.started", "thread_id": "019ef663"}),
            json.dumps({"type": "turn.started"}),
            json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "agent_message",
                    "text": "Here is my response.",
                },
            }),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10}}),
        ])
        chunks = self._chunk_texts(buf)
        combined = " ".join(chunks)
        self.assertIn("Here is my response.", combined)
        self.assertNotIn("thread.started", combined)
        self.assertNotIn("turn.started", combined)
        self.assertNotIn("turn.completed", combined)
        self.assertNotIn("019ef663", combined)

    def test_metadata_frames_produce_no_text_chunks(self):
        """Metadata-only frames (thread/turn lifecycle) must not produce text."""
        buf = self._run_with_codex_output([
            json.dumps({"type": "thread.started", "thread_id": "abc"}),
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "response.started"}),
            json.dumps({"type": "response.completed"}),
            json.dumps({"type": "turn.completed"}),
            json.dumps({"type": "thread.completed"}),
        ])
        chunks = self._chunk_texts(buf)
        combined = "".join(chunks).strip()
        self.assertEqual(combined, "")

    def test_turn_failed_rendered_as_error(self):
        """turn.failed events must render as visible error text, not be suppressed."""
        buf = self._run_with_codex_output([
            json.dumps({"type": "thread.started", "thread_id": "abc"}),
            json.dumps({"type": "turn.started"}),
            json.dumps({
                "type": "turn.failed",
                "error": {"message": "The 'gpt-5-mini' model is not supported"},
            }),
        ])
        chunks = self._chunk_texts(buf)
        combined = " ".join(chunks)
        self.assertIn("gpt-5-mini", combined)
        self.assertIn("turn failed", combined.lower())

    def test_multiple_agent_messages_separated(self):
        """Consecutive agent_message chunks must be separated by paragraph breaks."""
        buf = self._run_with_codex_output([
            json.dumps({"type": "thread.started", "thread_id": "abc"}),
            json.dumps({"type": "turn.started"}),
            json.dumps({
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "First message."},
            }),
            json.dumps({
                "type": "item.completed",
                "item": {"id": "item_1", "type": "agent_message", "text": "Second message."},
            }),
        ])
        chunks = self._chunk_texts(buf)
        combined = "".join(chunks)
        self.assertIn("First message.", combined)
        self.assertIn("Second message.", combined)
        first_idx = combined.index("First message.")
        second_idx = combined.index("Second message.")
        between = combined[first_idx + len("First message."):second_idx]
        self.assertIn("\n\n", between)

    def test_status_update_extracted_from_agent_message(self):
        """[STATUS_UPDATE: ...] markers in agent_message text must be stripped."""
        buf = self._run_with_codex_output([
            json.dumps({"type": "thread.started", "thread_id": "abc"}),
            json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "agent_message",
                    "text": "[STATUS_UPDATE: Installing deps] Done installing.",
                },
            }),
        ])
        chunks = self._chunk_texts(buf)
        combined = " ".join(chunks)
        self.assertNotIn("STATUS_UPDATE", combined)
        self.assertIn("Done installing.", combined)


if __name__ == "__main__":
    unittest.main()


class TestIssue374BackgroundTaskCodexParsing(unittest.TestCase):
    """Background tasks must parse Codex JSONL and not forward raw protocol JSON."""

    def test_codex_bg_protocol_frames_excluded_from_output(self):
        """Codex protocol frames should be parsed, not included as raw output."""
        from agent_manager import _parse_codex_transport_line

        lines = [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "turn.started"}),
            json.dumps({
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "bg result"},
            }),
            json.dumps({"type": "turn.completed"}),
        ]
        parsed_texts = []
        for line in lines:
            events = _parse_codex_transport_line(line.strip())
            if events is not None:
                for kind, data in events:
                    if kind == "chunk" and isinstance(data, str):
                        parsed_texts.append(data)
        self.assertIn("bg result", " ".join(parsed_texts))
        combined = " ".join(parsed_texts)
        self.assertNotIn("thread.started", combined)
        self.assertNotIn("turn.started", combined)


class TestIssue374RuntimeAwareModelDefault(unittest.TestCase):
    """get_default_model must return a valid model for each runtime."""

    def test_codex_default_not_gpt5_mini(self):
        from agent_manager import get_default_model

        model = get_default_model("codex")
        self.assertNotEqual(model, "gpt-5-mini")
        self.assertIn("gpt-5", model)

    def test_copilot_default_is_gpt5_mini(self):
        from agent_manager import get_default_model

        model = get_default_model("copilot")
        self.assertEqual(model, "gpt-5-mini")

    def test_no_runtime_returns_env_default(self):
        from agent_manager import get_default_model

        model = get_default_model()
        self.assertEqual(model, "gpt-5-mini")

    def test_claude_default(self):
        from agent_manager import get_default_model

        model = get_default_model("claude")
        self.assertEqual(model, "haiku")
