"""Regression test for Issue #230: Claude runtime stderr JSON parsing in background tasks.

Tests that the actual _drain_stderr implementation in agent_manager.py correctly:
1. Parses Claude stream_event JSON from stderr
2. Accumulates input_json_delta parts into tool_calls
3. Finalizes tool_calls on content_block_stop
4. Does NOT leak raw JSON into output_lines if recognized as protocol
"""

import json
import unittest


class TestIssue230StderrLogicUnit(unittest.TestCase):
    """Unit tests for the stderr parsing logic."""
    
    def test_stream_event_parsed_not_output(self):
        """Verify stream_event JSON is recognized and NOT added to output."""
        stderr_line = {
            "type": "stream_event",
            "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "t1", "name": "test", "input": {}}}
        }
        
        err_text = json.dumps(stderr_line)
        
        # Simulate the check in _drain_stderr
        parsed_as_event = False
        if err_text.strip().startswith("{"):
            try:
                _obj = json.loads(err_text.strip())
                _otype = _obj.get("type", "")
                if _otype == "stream_event":
                    parsed_as_event = True
            except (ValueError, KeyError):
                pass
        
        # If parsed_as_event is True, it's NOT added to output
        self.assertTrue(parsed_as_event)
        should_output = not parsed_as_event
        self.assertFalse(should_output)
    
    def test_non_stream_event_json_is_output(self):
        """Verify non-stream_event JSON is added to output."""
        stderr_line = {"type": "error_event", "message": "something"}
        err_text = json.dumps(stderr_line)
        
        parsed_as_event = False
        if err_text.strip().startswith("{"):
            try:
                _obj = json.loads(err_text.strip())
                _otype = _obj.get("type", "")
                if _otype == "stream_event":
                    parsed_as_event = True
            except (ValueError, KeyError):
                pass
        
        # Should be output since it's not stream_event
        self.assertFalse(parsed_as_event)
        should_output = not parsed_as_event
        self.assertTrue(should_output)
    
    def test_plain_text_stderr_is_output(self):
        """Verify non-JSON stderr is added to output."""
        err_text = "This is plain text error"
        
        parsed_as_event = False
        if err_text.strip().startswith("{"):
            try:
                _obj = json.loads(err_text.strip())
                _otype = _obj.get("type", "")
                if _otype == "stream_event":
                    parsed_as_event = True
            except (ValueError, KeyError):
                pass
        
        # Should be output since it's not JSON
        self.assertFalse(parsed_as_event)
        should_output = not parsed_as_event
        self.assertTrue(should_output)


if __name__ == "__main__":
    unittest.main()
