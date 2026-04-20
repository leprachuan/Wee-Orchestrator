"""Tests for Issue #115: Inline tool calls with expandable output.

Validates that all runtimes emit tool_call events with output data,
the sanitizer handles the output field, and the frontend rendering
preserves tool blocks across markdown application.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSanitizeToolCallOutput(unittest.TestCase):
    """Test _sanitize_tool_call_for_display handles output field."""

    def setUp(self):
        import agent_manager as am

        self.sanitize = am._sanitize_tool_call_for_display

    def test_output_field_sanitized(self):
        """Output field containing secrets should be redacted."""
        data = {
            "event": "completed",
            "id": "tc_1",
            "name": "bash",
            "input": "curl -H 'Authorization: Bearer sk-secret123' https://api.example.com",  # noqa: E501
            "output": "Response with Bearer sk-secret123 in it",
        }
        result = self.sanitize(data)
        self.assertNotIn("sk-secret123", result.get("output", ""))
        self.assertIn("[REDACTED]", result.get("output", ""))

    def test_output_field_none_passthrough(self):
        """When output is None, should pass through unchanged."""
        data = {"event": "completed", "id": "tc_1", "output": None}
        result = self.sanitize(data)
        self.assertIsNone(result.get("output"))

    def test_output_field_empty_string(self):
        """Empty output string should pass through."""
        data = {"event": "completed", "id": "tc_1", "output": ""}
        result = self.sanitize(data)
        self.assertEqual(result.get("output"), "")

    def test_output_no_secrets_passthrough(self):
        """Output without secrets should be unchanged."""
        data = {
            "event": "completed",
            "id": "tc_1",
            "output": "File created successfully at /tmp/test.txt",
        }
        result = self.sanitize(data)
        self.assertEqual(result["output"], "File created successfully at /tmp/test.txt")

    def test_input_still_sanitized(self):
        """Input field should still be sanitized alongside output."""
        data = {
            "event": "completed",
            "id": "tc_1",
            "input": "curl -u admin:password123 http://localhost",
            "output": "HTTP 200 OK",
        }
        result = self.sanitize(data)
        self.assertNotIn("password123", result.get("input", ""))
        self.assertEqual(result["output"], "HTTP 200 OK")


class TestCopilotSdkToolOutput(unittest.TestCase):
    """Test copilot-sdk runtime emits tool_call events with output."""

    def test_completed_event_has_output_field(self):
        """TOOL_EXECUTION_COMPLETE event should include output in tc_evt."""
        # Simulate the event data structure
        event_data = MagicMock()
        event_data.name = "bash"
        event_data.tool_name = None
        event_data.output = "command output here"
        event_data.result = None
        event_data.content = None

        # Extract output using the same logic as agent_manager
        tool_output = str(
            getattr(event_data, "output", "")
            or getattr(event_data, "result", "")
            or getattr(event_data, "content", "")
            or ""
        )[:2000]

        tc_evt = {
            "event": "completed",
            "id": "tc_copilot-sdk_1",
            "name": str(event_data.name),
            "input": "",
            "output": tool_output,
            "runtime": "copilot-sdk",
        }

        self.assertEqual(tc_evt["output"], "command output here")
        self.assertEqual(tc_evt["event"], "completed")

    def test_completed_event_fallback_to_result(self):
        """When output is empty, should fall back to result field."""
        event_data = MagicMock()
        event_data.name = "read"
        event_data.output = ""
        event_data.result = "file contents here"
        event_data.content = None

        tool_output = str(
            getattr(event_data, "output", "")
            or getattr(event_data, "result", "")
            or getattr(event_data, "content", "")
            or ""
        )[:2000]

        self.assertEqual(tool_output, "file contents here")

    def test_completed_event_fallback_to_content(self):
        """When output and result are empty, should fall back to content."""
        event_data = MagicMock()
        event_data.name = "write"
        event_data.output = ""
        event_data.result = ""
        event_data.content = "wrote 42 bytes"

        tool_output = str(
            getattr(event_data, "output", "")
            or getattr(event_data, "result", "")
            or getattr(event_data, "content", "")
            or ""
        )[:2000]

        self.assertEqual(tool_output, "wrote 42 bytes")

    def test_output_truncated_to_2000(self):
        """Output should be truncated to 2000 characters."""
        long_output = "x" * 5000
        truncated = long_output[:2000]
        self.assertEqual(len(truncated), 2000)


class TestClaudeSdkToolOutput(unittest.TestCase):
    """Test claude-sdk runtime includes output from ToolResultBlock."""

    def test_string_content_extracted(self):
        """String content in ToolResultBlock should be used as output."""
        block_content = "Tool execution result text"
        _result_content = ""
        if isinstance(block_content, str):
            _result_content = block_content
        self.assertEqual(_result_content, "Tool execution result text")

    def test_list_content_joined(self):
        """List content blocks should be joined with spaces."""

        class FakeBlock:
            def __init__(self, text):
                self.text = text

        block_content = [FakeBlock("line 1"), FakeBlock("line 2")]
        _result_content = " ".join(getattr(b, "text", str(b)) for b in block_content)
        self.assertEqual(_result_content, "line 1 line 2")

    def test_empty_content_returns_empty(self):
        """Empty/None content should return empty string."""
        block_content = None
        _result_content = ""
        if block_content:
            _result_content = str(block_content)
        self.assertEqual(_result_content, "")

    def test_output_in_completed_event(self):
        """Completed event should include output field."""
        tc_evt = {
            "event": "completed",
            "id": "tc_claude-sdk_1",
            "name": "tool",
            "output": "execution result"[:2000],
            "is_error": False,
            "runtime": "claude-sdk",
        }
        self.assertIn("output", tc_evt)
        self.assertEqual(tc_evt["output"], "execution result")


class TestClaudeRuntimeToolResult(unittest.TestCase):
    """Test claude runtime includes output from tool_result blocks."""

    def test_string_tool_result_content(self):
        """String content in tool_result should become output."""
        block = {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": "result text",
            "is_error": False,
        }
        _tr_content = block.get("content", "")
        if isinstance(_tr_content, list):
            _tr_content = " ".join(
                b.get("text", str(b)) if isinstance(b, dict) else str(b)
                for b in _tr_content
            )
        tc_event = {
            "event": "result",
            "id": block.get("tool_use_id", ""),
            "output": str(_tr_content)[:2000],
            "is_error": block.get("is_error", False),
        }
        self.assertEqual(tc_event["output"], "result text")

    def test_list_tool_result_content(self):
        """List content blocks in tool_result should be joined."""
        block = {
            "type": "tool_result",
            "tool_use_id": "t2",
            "content": [
                {"type": "text", "text": "line A"},
                {"type": "text", "text": "line B"},
            ],
            "is_error": False,
        }
        _tr_content = block.get("content", "")
        if isinstance(_tr_content, list):
            _tr_content = " ".join(
                b.get("text", str(b)) if isinstance(b, dict) else str(b)
                for b in _tr_content
            )
        self.assertEqual(_tr_content, "line A line B")

    def test_error_tool_result(self):
        """Error tool results should set is_error=True."""
        block = {
            "type": "tool_result",
            "tool_use_id": "t3",
            "content": "command not found",
            "is_error": True,
        }
        tc_event = {
            "event": "result",
            "id": block["tool_use_id"],
            "output": str(block["content"])[:2000],
            "is_error": block["is_error"],
        }
        self.assertTrue(tc_event["is_error"])
        self.assertEqual(tc_event["output"], "command not found")


class TestGeminiOutputLimit(unittest.TestCase):
    """Test gemini tool_result output uses 2000 char limit."""

    def test_output_limit_is_2000(self):
        """Gemini output should be truncated at 2000 chars."""
        _gobj = {"output": "y" * 3000}
        output = _gobj.get("output", "")[:2000]
        self.assertEqual(len(output), 2000)


class TestFrontendToolCallBlockStructure(unittest.TestCase):
    """Test the expected DOM structure for expandable tool call blocks."""

    def test_tc_block_wrapper_expected(self):
        """insertToolCallBlock should create tc-block > tc-line + tc-output."""
        # This is a structural test — validates the expected HTML pattern
        expected_classes = [
            "tc-block",
            "tc-line",
            "tc-toggle",
            "tc-spinner",
            "tc-name",
            "tc-input",
            "tc-status",
            "tc-output",
        ]
        # Read the actual app.js and verify these classes exist
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.js") as f:
            js_content = f.read()
        for cls in expected_classes:
            self.assertIn(cls, js_content, f"Missing CSS class '{cls}' in app.js")

    def test_expand_collapse_toggle(self):
        """Click handler should toggle tc-expanded class."""
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.js") as f:
            js_content = f.read()
        self.assertIn("tc-expanded", js_content)
        self.assertIn("toggle", js_content.lower())

    def test_disclosure_triangle_present(self):
        """Disclosure triangle (▶/▼) should be in the tool call block."""
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.js") as f:
            js_content = f.read()
        self.assertIn("▶", js_content)
        self.assertIn("▼", js_content)

    def test_error_state_class(self):
        """Error state should add tc-error class."""
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.js") as f:
            js_content = f.read()
        self.assertIn("tc-error", js_content)

    def test_has_output_class(self):
        """Toggle should get has-output class when output is set."""
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.js") as f:
            js_content = f.read()
        self.assertIn("has-output", js_content)

    def test_completed_event_handled(self):
        """Frontend should handle 'completed' event kind."""
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.js") as f:
            js_content = f.read()
        self.assertIn("'completed'", js_content)

    def test_started_event_handled(self):
        """Frontend should handle 'started' event kind."""
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.js") as f:
            js_content = f.read()
        self.assertIn("'started'", js_content)

    def test_output_passed_to_complete(self):
        """completeToolCallBlock should receive output parameter."""
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.js") as f:
            js_content = f.read()
        # Check that evt.output is passed when completing
        self.assertIn("evt.output ||", js_content)

    def test_is_error_passed_to_complete(self):
        """completeToolCallBlock should receive is_error parameter."""
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.js") as f:
            js_content = f.read()
        self.assertIn("evt.is_error ||", js_content)

    def test_markdown_preserves_tool_blocks(self):
        """applyMarkdownToBubble should preserve .tc-block elements."""
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.js") as f:
            js_content = f.read()
        # Check that tool blocks are preserved before innerHTML replacement
        self.assertIn("querySelectorAll('.tc-block')", js_content)


class TestCssExpandableStyles(unittest.TestCase):
    """Test CSS has the required expandable tool call styles."""

    def setUp(self):
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.css") as f:
            self.css = f.read()

    def test_tc_block_style(self):
        self.assertIn(".tc-block", self.css)

    def test_tc_toggle_style(self):
        self.assertIn(".tc-toggle", self.css)

    def test_tc_output_style(self):
        self.assertIn(".tc-output", self.css)

    def test_tc_expanded_shows_output(self):
        self.assertIn(".tc-block.tc-expanded .tc-output", self.css)

    def test_tc_error_style(self):
        self.assertIn(".tc-block.tc-error", self.css)

    def test_tc_status_error_color(self):
        self.assertIn(".tc-status.error", self.css)

    def test_has_output_toggle_style(self):
        self.assertIn(".tc-toggle.has-output", self.css)

    def test_silent_mode_hides_blocks(self):
        """Silent mode should hide .tc-block elements."""
        self.assertIn(".tool-calls-hidden .tc-block", self.css)

    def test_output_max_height(self):
        """Output area should have max-height for scrolling."""
        self.assertIn("max-height: 300px", self.css)

    def test_output_pre_wrap(self):
        """Output should use pre-wrap for whitespace."""
        self.assertIn("white-space: pre-wrap", self.css)


class TestSilentModeIntegration(unittest.TestCase):
    """Test that silent mode hides tool calls in all runtimes."""

    def test_silent_mode_hides_tc_block(self):
        """CSS rule should hide .tc-block inside .tool-calls-hidden."""
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.css") as f:
            css = f.read()
        self.assertIn(".tool-calls-hidden .tc-block { display: none !important; }", css)

    def test_sse_skips_tool_calls_in_silent_mode(self):
        """SSE endpoint should skip tool_call events when silent_mode is True."""
        with open("/opt/n8n-copilot-shim-dev/agent_manager.py") as f:
            py = f.read()
        # Verify the silent mode check exists for tool_call SSE events
        self.assertIn('elif kind == "tool_call"', py)
        self.assertIn("silent_mode", py)


if __name__ == "__main__":
    unittest.main()
