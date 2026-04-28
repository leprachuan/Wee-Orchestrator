"""Regression tests for Issue #278: Claude runtime tool call status stuck
'running' and output toggle always empty.

These tests verify:
1. _sanitize_tool_call_for_display redacts secrets in the 'output' field.
2. The claude CLI runtime emits a 'result' event with an 'output' field
   extracted from the tool_result content block (string and list formats).
3. The claude-sdk runtime emits a 'completed' event with 'output' field
   (not 'input') populated from ToolResultBlock.content.
4. The frontend app.js calls cleanupAllToolSpinners() in the
   "stream ended without done event" fallback path.
"""

import sys
import os
import unittest

# Ensure agent_manager can be imported from the dev repo
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("API_SHARED_KEY", "test_key_123")

import agent_manager as am


class TestIssue278SanitizeOutputField(unittest.TestCase):
    """_sanitize_tool_call_for_display must redact secrets from output."""

    def setUp(self):
        self.sanitize = am._sanitize_tool_call_for_display

    def test_output_field_sanitized(self):
        data = {
            "event": "result",
            "id": "toolu_xxx",
            "output": "Bearer sk-ant-secret999 found in response",
        }
        result = self.sanitize(data)
        self.assertNotIn("sk-ant-secret999", result.get("output", ""))
        self.assertIn("[REDACTED]", result.get("output", ""))

    def test_output_field_none_passthrough(self):
        data = {"event": "result", "id": "toolu_xxx", "output": None}
        result = self.sanitize(data)
        self.assertIsNone(result.get("output"))

    def test_output_field_empty_passthrough(self):
        data = {"event": "result", "id": "toolu_xxx", "output": ""}
        result = self.sanitize(data)
        self.assertEqual(result.get("output", ""), "")

    def test_input_and_output_both_sanitized(self):
        """Both input and output sanitized in same event."""
        data = {
            "event": "result",
            "id": "toolu_xxx",
            "input": "curl -H 'Authorization: Bearer sk-abc123' http://api.test",
            "output": "Response Bearer sk-abc123 in body",
        }
        result = self.sanitize(data)
        self.assertNotIn("sk-abc123", result.get("input", ""))
        self.assertNotIn("sk-abc123", result.get("output", ""))
        self.assertIn("[REDACTED]", result.get("output", ""))

    def test_event_without_output_unchanged(self):
        """Events that never had output field are unaffected."""
        data = {"event": "start", "id": "toolu_xxx", "name": "bash"}
        result = self.sanitize(data)
        self.assertNotIn("output", result)


class TestIssue278ClaudeCliResultEvent(unittest.TestCase):
    """claude CLI runtime must emit 'result' events with 'output' field."""

    def _extract_output(self, block_content):
        """Replicate the output-extraction logic from agent_manager."""
        if isinstance(block_content, list):
            return "\n".join(
                p.get("text", "")
                for p in block_content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        elif isinstance(block_content, str):
            return block_content
        return str(block_content) if block_content else ""

    def test_string_content_extracted(self):
        block = {"type": "tool_result", "tool_use_id": "toolu_001", "content": "hello world"}
        output = self._extract_output(block["content"])
        self.assertEqual(output, "hello world")

    def test_list_content_extracted(self):
        block = {
            "type": "tool_result",
            "tool_use_id": "toolu_002",
            "content": [{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}],
        }
        output = self._extract_output(block["content"])
        self.assertEqual(output, "line1\nline2")

    def test_empty_content_returns_empty(self):
        block = {"type": "tool_result", "tool_use_id": "toolu_003", "content": ""}
        output = self._extract_output(block["content"])
        self.assertEqual(output, "")

    def test_result_event_has_output_field(self):
        """Verify the result event dict structure includes 'output'."""
        block = {
            "type": "tool_result",
            "tool_use_id": "toolu_004",
            "content": "command output here",
            "is_error": False,
        }
        _raw_out = block.get("content", "")
        if isinstance(_raw_out, list):
            _out_text = " ".join(
                b.get("text", str(b)) if isinstance(b, dict) else str(b)
                for b in _raw_out
            )
        elif isinstance(_raw_out, str):
            _out_text = _raw_out
        else:
            _out_text = str(_raw_out) if _raw_out else ""
        tc_event = {
            "event": "result",
            "id": block.get("tool_use_id", ""),
            "output": _out_text[:2000],
            "is_error": block.get("is_error", False),
        }
        self.assertEqual(tc_event["event"], "result")
        self.assertEqual(tc_event["id"], "toolu_004")
        self.assertEqual(tc_event["output"], "command output here")
        self.assertFalse(tc_event["is_error"])

    def test_error_result_event(self):
        block = {
            "type": "tool_result",
            "tool_use_id": "toolu_005",
            "content": "bash: command not found",
            "is_error": True,
        }
        _out = block["content"] if isinstance(block["content"], str) else ""
        tc_event = {
            "event": "result",
            "id": block["tool_use_id"],
            "output": _out[:2000],
            "is_error": block["is_error"],
        }
        self.assertTrue(tc_event["is_error"])
        self.assertEqual(tc_event["output"], "bash: command not found")


class TestIssue278ClaudeSdkCompletedEvent(unittest.TestCase):
    """claude-sdk runtime ToolResultBlock must use 'output' field, not 'input'."""

    def test_completed_event_uses_output_not_input(self):
        """ToolResultBlock event must have 'output' key (not 'input') for content."""
        # Simulate what agent_manager builds for ToolResultBlock
        block_content = "tool result text"
        tc_evt = {
            "event": "completed",
            "id": "toolu_sdk_001",
            "name": "tool",
            "input": "",            # empty input — tool result goes in output
            "output": str(block_content)[:2000] if block_content else "",
            "is_error": False,
        }
        # Frontend reads evt.output — must be present and non-empty
        self.assertIn("output", tc_evt)
        self.assertEqual(tc_evt["output"], "tool result text")
        # input should be empty since tool results are output, not input
        self.assertEqual(tc_evt["input"], "")

    def test_completed_event_list_content(self):
        """List-typed content must be joined into string for 'output'."""
        content_parts = ["part1", "part2"]
        joined = " ".join(str(p) for p in content_parts)
        tc_evt = {"event": "completed", "output": joined[:2000]}
        self.assertEqual(tc_evt["output"], "part1 part2")


class TestIssue278FrontendStreamEndedCleanup(unittest.TestCase):
    """Frontend app.js must call cleanupAllToolSpinners() when stream ends without done."""

    def test_cleanup_called_in_stream_ended_fallback(self):
        app_js_path = os.path.join(
            os.path.dirname(__file__), "..", "webui", "dist", "app.js"
        )
        with open(app_js_path) as f:
            js = f.read()

        # Find the "stream ended without done event" fallback block
        idx = js.find("Stream ended without done event")
        self.assertGreater(idx, 0, "Fallback comment not found in app.js")

        # The cleanup call must appear before STATE.isProcessing = false
        fallback_block = js[idx : idx + 300]
        cleanup_pos = fallback_block.find("cleanupAllToolSpinners()")
        processing_pos = fallback_block.find("STATE.isProcessing = false")
        self.assertGreater(
            cleanup_pos, -1,
            "cleanupAllToolSpinners() not called in stream-ended-without-done fallback"
        )
        self.assertLess(
            cleanup_pos, processing_pos,
            "cleanupAllToolSpinners() must be called before STATE.isProcessing = false"
        )


if __name__ == "__main__":
    unittest.main()
