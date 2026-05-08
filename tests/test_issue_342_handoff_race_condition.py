"""
Regression tests for issue #342:
  Handoff race condition causes silent context loss on /runtime switch.

When _get_session_messages() returns [] (due to flush timing), write_handoff_summary()
must NOT silently cancel the handoff.  It must either:
  (a) retry and succeed, or
  (b) write a fallback handoff note so the new runtime is informed.
"""

import json
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import tempfile
import os
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from session_handoff import SessionHandoff


def _make_handoff(tmpdir: str) -> SessionHandoff:
    """Return a SessionHandoff wired to a temp directory."""
    h = SessionHandoff()
    h.copilot_home = Path(tmpdir)
    h.chat_history_path = h.copilot_home / "chat-history.json"
    h.session_map_path = h.copilot_home / "n8n-session-map.json"
    h.session_state_dir = h.copilot_home / "session-state"
    return h


def _write_chat_history(path: Path, n8n_session_id: str, messages: list):
    data = {
        "user1": {
            "sessions": [
                {
                    "session_id": n8n_session_id,
                    "messages": messages,
                }
            ]
        }
    }
    path.write_text(json.dumps(data), encoding="utf-8")


FAKE_MESSAGES = [
    {"role": "user", "content": "Hello there", "timestamp": 1715000000},
    {"role": "assistant", "content": "Hi! How can I help?", "timestamp": 1715000001},
    {"role": "user", "content": "Fix the auth bug please", "timestamp": 1715000002},
]


class TestHandoffRetryLogic(unittest.TestCase):
    """_get_session_messages_with_retry retries until history appears."""

    def test_retry_constants_exist(self):
        """Class must expose HISTORY_RETRY_ATTEMPTS and HISTORY_RETRY_DELAY."""
        self.assertTrue(hasattr(SessionHandoff, "HISTORY_RETRY_ATTEMPTS"))
        self.assertTrue(hasattr(SessionHandoff, "HISTORY_RETRY_DELAY"))
        self.assertGreater(SessionHandoff.HISTORY_RETRY_ATTEMPTS, 1)
        self.assertGreater(SessionHandoff.HISTORY_RETRY_DELAY, 0)

    def test_retry_method_exists(self):
        """SessionHandoff must have _get_session_messages_with_retry."""
        h = SessionHandoff()
        self.assertTrue(hasattr(h, "_get_session_messages_with_retry"))
        self.assertTrue(callable(h._get_session_messages_with_retry))

    def test_returns_messages_on_first_success(self):
        """If messages available on attempt 1, returns them without sleeping."""
        h = SessionHandoff()
        h._get_session_messages = MagicMock(return_value=FAKE_MESSAGES)
        with patch("time.sleep") as mock_sleep:
            result = h._get_session_messages_with_retry("sess1")
        self.assertEqual(result, FAKE_MESSAGES)
        mock_sleep.assert_not_called()

    def test_retries_when_initially_empty(self):
        """If messages unavailable on attempt 1 but available on attempt 2, retries."""
        h = SessionHandoff()
        h._get_session_messages = MagicMock(side_effect=[[], FAKE_MESSAGES])
        with patch("time.sleep"):
            result = h._get_session_messages_with_retry("sess1")
        self.assertEqual(result, FAKE_MESSAGES)
        self.assertEqual(h._get_session_messages.call_count, 2)

    def test_sleeps_between_retries(self):
        """Must sleep between retry attempts."""
        h = SessionHandoff()
        h._get_session_messages = MagicMock(side_effect=[[], FAKE_MESSAGES])
        with patch("time.sleep") as mock_sleep:
            h._get_session_messages_with_retry("sess1")
        mock_sleep.assert_called_once_with(SessionHandoff.HISTORY_RETRY_DELAY)

    def test_returns_empty_after_all_retries_exhausted(self):
        """Returns [] only after all retries fail."""
        h = SessionHandoff()
        h._get_session_messages = MagicMock(return_value=[])
        with patch("time.sleep"):
            result = h._get_session_messages_with_retry("sess1")
        self.assertEqual(result, [])
        self.assertEqual(
            h._get_session_messages.call_count, SessionHandoff.HISTORY_RETRY_ATTEMPTS
        )


