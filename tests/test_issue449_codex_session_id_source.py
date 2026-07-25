"""
Regression tests for issue #449: the Codex session ID must come from Codex, not
from an mtime scan of a shared directory.

`~/.codex/sessions` is shared with Codex Desktop and any other local Codex use,
so `get_most_recent_session_id("codex", ...)` could return a rollout belonging
to an unrelated task. `codex exec resume` then hangs or resumes someone else's
context.

`codex exec --json` states the ID outright, captured verbatim from a real run:

    {"type": "thread.started", "thread_id": "019f96bd-4256-7ae1-bd50-19277a22af5e"}

Reading it removes the guess, and keeps #326 (session-turn memory) working
because the recorded ID is observed rather than inferred.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_449")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9449")

from agent_manager import SessionManager  # noqa: E402

REAL_THREAD_ID = "019f96bd-4256-7ae1-bd50-19277a22af5e"

# Captured shape of a real `codex exec --json` turn.
REAL_STREAM = "\n".join(
    [
        json.dumps({"type": "thread.started", "thread_id": REAL_THREAD_ID}),
        json.dumps({"type": "turn.started"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Done."},
            }
        ),
        json.dumps({"type": "turn.completed"}),
    ]
)


def _make_sm():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"agents": []}, tmp)
    tmp.close()
    return SessionManager(tmp.name)


class TestIssue449CodexSessionIdSource(unittest.TestCase):
    def setUp(self):
        self.sm = _make_sm()

    def test_thread_id_is_read_from_the_stream(self):
        self.assertEqual(
            self.sm.codex_thread_id_from_output(REAL_STREAM), REAL_THREAD_ID
        )

    def test_absent_frame_yields_none_so_no_id_is_adopted(self):
        """Declining to guess is the point — never fall back to a scan."""
        stream = "\n".join(
            [
                json.dumps({"type": "turn.started"}),
                json.dumps({"type": "turn.completed"}),
            ]
        )
        self.assertIsNone(self.sm.codex_thread_id_from_output(stream))
        self.assertIsNone(self.sm.codex_thread_id_from_output(""))
        self.assertIsNone(self.sm.codex_thread_id_from_output(None))

    def test_non_json_and_malformed_lines_are_tolerated(self):
        """Codex interleaves plain-text log lines with the JSON stream."""
        stream = "\n".join(
            [
                "2026-07-25T00:46:53Z ERROR codex_models_manager::cache: failed to load",
                "{not json at all",
                json.dumps({"type": "thread.started", "thread_id": REAL_THREAD_ID}),
                "OpenAI Codex v0.144.1",
            ]
        )
        self.assertEqual(self.sm.codex_thread_id_from_output(stream), REAL_THREAD_ID)

    def test_blank_or_non_string_thread_id_is_rejected(self):
        for value in ("", "   ", None, 12345, {"id": "x"}):
            with self.subTest(value=value):
                stream = json.dumps({"type": "thread.started", "thread_id": value})
                self.assertIsNone(self.sm.codex_thread_id_from_output(stream))

    def test_first_thread_started_wins(self):
        """A turn belongs to one thread; a later frame must not override it."""
        stream = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": REAL_THREAD_ID}),
                json.dumps({"type": "thread.started", "thread_id": "other-thread"}),
            ]
        )
        self.assertEqual(self.sm.codex_thread_id_from_output(stream), REAL_THREAD_ID)

    def test_a_lookalike_frame_is_not_mistaken_for_thread_started(self):
        """Only the real event type counts, not any line mentioning it."""
        stream = "\n".join(
            [
                json.dumps(
                    {"type": "item.completed", "item": {"text": "thread.started soon"}}
                ),
                json.dumps({"type": "log", "message": "thread.started", "thread_id": "nope"}),
            ]
        )
        self.assertIsNone(self.sm.codex_thread_id_from_output(stream))

    def test_scan_is_no_longer_used_for_codex(self):
        """The mtime scan is what could adopt a Codex Desktop session."""
        import inspect

        source = inspect.getsource(self.sm.execute)
        block_idx = source.find("Handle session ID mapping")
        self.assertNotEqual(block_idx, -1)
        block = source[block_idx : block_idx + 900]
        scan_idx = block.find("get_most_recent_session_id")
        self.assertNotEqual(scan_idx, -1, "scan should remain for other runtimes")
        self.assertNotIn('"codex"', block[:scan_idx])

    def test_run_codex_persists_the_captured_id(self):
        import inspect

        source = inspect.getsource(self.sm.run_codex)
        self.assertIn("codex_thread_id_from_output", source)
        self.assertIn('update_session_field(n8n_session_id, "session_id"', source)


if __name__ == "__main__":
    unittest.main()
