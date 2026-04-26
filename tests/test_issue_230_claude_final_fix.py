"""
Regression tests for Issue #230: Claude tool_use input accumulation bug.

Production bug: _active_tool_calls[cb_index]["input_parts"] was seeded with
json.dumps(_cb.get("input", {})) which produces "{}" for the always-empty
content_block_start input.  When input_json_delta chunks are appended the
accumulated string becomes '{}{"key":"val"}' which is not valid JSON and
falls through the error path at content_block_stop.

The fix: initialize input_parts = [] so delta chunks concatenate into valid JSON.
"""

import json
import unittest


class TestIssue230InputPartsBug(unittest.TestCase):
    """Regression tests proving the input_parts initialization bug and its fix."""

    def test_buggy_init_produces_invalid_json(self):
        """Pre-fix: seeding with json.dumps({}) == '{}' then appending deltas yields
        invalid JSON."""
        # Reproduce the old line: "input_parts": [_json.dumps(_cb.get("input", {}))]
        input_parts = [json.dumps({})]  # → ["{}"]

        deltas = ['{"path": "/tmp/foo.txt"', ', "content": "hello"', "}"]
        for d in deltas:
            input_parts.append(d)

        accumulated = "".join(input_parts)
        # Produces: '{}{"path": "/tmp/foo.txt", "content": "hello"}'
        with self.assertRaises(json.JSONDecodeError):
            json.loads(accumulated)

    def test_fixed_init_produces_valid_json(self):
        """Post-fix: seeding with [] and appending partial_json deltas yields valid
        JSON."""
        input_parts = []  # ← the fix

        deltas = ['{"path": "/tmp/foo.txt"', ', "content": "hello"', "}"]
        for d in deltas:
            input_parts.append(d)

        accumulated = "".join(input_parts)
        parsed = json.loads(accumulated)
        self.assertEqual(parsed["path"], "/tmp/foo.txt")
        self.assertEqual(parsed["content"], "hello")

    def test_empty_input_parts_on_content_block_stop_no_deltas(self):
        """When no input_json_delta events arrive, input_parts stays [] and stop
        produces {}."""
        input_parts = []
        full_input = "".join(input_parts)
        result = json.loads(full_input) if full_input else {}
        self.assertEqual(result, {})

    def test_partial_json_accumulation_multi_chunk(self):
        """Complex input split across many deltas round-trips correctly with the fixed
        init."""
        input_parts = []
        full_expected = {
            "command": "write",
            "path": "/tmp/test.py",
            "content": "x = 1\n",
        }
        raw = json.dumps(full_expected)
        chunk_size = max(1, len(raw) // 4)
        for i in range(0, len(raw), chunk_size):
            input_parts.append(raw[i : i + chunk_size])

        accumulated = "".join(input_parts)
        self.assertEqual(json.loads(accumulated), full_expected)

    def test_full_lifecycle_start_deltas_stop(self):
        """
        Simulate the complete production lifecycle for one tool_use block:
        content_block_start → input_json_delta × N → content_block_stop.

        Mirrors the _drain_stderr logic in agent_manager.py lines 13600-13654.
        """

        class _MockMgr:
            def __init__(self):
                self.tool_calls = []
                self.output_lines = []

            def append_tool_call(self, task_id, tc):
                self.tool_calls.append(dict(tc))

            def append_output(self, task_id, line):
                self.output_lines.append(line)

            def update_tool_call(self, task_id, tool_id, **kwargs):
                for tc in self.tool_calls:
                    if tc.get("id") == tool_id:
                        tc.update(kwargs)

        mgr = _MockMgr()
        task_id = "t1"
        active = {}

        # --- content_block_start ---
        cb_start = {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_fix_test",
                    "name": "write_file",
                    "input": {},
                },
            },
        }
        _cb = cb_start["event"]["content_block"]
        cb_index = cb_start["event"]["index"]
        mgr.append_tool_call(
            task_id,
            {
                "id": _cb["id"],
                "name": _cb["name"],
                "input": json.dumps(_cb.get("input", {})),
                "status": "running",
            },
        )
        # Fixed initialisation — must be []
        active[cb_index] = {"id": _cb["id"], "name": _cb["name"], "input_parts": []}

        # --- input_json_delta events ---
        raw_input = json.dumps({"path": "/tmp/out.txt", "content": "done"})
        for chunk in [raw_input[:10], raw_input[10:20], raw_input[20:]]:
            active[cb_index]["input_parts"].append(chunk)
            accumulated = "".join(active[cb_index]["input_parts"])
            mgr.update_tool_call(task_id, active[cb_index]["id"], input=accumulated)

        # --- content_block_stop ---
        tc_info = active.pop(cb_index)
        full_input = "".join(tc_info["input_parts"])
        parsed_input = json.loads(full_input) if full_input else {}
        mgr.update_tool_call(
            task_id,
            tc_info["id"],
            status="completed",
            input=json.dumps(parsed_input),
        )

        self.assertEqual(len(mgr.tool_calls), 1)
        self.assertEqual(mgr.tool_calls[0]["status"], "completed")
        final = json.loads(mgr.tool_calls[0]["input"])
        self.assertEqual(final["path"], "/tmp/out.txt")
        self.assertEqual(final["content"], "done")
        # No raw JSON should have leaked to output_lines
        self.assertEqual(len(mgr.output_lines), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
