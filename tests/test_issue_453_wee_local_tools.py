"""Regression tests for issue #453.

On the wee runtime with a local Ollama model, only search/call_agent/browser
were registered. With no Python tool and no reliable way to reach the SDK's
shell built-in, models printed the command they *would* have run and the turn
ended with nothing executed:

    filesystem-write -> ```bash\necho -n 'WEE-WROTE-5K2M8' > /tmp/probe```
    python-compute   -> <python>print(7919 * 104729)</python>
    shell-fact       -> ```bash\nctx_execute(language: "shell", ...)```

#398's retry could not rescue these because its detector only matched prose
promises ("I'll search for that"), and none of the above contains one.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_453")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9453")

from agent_manager import SessionManager  # noqa: E402


def _make_sm():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"agents": []}, tmp)
    tmp.close()
    return SessionManager(tmp.name)


class TestUnexecutedCallDetection(unittest.TestCase):
    """A printed command must count as an incomplete turn."""

    def test_fenced_bash_block_with_no_tool_call_is_incomplete(self):
        text = (
            "Writing the exact text `WEE-WROTE-5K2M8` into `/tmp/wee_write_probe.txt`:\n\n"
            "```bash\necho -n 'WEE-WROTE-5K2M8' > /tmp/wee_write_probe.txt\n```"
        )
        self.assertTrue(SessionManager._wee_turn_is_incomplete(text, 0))

    def test_pseudo_xml_python_tag_is_incomplete(self):
        text = "<python>print(7919 * 104729)</python>"
        self.assertTrue(SessionManager._wee_turn_is_incomplete(text, 0))

    def test_invented_tool_name_in_a_block_is_incomplete(self):
        text = '```bash\nctx_execute(language: "shell", code: "uname -s")\n```'
        self.assertTrue(SessionManager._wee_turn_is_incomplete(text, 0))

    def test_inline_pseudo_call_is_incomplete(self):
        """Live-observed after the python tool was registered: the model wrote
        the call's own signature back as text instead of invoking it."""
        text = '`python(code="print(7919 * 104729)")`, **compute product**.'
        self.assertTrue(SessionManager._wee_turn_is_incomplete(text, 0))

    def test_compute_promise_is_incomplete(self):
        """Live-observed: 'compute'/'calculate' weren't in the original verb
        list, so a python-tool promise slipped through with no retry."""
        text = "I'll compute that multiplication using Python:"
        self.assertTrue(SessionManager._wee_turn_is_incomplete(text, 0))

    def test_a_turn_that_used_a_tool_is_never_incomplete(self):
        """Re-prompting after real work risks repeating a side effect."""
        text = "```bash\nrm -rf /tmp/scratch\n```"
        self.assertFalse(SessionManager._wee_turn_is_incomplete(text, 1))

    def test_explained_snippet_is_left_alone(self):
        """Answering "show me a script" must not be mistaken for a failed call."""
        text = (
            "You can rotate the logs with a short shell script. The idea is to "
            "move the current file aside, signal the daemon so it reopens its "
            "handle, and then compress whatever was rotated out last time. Keep "
            "roughly two weeks of history so a bad deploy is still diagnosable "
            "days later, and run it from cron rather than a timer so the output "
            "lands in the mail spool you already read.\n\n"
            "```bash\nmv app.log app.log.1\n```\n\n"
            "Adjust the retention window to taste; anything past a fortnight is "
            "usually noise, and the compression step keeps the directory small."
        )
        self.assertFalse(SessionManager._wee_turn_is_incomplete(text, 0))

    def test_prose_promise_still_detected(self):
        """#398's original behaviour must survive the #453 addition."""
        self.assertTrue(
            SessionManager._wee_turn_is_incomplete("I will use the search tool now.", 0)
        )

    def test_reported_phrasing_with_backticks_is_detected(self):
        """The verbatim reply from the bug report, backticks included.

        #398's verb list had no "use" branch, so the exact sentence users hit
        most often slipped through and no retry ever fired.
        """
        text = (
            "The best way to start is by doing some research on the available "
            "options and what factors I should consider (like whether the cart "
            "uses lead-acid, lithium-ion, etc.). I will use the `search` tool now."
        )
        self.assertTrue(SessionManager._wee_turn_is_incomplete(text, 0))

    def test_use_without_a_tool_noun_is_not_a_promise(self):
        """"I'll use a different approach" is prose, not an abandoned call."""
        self.assertFalse(
            SessionManager._wee_turn_is_incomplete(
                "I will use a different approach for this problem.", 0
            )
        )

    def test_completed_work_is_not_incomplete(self):
        text = "Search completed.\n\n```bash\nuname -s\n```"
        self.assertFalse(SessionManager._wee_turn_is_incomplete(text, 0))

    def test_empty_and_plain_text_are_not_incomplete(self):
        self.assertFalse(SessionManager._wee_turn_is_incomplete("", 0))
        self.assertFalse(SessionManager._wee_turn_is_incomplete("Hello there!", 0))


class TestPythonTool(unittest.TestCase):
    """The python tool must exist and actually execute."""

    def setUp(self):
        self.sm = _make_sm()

    def test_python_tool_runs_code_and_returns_output(self):
        out = self.sm._wee_execute_tool(
            "python", {"code": "print(7919 * 104729)"}, "orchestrator", None
        )
        self.assertIn("829348951", out)

    def test_python_tool_reports_a_real_traceback_line(self):
        """#443 removed the old tool because `python3 -c` gave opaque errors."""
        out = self.sm._wee_execute_tool(
            "python", {"code": "print('unterminated)"}, "orchestrator", None
        )
        self.assertIn("SyntaxError", out)

    def test_python_tool_requires_code(self):
        out = self.sm._wee_execute_tool("python", {}, "orchestrator", None)
        self.assertIn("requires 'code'", out)

    def test_python_is_advertised_in_the_capability_prompt(self):
        prompt = self.sm._wee_augment_system_prompt_with_tools("BASE")
        self.assertIn("**python**", prompt)

    def test_unknown_tool_message_lists_python(self):
        out = self.sm._wee_execute_tool("nope", {}, "orchestrator", None)
        self.assertIn("python", out)

    def test_python_tool_is_registered_on_the_sdk_session(self):
        """Both the first turn and the #398 retry must offer the same tools."""
        import inspect

        source = inspect.getsource(self.sm.run_wee_native)
        self.assertEqual(source.count("tools=[search_tool, python_tool"), 2)


if __name__ == "__main__":
    unittest.main()
