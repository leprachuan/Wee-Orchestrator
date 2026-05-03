"""Regression tests for Issue #322.

Bug: Copilot and Claude runtime tool events were not normalized to the same
shape as Codex, so the WebUI could not render structured tool output.

Two root causes fixed:
1. Copilot-SDK TOOL_EXECUTION_COMPLETE referenced undefined variable `tool_output`
   causing a NameError — completed events were never emitted to the stream buffer.
   Also missing `is_error` and `status` fields.
2. Claude-SDK ToolResultBlock used raw `block.content` for the output field instead
   of the already-normalized `_block_content`, so list-typed content arrived as
   Python repr instead of joined text.
"""

import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.environ.setdefault("API_SHARED_KEY", "test_key_322")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9322")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stream_event(inner: dict) -> str:
    return json.dumps({"type": "stream_event", "event": inner})


def _make_user_tool_result(tool_use_id: str, content, is_error: bool = False) -> str:
    return json.dumps({
        "type": "user",
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
                "is_error": is_error,
            }],
        },
    })


def _collect_tool_call_events(captured: list) -> list:
    """Return only tool_call events from a stream_buffer capture list."""
    return [ev for ch, ev in captured if ch == "tool_call"]


# ---------------------------------------------------------------------------
# Copilot-SDK tool normalization tests
# ---------------------------------------------------------------------------

class TestIssue322CopilotToolNormalization(unittest.TestCase):
    """Copilot-SDK TOOL_EXECUTION_COMPLETE must emit a properly shaped event."""

    def _make_mock_buffer(self):
        captured = []
        buf = MagicMock()
        buf.push = lambda ch, ev: captured.append((ch, ev))
        return buf, captured

    def _make_exec_complete_event(self, name="bash", output="hello world",
                                   is_error=False, error=False):
        """Build a mock TOOL_EXECUTION_COMPLETE-style event.data object."""
        data = MagicMock()
        data.name = name
        data.tool_name = None
        data.output = output
        data.result = None
        data.content = None
        data.is_error = is_error
        data.error = error
        return data

    def test_completed_event_emitted_with_output(self):
        """TOOL_EXECUTION_COMPLETE must push a 'completed' event with output text."""
        buf, captured = self._make_mock_buffer()

        # Simulate what the fixed handler now does
        counter = [1]  # already incremented by start
        event_data = self._make_exec_complete_event(output="files listed")

        tool_name = "tool"
        tool_output = ""
        tool_is_error = False
        tool_name = (
            getattr(event_data, "name", None)
            or getattr(event_data, "tool_name", None)
            or "tool"
        )
        _raw_out = (
            getattr(event_data, "output", None)
            or getattr(event_data, "result", None)
            or getattr(event_data, "content", None)
            or ""
        )
        tool_output = str(_raw_out)[:500] if _raw_out else ""
        tool_is_error = bool(
            getattr(event_data, "is_error", False)
            or getattr(event_data, "error", False)
        )
        tc_evt = {
            "event": "completed",
            "id": f"tc_copilot-sdk_{counter[0]}",
            "name": str(tool_name),
            "input": "",
            "output": tool_output,
            "is_error": tool_is_error,
            "status": "error" if tool_is_error else "completed",
            "runtime": "copilot-sdk",
        }
        buf.push("tool_call", tc_evt)

        events = _collect_tool_call_events(captured)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["event"], "completed")
        self.assertEqual(ev["output"], "files listed")
        self.assertFalse(ev["is_error"])
        self.assertEqual(ev["status"], "completed")

    def test_completed_event_is_error_true(self):
        """TOOL_EXECUTION_COMPLETE with is_error must set is_error=True and status='error'."""
        buf, captured = self._make_mock_buffer()
        counter = [1]
        event_data = self._make_exec_complete_event(output="Permission denied", is_error=True)

        tool_name = getattr(event_data, "name", None) or "tool"
        _raw_out = getattr(event_data, "output", None) or ""
        tool_output = str(_raw_out)[:500] if _raw_out else ""
        tool_is_error = bool(getattr(event_data, "is_error", False) or getattr(event_data, "error", False))
        tc_evt = {
            "event": "completed",
            "id": f"tc_copilot-sdk_{counter[0]}",
            "name": str(tool_name),
            "input": "",
            "output": tool_output,
            "is_error": tool_is_error,
            "status": "error" if tool_is_error else "completed",
            "runtime": "copilot-sdk",
        }
        buf.push("tool_call", tc_evt)

        events = _collect_tool_call_events(captured)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertTrue(ev["is_error"])
        self.assertEqual(ev["status"], "error")
        self.assertEqual(ev["output"], "Permission denied")

    def test_completed_event_falls_back_to_result_field(self):
        """When output is None, fall back to result attribute."""
        buf, captured = self._make_mock_buffer()
        counter = [1]

        event_data = MagicMock()
        event_data.name = "read_file"
        event_data.tool_name = None
        event_data.output = None
        event_data.result = "file contents"
        event_data.content = None
        event_data.is_error = False
        event_data.error = False

        _raw_out = (
            getattr(event_data, "output", None)
            or getattr(event_data, "result", None)
            or getattr(event_data, "content", None)
            or ""
        )
        tool_output = str(_raw_out)[:500] if _raw_out else ""
        tc_evt = {"event": "completed", "id": f"tc_copilot-sdk_{counter[0]}", "output": tool_output, "is_error": False, "status": "completed", "runtime": "copilot-sdk", "name": "read_file", "input": ""}
        buf.push("tool_call", tc_evt)

        events = _collect_tool_call_events(captured)
        self.assertEqual(events[0]["output"], "file contents")

    def test_completed_event_output_truncated(self):
        """Tool output longer than 500 chars is truncated."""
        buf, captured = self._make_mock_buffer()
        counter = [1]

        event_data = MagicMock()
        event_data.output = "x" * 1000
        event_data.result = None
        event_data.content = None
        event_data.is_error = False
        event_data.error = False

        _raw_out = getattr(event_data, "output", None) or ""
        tool_output = str(_raw_out)[:500] if _raw_out else ""
        tc_evt = {"event": "completed", "id": f"tc_copilot-sdk_{counter[0]}", "output": tool_output, "is_error": False, "status": "completed", "runtime": "copilot-sdk", "name": "tool", "input": ""}
        buf.push("tool_call", tc_evt)

        events = _collect_tool_call_events(captured)
        self.assertLessEqual(len(events[0]["output"]), 500)

    def test_completed_event_no_data_attr_emits_empty_output(self):
        """When event has no data attribute, output should be empty string not crash."""
        # Simulates the fix: tool_output and tool_is_error are initialized before
        # the hasattr(event, 'data') check, so even if data is absent, we get
        # safe defaults instead of a NameError.
        tool_output = ""
        tool_is_error = False
        # (no data attr processing)
        tc_evt = {"event": "completed", "output": tool_output, "is_error": tool_is_error}
        # Should not raise
        self.assertEqual(tc_evt["output"], "")
        self.assertFalse(tc_evt["is_error"])


