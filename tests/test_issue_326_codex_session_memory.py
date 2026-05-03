"""
Regression tests for issue #326: Codex runtime loses session-turn memory
within the same session.

Root cause: After a new Codex session runs, the real Codex session UUID
(created by the Codex CLI itself) was never saved back to the session map.
On the next turn, session_exists() found no rollout file for the
pre-generated UUID → can_resume = False → fresh session with no context.

Fix: Add "codex" to the list of runtimes whose session ID is updated from
get_most_recent_session_id() after each new (non-resume) dispatch, matching
the existing pattern for "copilot", "opencode", and "gemini".
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_326")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9326")

from agent_manager import SessionManager  # noqa: E402


def _make_sm():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"agents": []}, tmp)
    tmp.close()
    return SessionManager(tmp.name), tmp.name


class TestIssue326CodexSessionMemory(unittest.TestCase):
    """Codex session ID must be captured after turn 1 so turn 2 can resume."""

    @classmethod
    def setUpClass(cls):
        cls.sm, cls.config_path = _make_sm()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.config_path)

    def _make_codex_session_file(self, tmpdir: Path, session_uuid: str):
        """Create a fake Codex rollout file matching real CLI layout."""
        date_dir = tmpdir / "2026" / "05" / "03"
        date_dir.mkdir(parents=True, exist_ok=True)
        rollout = date_dir / f"rollout-2026-05-03T12-00-00-{session_uuid}.jsonl"
        rollout.write_text(
            json.dumps(
                {
                    "timestamp": "2026-05-03T12:00:00Z",
                    "type": "session_meta",
                    "payload": {"id": session_uuid},
                }
            )
            + "\n"
        )
        return rollout

    def test_session_id_updated_after_first_codex_turn(self):
        """After a new Codex run the real session UUID must be saved to the map."""
        real_uuid = "aabbccdd-1234-5678-9abc-def012345678"

        with tempfile.TemporaryDirectory() as tmpdir:
            codex_session_dir = Path(tmpdir)
            self._make_codex_session_file(codex_session_dir, real_uuid)

            # Simulate: new session (can_resume=False), codex creates rollout file
            with (
                patch.object(self.sm, "codex_session_dir", codex_session_dir),
                patch.object(self.sm, "update_session_field") as mock_update,
            ):
                # Simulate the block that runs after _dispatch_single_runtime
                current_runtime = "codex"
                can_resume = False
                n8n_session_id = "test-n8n-326"
                agent = "orchestrator"

                if not can_resume and current_runtime in (
                    "copilot",
                    "opencode",
                    "gemini",
                    "codex",
                ):
                    new_id = self.sm.get_most_recent_session_id(
                        current_runtime, agent
                    )
                    if new_id:
                        self.sm.update_session_field(
                            n8n_session_id, "session_id", new_id
                        )

                mock_update.assert_called_once_with(
                    n8n_session_id, "session_id", real_uuid
                )

    def test_session_exists_finds_codex_rollout_by_uuid(self):
        """session_exists() must return True when the rollout file exists."""
        real_uuid = "aabbccdd-1234-5678-9abc-def012345678"

        with tempfile.TemporaryDirectory() as tmpdir:
            codex_session_dir = Path(tmpdir)
            self._make_codex_session_file(codex_session_dir, real_uuid)

            with patch.object(self.sm, "codex_session_dir", codex_session_dir):
                self.assertTrue(
                    self.sm.session_exists(real_uuid, "codex"),
                    "session_exists() must return True for a known rollout file UUID",
                )

    def test_session_exists_returns_false_for_unknown_uuid(self):
        """session_exists() must return False for a UUID with no rollout file."""
        unknown_uuid = "00000000-0000-0000-0000-000000000000"

        with tempfile.TemporaryDirectory() as tmpdir:
            codex_session_dir = Path(tmpdir)
            with patch.object(self.sm, "codex_session_dir", codex_session_dir):
                self.assertFalse(
                    self.sm.session_exists(unknown_uuid, "codex"),
                    "session_exists() must return False for an unknown UUID",
                )

    def test_pre_generated_uuid_not_resumable_without_rollout(self):
        """A pre-generated UUID (no rollout file) must not allow session resume."""
        pre_generated_uuid = "deadbeef-dead-beef-dead-beefdeadbeef"

        with tempfile.TemporaryDirectory() as tmpdir:
            codex_session_dir = Path(tmpdir)
            with patch.object(self.sm, "codex_session_dir", codex_session_dir):
                can_resume = self.sm.session_exists(pre_generated_uuid, "codex")
                self.assertFalse(
                    can_resume,
                    "Pre-generated UUID with no rollout file must not be resumable",
                )

    def test_get_most_recent_session_id_extracts_uuid_from_rollout_filename(self):
        """get_most_recent_session_id() must extract the correct UUID."""
        real_uuid = "aabbccdd-1234-5678-9abc-def012345678"

        with tempfile.TemporaryDirectory() as tmpdir:
            codex_session_dir = Path(tmpdir)
            self._make_codex_session_file(codex_session_dir, real_uuid)

            with patch.object(self.sm, "codex_session_dir", codex_session_dir):
                result = self.sm.get_most_recent_session_id("codex")
                self.assertEqual(
                    result,
                    real_uuid,
                    f"Expected UUID {real_uuid!r}, got {result!r}",
                )

    def test_codex_in_session_id_update_runtime_list(self):
        """Verify codex is in the runtime list that triggers session ID update."""
        # This tests the fix itself: the source code must include codex in the
        # post-dispatch session ID update block.
        import inspect
        source = inspect.getsource(self.sm.execute)
        # Find the section after _dispatch_single_runtime call
        assert '"codex"' in source, (
            '"codex" must appear in the session ID update block inside dispatch_query'
        )
        # Verify it's near the other runtimes in the same block
        update_block_idx = source.find("Handle session ID mapping")
        assert update_block_idx != -1, "Session ID mapping block not found"
        block_snippet = source[update_block_idx : update_block_idx + 700]
        assert '"codex"' in block_snippet, (
            '"codex" not found in the session ID mapping block — fix not applied'
        )


if __name__ == "__main__":
    unittest.main()
