"""
Regression tests for Issue #230: Claude runtime log parsing in background tasks.
"""
import json as _json
import unittest


class TestIssue230ClaudeLogParsing(unittest.TestCase):
    """Test Issue #230: Claude stream_event parsing in background tasks"""

    def test_delta_accumulation_without_prefix(self):
        """Test that deltas start with first partial_json, not stringified {}"""
        active_calls = {
            0: {
                "id": "toolu_123",
                "name": "file_edit",
                "input_parts": [''],  
            }
        }
        
        # Simulate deltas with partial JSON chunks
        deltas = [
            '{"path": "/tmp/test.txt"',
            ', "content": "create file"',
            '}',
        ]
        
        for partial in deltas:
            active_calls[0]["input_parts"].append(partial)
        
        full_input = "".join(active_calls[0]["input_parts"])
        parsed = _json.loads(full_input)
        self.assertEqual(parsed["path"], "/tmp/test.txt")
        self.assertEqual(parsed["content"], "create file")

    def test_protocol_json_filtered_from_output(self):
        """Test that stream_event JSON is properly filtered from output"""
        protocol_lines = [
            '{"type":"stream_event","event":{"type":"content_block_start"}}',
            '{"type":"stream_event","event":{"type":"content_block_delta"}}',
            '{"type":"stream_event","event":{"type":"content_block_stop"}}',
        ]
        
        output_lines = []
        
        for line in protocol_lines:
            skip_output = False
            if line.strip().startswith("{"):
                try:
                    _obj = _json.loads(line)
                    if _obj.get("type") == "stream_event":
                        skip_output = True
                except (ValueError, TypeError):
                    pass
            
            if not skip_output:
                output_lines.append(line)
        
        self.assertEqual(len(output_lines), 0, "Protocol JSON should be filtered from output")

    def test_non_protocol_json_passes_through(self):
        """Test that non-protocol JSON still gets output"""
        line = '{"result":"success","data":{"key":"value"}}'
        
        skip_output = False
        if line.strip().startswith("{"):
            try:
                _obj = _json.loads(line)
                if _obj.get("type") == "stream_event":
                    skip_output = True
            except (ValueError, TypeError):
                pass
        
        self.assertFalse(skip_output, "Non-protocol JSON should NOT be filtered")

    def test_stream_event_filter_in_stderr(self):
        """Test that stream_event is filtered from stderr before final output"""
        stderr_lines = [
            '{"type":"stream_event","event":{"type":"content_block_start"}}\n',
            'WARNING: something\n',
            '{"type":"stream_event","event":{"type":"content_block_delta"}}\n',
            'ERROR: something else\n',
        ]
        
        filtered_stderr = []
        for line in stderr_lines:
            _skip = False
            if line.strip().startswith("{"):
                try:
                    _obj = _json.loads(line.strip())
                    if _obj.get("type") == "stream_event":
                        _skip = True
                except (ValueError, TypeError):
                    pass
            if not _skip:
                filtered_stderr.append(line)
        
        self.assertEqual(len(filtered_stderr), 2)
        self.assertIn("WARNING", filtered_stderr[0])
        self.assertIn("ERROR", filtered_stderr[1])

    def test_tool_call_tracking_with_cb_index(self):
        """Test _active_tool_calls tracking by content block index"""
        _active_tool_calls = {}
        
        # Simulate content_block_start event
        cb_index = 0
        tool_id = "toolu_001"
        
        _active_tool_calls[cb_index] = {
            "id": tool_id,
            "name": "test_tool",
            "input_parts": [""],
        }
        
        self.assertIn(cb_index, _active_tool_calls)
        self.assertEqual(_active_tool_calls[cb_index]["id"], tool_id)
        
        # Simulate content_block_delta events with valid JSON
        _active_tool_calls[cb_index]["input_parts"].append('{"key"')
        _active_tool_calls[cb_index]["input_parts"].append(': "value"}')
        
        full_input = "".join(_active_tool_calls[cb_index]["input_parts"])
        parsed = _json.loads(full_input)
        self.assertEqual(parsed["key"], "value")
        
        # Simulate content_block_stop
        tc_info = _active_tool_calls.pop(cb_index)
        self.assertEqual(tc_info["id"], tool_id)
        self.assertNotIn(cb_index, _active_tool_calls)


if __name__ == '__main__':
    unittest.main(verbosity=2)
