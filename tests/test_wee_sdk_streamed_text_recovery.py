"""
Regression coverage for streamed content being dropped from a Wee SDK turn.

Observed on the dev server against Ollama (gemma4:e4b via the
OpenAI-compatible transport). The SDK forwarded three streaming deltas to the
event callback — "OK", "I", "I" — but `send_and_wait` reported only the final
fragment "I". Because deltas were never accumulated, that 1-character fragment
became the turn's result and the Ollama length check then rejected it:

    WeeCopilotSDKError: Ollama Copilot SDK returned an unusably short response

Direct `curl` to the same `/v1/chat/completions` endpoint returned the full
answer, so the content existed — it was being discarded on our side.

NOTE this does not by itself make the SDK/Ollama transport reliable: turns are
still sometimes observed producing no deltas at all. It fixes only the case
where content arrived and was thrown away.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wee_copilot_sdk import prefer_longer_text  # noqa: E402


class TestStreamedTextRecovery(unittest.TestCase):
    def test_fragment_final_message_is_replaced_by_the_assembled_stream(self):
        """The exact observed failure: deltas OK/I/I reported as just "I"."""
        self.assertEqual(prefer_longer_text("I", "OKII"), "OKII")

    def test_complete_final_message_is_preferred_over_its_own_stream(self):
        """The normal case must not change: a full message wins."""
        full = "A hash map stores key-value pairs with O(1) average lookup."
        self.assertEqual(prefer_longer_text(full, full), full)
        # Even if the stream is a strict prefix (last delta lost), keep the full one.
        self.assertEqual(prefer_longer_text(full, full[:20]), full)

    def test_stream_used_when_there_is_no_final_message(self):
        self.assertEqual(prefer_longer_text("", "streamed answer"), "streamed answer")
        self.assertEqual(prefer_longer_text(None, "streamed answer"), "streamed answer")

    def test_final_used_when_there_is_no_stream(self):
        self.assertEqual(prefer_longer_text("final answer", ""), "final answer")
        self.assertEqual(prefer_longer_text("final answer", None), "final answer")

    def test_both_empty_yields_empty_not_none(self):
        self.assertEqual(prefer_longer_text(None, None), "")
        self.assertEqual(prefer_longer_text("", ""), "")
        self.assertEqual(prefer_longer_text("   ", "  "), "")

    def test_whitespace_is_not_mistaken_for_content(self):
        """A stream of only whitespace must not displace a real message."""
        self.assertEqual(prefer_longer_text("ok", "        "), "ok")

    def test_result_is_stripped(self):
        self.assertEqual(prefer_longer_text("  padded final  ", ""), "padded final")
        self.assertEqual(prefer_longer_text("", "  padded stream  "), "padded stream")


if __name__ == "__main__":
    unittest.main()
