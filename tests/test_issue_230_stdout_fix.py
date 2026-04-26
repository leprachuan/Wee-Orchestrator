"""
Regression test for Issue #230 — Claude stdout stream-json log parsing.

The bug: When a Claude background task runs with --output-format stream-json,
stdout lines are raw JSON objects like {"type":"assistant","message":{...}}.
These were previously appended directly to output_lines for the live log view,
making the live log display unreadable JSON blobs.

The fix: Parse Claude stdout stream-json events and extract human-readable text
before appending to output_lines:
  - "assistant" events: extract text content blocks
  - "system" events: skip (init metadata, not user-visible)
  - "stream_event" events: skip (already handled separately or via stderr)
  - "result" events: skip success (surfaced separately); show error text
  - Non-JSON lines: pass through unchanged (other runtimes unchanged)
"""

import json


def _extract_claude_stdout_log_line(line_text, runtime="claude"):
    """
    Replicate the Issue #230 stdout log extraction logic from agent_manager.py.
    Returns the human-readable line to append, or None if the line should be skipped.
    """
    _live_line = line_text
    if runtime == "claude" and line_text.strip().startswith("{"):
        try:
            obj = json.loads(line_text.strip())
            t = obj.get("type", "")
            if t == "assistant":
                parts = []
                for blk in (obj.get("message") or {}).get("content", []):
                    if blk.get("type") == "text" and blk.get("text"):
                        parts.append(blk["text"].rstrip())
                _live_line = "\n".join(parts).strip() if parts else None
            elif t in ("system", "stream_event"):
                _live_line = None
            elif t == "result":
                if obj.get("is_error"):
                    _live_line = obj.get("result", "").strip() or None
                else:
                    _live_line = None
        except (ValueError, KeyError, TypeError):
            pass  # Not valid JSON -- keep original line
    return _live_line


class TestIssue230ClaudeStdoutParsing:
    """Verify stdout stream-json parsing for Claude background task live log."""

    def test_assistant_text_block_extracted(self):
        """Assistant message with text content should produce readable text."""
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Hello, I am working on this."}
                    ],
                },
                "session_id": "abc123",
            }
        )
        result = _extract_claude_stdout_log_line(line)
        assert result == "Hello, I am working on this."

    def test_assistant_multi_text_blocks_joined(self):
        """Multiple text blocks in assistant message should be joined with newline."""
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "First paragraph."},
                        {"type": "text", "text": "Second paragraph."},
                    ],
                },
            }
        )
        result = _extract_claude_stdout_log_line(line)
        assert result == "First paragraph.\nSecond paragraph."

    def test_assistant_tool_use_only_block_returns_none(self):
        """Assistant message with only tool_use blocks (no text).

        Returns None (skip).
        """
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu1",
                            "name": "Bash",
                            "input": {"command": "ls"},
                        }
                    ],
                },
            }
        )
        result = _extract_claude_stdout_log_line(line)
        assert result is None

    def test_system_init_event_skipped(self):
        """System init event (metadata) should be skipped — returns None."""
        line = json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "abc123",
                "tools": [],
                "cwd": "/opt",
            }
        )
        result = _extract_claude_stdout_log_line(line)
        assert result is None

    def test_stream_event_skipped(self):
        """stream_event lines on stdout should be skipped — returns None."""
        line = json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "hi"},
                },
            }
        )
        result = _extract_claude_stdout_log_line(line)
        assert result is None

    def test_result_success_skipped(self):
        """Successful result event should be skipped (output shown separately)."""
        line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "Task complete.",
                "is_error": False,
                "session_id": "abc123",
            }
        )
        result = _extract_claude_stdout_log_line(line)
        assert result is None

    def test_result_error_shown(self):
        """Error result event should surface the error text."""
        line = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_generation",
                "result": "API Error: rate_limit_error",
                "is_error": True,
                "session_id": "abc123",
            }
        )
        result = _extract_claude_stdout_log_line(line)
        assert result == "API Error: rate_limit_error"

    def test_non_json_line_passed_through(self):
        """Non-JSON lines (plain text output) should pass through unchanged."""
        line = "Plain text output from Claude"
        result = _extract_claude_stdout_log_line(line)
        assert result == "Plain text output from Claude"

    def test_non_claude_runtime_passes_through_json(self):
        """Non-Claude runtimes: even JSON lines pass through unchanged."""
        line = '{"type":"tool_use","tool_name":"shell","command":"ls"}'
        result = _extract_claude_stdout_log_line(line, runtime="gemini")
        assert result == line

    def test_malformed_json_passed_through(self):
        """Malformed JSON that starts with { should pass through unchanged."""
        line = "{not valid json"
        result = _extract_claude_stdout_log_line(line, runtime="claude")
        assert result == line

    def test_assistant_empty_content_list_returns_none(self):
        """Assistant message with empty content list should return None (skip)."""
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": []},
            }
        )
        result = _extract_claude_stdout_log_line(line)
        assert result is None

    def test_regression_raw_json_not_in_live_log(self):
        """
        Regression test: the live log should NOT contain raw Claude JSON events.
        Simulate the stdout processing logic and verify output_lines only has
        human-readable content.
        """
        # Simulate Claude background task stdout lines
        stdout_lines = [
            json.dumps(
                {"type": "system", "subtype": "init", "session_id": "s1", "tools": []}
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "I'll check the file."}]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_start",
                        "content_block": {"type": "tool_use", "name": "Read"},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "File contents here.",
                    "is_error": False,
                }
            ),
        ]

        output_lines = []
        for raw_line in stdout_lines:
            parsed = _extract_claude_stdout_log_line(raw_line, runtime="claude")
            if parsed:
                output_lines.append(parsed)

        # Only the assistant text should appear in the live log
        assert output_lines == ["I'll check the file."]

        # Raw JSON blobs must NOT appear
        for line in output_lines:
            assert not line.strip().startswith("{"), f"Raw JSON in live log: {line}"
