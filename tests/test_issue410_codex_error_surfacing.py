"""
Regression tests for issue #410: a failed Codex turn must show a meaningful
failure rather than an empty response or a raw protocol frame.

The frames below are captured verbatim from Codex CLI v0.144.1 running
`codex exec --json -m gpt-5.6` on a ChatGPT account (an unsupported model):

  {"type":"item.completed","item":{"id":"item_0","type":"error",
    "message":"Model metadata for `gpt-5.6` not found. ..."}}
  {"type":"error","message":"{\\"type\\":\\"error\\",\\"status\\":400,
    \\"error\\":{\\"type\\":\\"invalid_request_error\\",\\"message\\":\\"The
    'gpt-5.6' model is not supported when using Codex with a ChatGPT
    account.\\"}}"}
  {"type":"turn.failed","error":{"message":"<same double-encoded blob>"}}

Note the double encoding: the readable sentence is at `error.message` *inside*
a JSON string. Before this fix the transcript showed the entire blob.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_410")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9410")

from agent_manager import SessionManager  # noqa: E402

HUMAN_MESSAGE = (
    "The 'gpt-5.6' model is not supported when using Codex with a ChatGPT account."
)
NESTED_BLOB = json.dumps(
    {
        "type": "error",
        "status": 400,
        "error": {"type": "invalid_request_error", "message": HUMAN_MESSAGE},
    }
)

CAPTURED_STREAM = "\n".join(
    [
        json.dumps({"type": "thread.started", "thread_id": "0199"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "error",
                    "message": "Model metadata for `gpt-5.6` not found. Defaulting to fallback metadata; this can degrade performance and cause issues.",
                },
            }
        ),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "error", "message": NESTED_BLOB}),
        json.dumps({"type": "turn.failed", "error": {"message": NESTED_BLOB}}),
    ]
)


def _make_sm():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"agents": []}, tmp)
    tmp.close()
    return SessionManager(tmp.name)


class TestIssue410CodexErrorSurfacing(unittest.TestCase):
    def setUp(self):
        self.sm = _make_sm()

    def test_failed_turn_surfaces_the_human_sentence(self):
        out = self.sm.strip_metadata(CAPTURED_STREAM, "codex")

        self.assertIn(HUMAN_MESSAGE, out, "the readable failure reason must reach the user")
        self.assertTrue(out.strip(), "a failed turn must never render as an empty response")

    def test_failed_turn_does_not_leak_the_protocol_frame(self):
        out = self.sm.strip_metadata(CAPTURED_STREAM, "codex")

        self.assertNotIn('"invalid_request_error"', out, "raw JSON frame leaked into the transcript")
        self.assertNotIn('{"type":"error"', out)
        self.assertNotIn('"status": 400', out)
        self.assertNotIn('"status":400', out)

    def test_double_encoded_payload_reduces_to_its_innermost_message(self):
        self.assertEqual(self.sm._codex_error_text(NESTED_BLOB), HUMAN_MESSAGE)
        self.assertEqual(
            self.sm._codex_error_text({"error": {"message": HUMAN_MESSAGE}}), HUMAN_MESSAGE
        )
        self.assertEqual(
            self.sm._codex_error_text(json.dumps({"message": json.dumps({"error": {"message": "twice"}})})),
            "twice",
        )

    def test_plain_text_and_empty_payloads_are_handled(self):
        self.assertEqual(self.sm._codex_error_text("plain failure"), "plain failure")
        self.assertEqual(self.sm._codex_error_text(""), "")
        self.assertEqual(self.sm._codex_error_text(None), "")

    def test_unrecognized_payload_is_preserved_rather_than_discarded(self):
        """Losing the failure entirely is worse than showing an odd payload."""
        self.assertEqual(self.sm._codex_error_text('{"nope":1}'), '{"nope":1}')

    def test_recursion_is_bounded(self):
        """A pathological payload must terminate, not blow the stack."""
        payload = {"message": "bottom"}
        for _ in range(200):
            payload = {"error": payload}
        self.sm._codex_error_text(payload)  # must simply return

    def test_partial_answer_plus_failure_keeps_both(self):
        """A turn that produced text before failing shows the text and the reason."""
        stream = "\n".join(
            [
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "I will look that up."},
                    }
                ),
                json.dumps({"type": "turn.failed", "error": {"message": NESTED_BLOB}}),
            ]
        )
        out = self.sm.strip_metadata(stream, "codex")

        self.assertIn("I will look that up.", out)
        self.assertIn(HUMAN_MESSAGE, out)


if __name__ == "__main__":
    unittest.main()