class TestHandoffFallbackSummary(unittest.TestCase):
    """write_handoff_summary must never silently drop the handoff."""

    def test_no_silent_cancel_when_history_empty(self):
        """If history is unavailable after all retries, write_handoff_summary
        must NOT return None — it must write a fallback handoff file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            h = _make_handoff(tmpdir)
            # Ensure chat history is missing (not written)
            h.session_map_path.parent.mkdir(parents=True, exist_ok=True)
            h.session_map_path.write_text(json.dumps({}), encoding="utf-8")

            with patch("time.sleep"):
                result = h.write_handoff_summary(
                    n8n_session_id="7747ee38",
                    new_session_id="new-sess",
                    prev_session_id="old-sess",
                    prev_runtime="copilot",
                    new_runtime="wee",
                )

            # Must not return None
            self.assertIsNotNone(
                result,
                "write_handoff_summary returned None — silent context drop NOT fixed",
            )
            # Must write the handoff file
            handoff_path = Path(result)
            self.assertTrue(handoff_path.exists(), "handoff.md was not written")

    def test_fallback_content_informs_new_runtime(self):
        """Fallback handoff.md must mention history unavailability."""
        with tempfile.TemporaryDirectory() as tmpdir:
            h = _make_handoff(tmpdir)
            h.session_map_path.parent.mkdir(parents=True, exist_ok=True)
            h.session_map_path.write_text(json.dumps({}), encoding="utf-8")

            with patch("time.sleep"):
                result = h.write_handoff_summary(
                    n8n_session_id="7747ee38",
                    new_session_id="new-sess",
                    prev_session_id="old-sess",
                    prev_runtime="copilot",
                    new_runtime="wee",
                )

            content = Path(result).read_text(encoding="utf-8")
            # Must mention history was unavailable
            self.assertIn("flush", content.lower(), "Fallback must mention flush timing")
            # Must include runtime info
            self.assertIn("copilot", content)
            self.assertIn("wee", content)

    def test_fallback_meta_json_written(self):
        """handoff_meta.json must be written even in the fallback path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            h = _make_handoff(tmpdir)
            h.session_map_path.parent.mkdir(parents=True, exist_ok=True)
            h.session_map_path.write_text(json.dumps({}), encoding="utf-8")

            with patch("time.sleep"):
                h.write_handoff_summary(
                    n8n_session_id="7747ee38",
                    new_session_id="new-sess",
                    prev_session_id="old-sess",
                    prev_runtime="copilot",
                    new_runtime="wee",
                )

            meta_path = h.session_state_dir / "new-sess" / "handoff_meta.json"
            self.assertTrue(meta_path.exists())
            meta = json.loads(meta_path.read_text())
            self.assertEqual(meta["prev_runtime"], "copilot")
            self.assertEqual(meta["new_runtime"], "wee")

    def test_normal_path_still_works(self):
        """When history IS available, normal summary is written (regression guard)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            h = _make_handoff(tmpdir)
            h.copilot_home.mkdir(parents=True, exist_ok=True)
            _write_chat_history(h.chat_history_path, "sess-abc", FAKE_MESSAGES)
            h.session_map_path.write_text(json.dumps({}), encoding="utf-8")

            with patch("time.sleep"):
                result = h.write_handoff_summary(
                    n8n_session_id="sess-abc",
                    new_session_id="new-sess2",
                    prev_session_id="old-sess2",
                    prev_runtime="copilot",
                    new_runtime="wee",
                )

            self.assertIsNotNone(result)
            content = Path(result).read_text(encoding="utf-8")
            # Should contain real content, not the fallback note
            self.assertIn("Fix the auth bug please", content)


class TestRaceConditionSimulation(unittest.TestCase):
    """Simulate the exact scenario from issue #342."""

    def test_issue_342_scenario(self):
        """
        Reproduce: history exists but _get_session_messages returns [] on first
        call (flush not yet complete). Verify retry recovers correctly.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            h = _make_handoff(tmpdir)
            h.copilot_home.mkdir(parents=True, exist_ok=True)
            h.session_map_path.write_text(json.dumps({}), encoding="utf-8")

            call_count = [0]

            def delayed_get(session_id):
                """First call simulates unflushed file; second call has the data."""
                call_count[0] += 1
                if call_count[0] == 1:
                    return []  # flush not yet complete
                return FAKE_MESSAGES

            h._get_session_messages = delayed_get

            with patch("time.sleep"):
                result = h.write_handoff_summary(
                    n8n_session_id="7747ee38",
                    new_session_id="new-sess3",
                    prev_session_id="old-sess3",
                    prev_runtime="copilot",
                    new_runtime="wee",
                )

            # Must succeed (retry caught the delayed flush)
            self.assertIsNotNone(result)
            content = Path(result).read_text(encoding="utf-8")
            # Should have real content
            self.assertIn("Fix the auth bug please", content)


if __name__ == "__main__":
    unittest.main()