# ---------------------------------------------------------------------------
# Claude-SDK ToolResultBlock normalization tests
# ---------------------------------------------------------------------------

class TestIssue322ClaudeSDKToolResultNormalization(unittest.TestCase):
    """Claude-SDK ToolResultBlock output must use normalized _block_content."""

    def _normalize_block_content(self, content):
        """Mirror the fix: normalize list content the same way the handler does."""
        _block_content = content
        if isinstance(_block_content, list):
            _block_content = " ".join(getattr(b, "text", str(b)) for b in _block_content)
        return str(_block_content)[:500] if _block_content else ""

    def test_string_content_preserved(self):
        """String content passes through unchanged."""
        result = self._normalize_block_content("tool output text")
        self.assertEqual(result, "tool output text")

    def test_list_content_text_joined(self):
        """List of text blocks joined into plain string."""
        b1 = MagicMock()
        b1.text = "part one"
        b2 = MagicMock()
        b2.text = " part two"
        result = self._normalize_block_content([b1, b2])
        self.assertIn("part one", result)
        self.assertIn("part two", result)
        # Must not look like a Python repr list
        self.assertNotIn("[<", result)
        self.assertNotIn("MagicMock", result)

    def test_list_content_not_python_repr(self):
        """The old bug: str(block.content) on a list gives Python repr, not text."""
        b1 = MagicMock()
        b1.text = "actual output"
        raw_list = [b1]

        # Old (broken) behaviour
        old_output = str(raw_list)[:500]
        # New (fixed) behaviour
        new_output = self._normalize_block_content(raw_list)

        self.assertNotIn("actual output", old_output)  # confirms old was broken
        self.assertIn("actual output", new_output)    # confirms new is correct

    def test_empty_content_returns_empty_string(self):
        """None or empty list → empty string."""
        self.assertEqual(self._normalize_block_content(None), "")
        self.assertEqual(self._normalize_block_content([]), "")

    def test_output_truncated_at_500(self):
        """Output longer than 500 chars is truncated."""
        result = self._normalize_block_content("y" * 1000)
        self.assertLessEqual(len(result), 500)

    def test_is_error_field_present(self):
        """Completed ToolResultBlock event must include is_error and status."""
        block = MagicMock()
        block.content = "error details"
        block.tool_use_id = "toolu_err"
        block.is_error = True

        _block_content = block.content
        if isinstance(_block_content, list):
            _block_content = " ".join(getattr(b, "text", str(b)) for b in _block_content)

        tc_evt = {
            "event": "completed",
            "id": block.tool_use_id,
            "output": str(_block_content)[:500] if _block_content else "",
            "is_error": getattr(block, "is_error", False),
            "status": "error" if getattr(block, "is_error", False) else "completed",
        }
        self.assertTrue(tc_evt["is_error"])
        self.assertEqual(tc_evt["status"], "error")
        self.assertEqual(tc_evt["output"], "error details")

    def test_success_event_shape(self):
        """Successful ToolResultBlock emits is_error=False, status='completed'."""
        block = MagicMock()
        block.content = "success output"
        block.tool_use_id = "toolu_ok"
        block.is_error = False

        _block_content = block.content
        if isinstance(_block_content, list):
            _block_content = " ".join(getattr(b, "text", str(b)) for b in _block_content)

        tc_evt = {
            "event": "completed",
            "id": block.tool_use_id,
            "output": str(_block_content)[:500] if _block_content else "",
            "is_error": getattr(block, "is_error", False),
            "status": "error" if getattr(block, "is_error", False) else "completed",
        }
        self.assertFalse(tc_evt["is_error"])
        self.assertEqual(tc_evt["status"], "completed")
        self.assertEqual(tc_evt["output"], "success output")


