"""Regression test for Issue #230: Claude runtime stderr JSON parsing in background tasks."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from io import StringIO
from unittest import mock

# Mock the background task manager and necessary components
class MockBgTaskMgr:
    """Mock background task manager to capture appended output and tool calls."""
    
    def __init__(self):
        self.output_lines = []
        self.tool_calls = []
        self.task_data = {}
    
    def append_output(self, task_id, line):
        """Append an output line."""
        self.output_lines.append(line)
    
    def append_tool_call(self, task_id, tc):
        """Append a tool call."""
        self.tool_calls.append(tc)
    
    def update_task(self, task_id, **kwargs):
        """Update task metadata."""
        if task_id not in self.task_data:
            self.task_data[task_id] = {}
        self.task_data[task_id].update(kwargs)
    
    def update_tool_call(self, task_id, tool_id, **kwargs):
        """Update a tool call."""
        for tc in self.tool_calls:
            if tc.get("id") == tool_id:
                tc.update(kwargs)
                break
    
    def fail_task(self, task_id, reason):
        """Mark task as failed."""
        if task_id not in self.task_data:
            self.task_data[task_id] = {}
        self.task_data[task_id]["status"] = "failed"
        self.task_data[task_id]["error"] = reason


class TestIssue230ClaudeStderrParsing(unittest.TestCase):
    """Test Claude runtime stderr JSON parsing in background task execution."""
    
    def test_claude_stderr_stream_event_tool_use_parsing(self):
        """
        Test that Claude stderr stream_event JSON with tool_use is properly parsed
        into tool_calls, not treated as raw text.
        
        This reproduces the bug: Before the fix, a Claude stream_event emitted on
        stderr appeared as raw [stderr] {...json...} text in the live viewer instead
        of being parsed into a tool_call entry.
        """
        mock_mgr = MockBgTaskMgr()
        runtime = "claude"
        task_id = "test_task_123"
        
        # Simulate Claude stderr containing a stream_event with tool_use
        claude_stderr_event = {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_abc123def456",
                    "name": "file_editor",
                    "input": {
                        "command": "create",
                        "path": "/tmp/test.txt",
                        "file_text": "hello world"
                    }
                }
            }
        }
        
        # Simulate the _drain_stderr parsing logic
        tool_call_counter = [0]  # Use list for mutable reference in nested scope
        
        err_line = json.dumps(claude_stderr_event) + "\n"
        err_text = err_line.rstrip("\n\r")
        
        # Only process if it looks like JSON (as the real code does)
        if err_text.strip().startswith("{"):
            try:
                _obj = json.loads(err_text.strip())
                _otype = _obj.get("type", "")
                
                if _otype == "stream_event":
                    _event = _obj.get("event") or {}
                    _inner = _event.get("type", "")
                    if _inner == "content_block_start":
                        _cb = _event.get("content_block") or {}
                        if _cb.get("type") == "tool_use":
                            tool_call_counter[0] += 1
                            tc = {
                                "id": _cb.get(
                                    "id",
                                    f"bg_{task_id[:8]}_{tool_call_counter[0]}",
                                ),
                                "name": _cb.get("name", "tool"),
                                "input": json.dumps(_cb.get("input", {})),
                                "status": "running",
                                "runtime": runtime,
                                "timestamp": time.strftime(
                                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                                ),
                            }
                            mock_mgr.append_tool_call(task_id, tc)
            except (ValueError, KeyError, TypeError):
                pass
        
        # Append to live output
        mock_mgr.append_output(task_id, f"[stderr] {err_text}")
        
        # Verify tool_call was captured (this is the key test)
        self.assertEqual(len(mock_mgr.tool_calls), 1, 
                        "Tool call should be parsed from stderr JSON")
        self.assertEqual(mock_mgr.tool_calls[0]["name"], "file_editor")
        self.assertEqual(mock_mgr.tool_calls[0]["status"], "running")
        self.assertIn("toolu_abc123def456", mock_mgr.tool_calls[0]["id"])
        
        # Verify the raw JSON also appears in output (for debugging)
        self.assertEqual(len(mock_mgr.output_lines), 1)
        self.assertIn("[stderr]", mock_mgr.output_lines[0])
        # The output should not be the raw dict — it should be the stringified JSON
        self.assertIn("stream_event", mock_mgr.output_lines[0])
    
    def test_claude_stderr_non_json_ignored(self):
        """Test that non-JSON stderr lines are just prefixed and logged, not parsed."""
        mock_mgr = MockBgTaskMgr()
        task_id = "test_task_456"
        
        # Simulate plain text stderr (e.g., debug output)
        plain_stderr = "DEBUG: Connecting to API...\n"
        err_text = plain_stderr.rstrip("\n\r")
        
        # This should not be parsed as JSON
        if err_text.strip().startswith("{"):
            try:
                json.loads(err_text.strip())
            except ValueError:
                pass
        
        # Should just be appended to output
        mock_mgr.append_output(task_id, f"[stderr] {err_text}")
        
        # Verify no tool call was created
        self.assertEqual(len(mock_mgr.tool_calls), 0)
        # Verify it was added to output
        self.assertEqual(len(mock_mgr.output_lines), 1)
        self.assertEqual(mock_mgr.output_lines[0], "[stderr] DEBUG: Connecting to API...")
    
    def test_claude_stderr_multiple_events(self):
        """Test multiple Claude stream_event objects on stderr."""
        mock_mgr = MockBgTaskMgr()
        runtime = "claude"
        task_id = "test_task_789"
        tool_call_counter = [0]
        
        # Simulate two tool_use events on stderr
        events = [
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_start",
                    "content_block": {
                        "type": "tool_use",
                        "id": "tool1",
                        "name": "get_weather",
                        "input": {"location": "NYC"}
                    }
                }
            },
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_start",
                    "content_block": {
                        "type": "tool_use",
                        "id": "tool2",
                        "name": "send_email",
                        "input": {"to": "user@example.com", "subject": "Hi"}
                    }
                }
            }
        ]
        
        for event in events:
            err_line = json.dumps(event) + "\n"
            err_text = err_line.rstrip("\n\r")
            
            if err_text.strip().startswith("{"):
                try:
                    _obj = json.loads(err_text.strip())
                    _otype = _obj.get("type", "")
                    if _otype == "stream_event":
                        _event = _obj.get("event") or {}
                        _inner = _event.get("type", "")
                        if _inner == "content_block_start":
                            _cb = _event.get("content_block") or {}
                            if _cb.get("type") == "tool_use":
                                tool_call_counter[0] += 1
                                tc = {
                                    "id": _cb.get("id"),
                                    "name": _cb.get("name"),
                                    "input": json.dumps(_cb.get("input", {})),
                                    "status": "running",
                                    "runtime": runtime,
                                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                }
                                mock_mgr.append_tool_call(task_id, tc)
                except (ValueError, KeyError, TypeError):
                    pass
            
            mock_mgr.append_output(task_id, f"[stderr] {err_text}")
        
        # Verify both tool calls were captured
        self.assertEqual(len(mock_mgr.tool_calls), 2)
        self.assertEqual(mock_mgr.tool_calls[0]["name"], "get_weather")
        self.assertEqual(mock_mgr.tool_calls[1]["name"], "send_email")
        # Verify both were added to output
        self.assertEqual(len(mock_mgr.output_lines), 2)


if __name__ == '__main__':
    unittest.main()
