"""
Regression tests for issue #398: recover when a local model stops after only
*planning* a tool call.

Design note. #443 moved the Wee runtime onto the Copilot SDK's own agentic
loop, and the SDK owns search/shell/file execution — wee only registers
``call_agent`` and ``browser`` on top. So the failure is not "the tool was
missing", it is "the model narrated an action instead of invoking a tool it
already had". The recovery is therefore #398's second option: re-prompt once
with an explicit completion instruction, in the same SDK session so context
carries. That subsumes the narrower search-only handling from #446.

The retry is deliberately gated on **zero tool calls**. A turn that used any
tool is left alone even if its prose still reads like a promise, because
re-prompting could duplicate a side effect — a delegated background task, or a
shell command.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_398")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9398")

from agent_manager import SessionManager  # noqa: E402


def _make_sm():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"agents": []}, tmp)
    tmp.close()
    return SessionManager(tmp.name)


class TestIssue398IncompleteAgenticTurn(unittest.TestCase):
    def setUp(self):
        self.sm = _make_sm()

    # --- the core condition -------------------------------------------------

    def test_promise_with_no_tool_call_is_incomplete(self):
        for text in [
            "I'll search for the current release notes.",
            "Let me check the config file for that setting.",
            "I'm going to run the test suite and report back.",
            "I need to look up the current pricing.",
            "Let me browse to the changelog page.",
            "I will delegate this to the research agent.",
            "Hold on while I read the log.",
        ]:
            with self.subTest(text=text):
                self.assertTrue(
                    self.sm._wee_turn_is_incomplete(text, 0),
                    f"should be treated as an unfinished turn: {text!r}",
                )

    def test_a_turn_that_used_a_tool_is_never_retried(self):
        """Guards against duplicating a side effect such as a delegated task."""
        text = "I'll search for the current release notes."
        self.assertFalse(self.sm._wee_turn_is_incomplete(text, 1))
        self.assertFalse(self.sm._wee_turn_is_incomplete(text, 5))

    # --- must not fire ------------------------------------------------------

    def test_completed_work_is_not_incomplete(self):
        for text in [
            "Web search completed.\n\nHere are the results: ...",
            "I searched and found three matching entries.",
            "Here are the results of the check.",
            "Based on the output, the service is healthy.",
            "I checked the file and the flag is already set.",
        ]:
            with self.subTest(text=text):
                self.assertFalse(self.sm._wee_turn_is_incomplete(text, 0), text)

    def test_explicit_refusal_is_not_incomplete(self):
        for text in [
            "I cannot search the web from this session.",
            "I can't browse external sites here.",
            "I am unable to access that file.",
        ]:
            with self.subTest(text=text):
                self.assertFalse(self.sm._wee_turn_is_incomplete(text, 0), text)

    def test_plain_answers_are_not_incomplete(self):
        for text in [
            "The capital of France is Paris.",
            "Your config sets num_ctx to 65536.",
            "",
            "   ",
            "Searching is a useful technique in general.",
            "The search results page has three columns.",
        ]:
            with self.subTest(text=text):
                self.assertFalse(self.sm._wee_turn_is_incomplete(text, 0), repr(text))

    # --- the instruction ----------------------------------------------------

    def test_completion_instruction_directs_action_not_narration(self):
        instruction = self.sm.WEE_COMPLETION_INSTRUCTION.lower()
        self.assertIn("did not perform", instruction)
        self.assertIn("tools", instruction)
        # Must discourage a second round of narration, which is the failure mode.
        self.assertIn("do not restate", instruction)

    # --- wiring -------------------------------------------------------------

    def test_retry_is_wired_into_the_sdk_path_and_bounded(self):
        source = __import__("inspect").getsource(self.sm.run_wee_native)

        self.assertIn(
            "_wee_turn_is_incomplete",
            source,
            "run_wee_native must check for an incomplete turn",
        )
        self.assertIn(
            "WEE_COMPLETION_INSTRUCTION",
            source,
            "the retry must send the explicit completion instruction",
        )
        self.assertIn(
            'tool_calls_seen["count"] += 1',
            source,
            "tool calls must be counted so a promise can be distinguished from work",
        )
        # Exactly one retry: a single extra execute call inside the guard.
        self.assertEqual(
            source.count("_wee_turn_is_incomplete"),
            1,
            "the retry must not be able to loop",
        )

    def test_tool_call_counter_updates_even_without_a_stream_buffer(self):
        """Background/non-streaming turns must still be able to detect a promise."""
        source = __import__("inspect").getsource(self.sm.run_wee_native)
        counter_at = source.index('tool_calls_seen["count"] += 1')
        guard_at = source.index("if not stream_buffer:\n                    return")
        self.assertLess(
            counter_at,
            guard_at,
            "the counter must be incremented before the stream_buffer early-return",
        )

    def test_retry_failure_preserves_the_original_answer(self):
        source = __import__("inspect").getsource(self.sm.run_wee_native)
        self.assertIn("completion retry failed", source)
        # On retry failure `output` must not be overwritten.
        self.assertIn("if retry_output and retry_output.strip():", source)


if __name__ == "__main__":
    unittest.main()