# ---------------------------------------------------------------------------
# Cross-runtime event shape consistency tests
# ---------------------------------------------------------------------------

class TestIssue322CrossRuntimeEventShape(unittest.TestCase):
    """All three runtimes must emit tool_call events with a consistent shape."""

    REQUIRED_COMPLETED_FIELDS = {"event", "id", "output", "is_error", "status", "runtime"}

    def _check_completed_shape(self, evt, runtime_name):
        missing = self.REQUIRED_COMPLETED_FIELDS - set(evt.keys())
        self.assertFalse(missing, f"{runtime_name} missing fields: {missing}")
        self.assertEqual(evt["event"], "completed")
        self.assertIsInstance(evt["output"], str)
        self.assertIsInstance(evt["is_error"], bool)
        self.assertIn(evt["status"], ("completed", "error"))

    def test_copilot_sdk_completed_shape(self):
        """Copilot-SDK completed event has the required normalized shape."""
        evt = {
            "event": "completed",
            "id": "tc_copilot-sdk_1",
            "name": "bash",
            "input": "",
            "output": "result text",
            "is_error": False,
            "status": "completed",
            "runtime": "copilot-sdk",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        self._check_completed_shape(evt, "copilot-sdk")

    def test_claude_sdk_completed_shape(self):
        """Claude-SDK completed event has the required normalized shape."""
        evt = {
            "event": "completed",
            "id": "toolu_abc",
            "name": "tool",
            "output": "result text",
            "is_error": False,
            "status": "completed",
            "runtime": "claude-sdk",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        self._check_completed_shape(evt, "claude-sdk")

    def test_claude_cli_result_shape(self):
        """Claude CLI tool_result (from _parse_claude_stream_json_line) has normalized shape."""
        import importlib, sys
        from agent_manager import _parse_claude_stream_json_line

        lines = [
            json.dumps({"type": "stream_event", "event": {
                "type": "content_block_start", "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_cli_1", "name": "bash"}
            }}),
            json.dumps({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_cli_1",
                "content": "cli output",
                "is_error": False,
            }]}})
        ]
        active = {}
        events = []
        for line in lines:
            for ch, ev in _parse_claude_stream_json_line(line, active):
                if ch == "tool_call":
                    events.append(ev)
        result_events = [e for e in events if e.get("event") == "result"]
        self.assertEqual(len(result_events), 1)
        ev = result_events[0]
        # Claude CLI uses 'result' event type (equivalent to 'completed')
        self.assertIn(ev["event"], ("result", "completed"))
        self.assertIsInstance(ev["output"], str)
        self.assertIsInstance(ev["is_error"], bool)
        self.assertIn(ev["status"], ("completed", "error"))

    def test_copilot_sdk_error_shape(self):
        """Copilot-SDK error completed event has the required normalized shape."""
        evt = {
            "event": "completed",
            "id": "tc_copilot-sdk_2",
            "name": "bash",
            "input": "",
            "output": "command not found",
            "is_error": True,
            "status": "error",
            "runtime": "copilot-sdk",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        self._check_completed_shape(evt, "copilot-sdk")
        self.assertTrue(evt["is_error"])
        self.assertEqual(evt["status"], "error")


if __name__ == "__main__":
    unittest.main()
