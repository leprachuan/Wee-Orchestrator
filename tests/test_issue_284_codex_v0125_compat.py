#!/usr/bin/env python3
"""Regression tests for issue #284 — Codex CLI v0.125.0 compatibility."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import agent_manager
from agent_manager import SessionManager


class TestIssue284CodexV0125Compat(unittest.TestCase):
    """Verify Codex CLI v0.125.0 flag changes are correctly handled."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mgr = SessionManager.__new__(SessionManager)
        # Minimal attribute init to satisfy run_codex internals
        self.mgr.AGENTS = {
            "orchestrator": {"path": self.tmpdir},
        }
        self.mgr.command_timeout = 60
        self.mgr.codex_session_dir = Path(self.tmpdir) / ".codex" / "sessions"
        self.mgr.codex_session_dir.mkdir(parents=True)

    # ------------------------------------------------------------------
    # Flag tests
    # ------------------------------------------------------------------

    def _capture_cmd_new_session(self, mode="default"):
        """Return the cmd list that run_codex would build for a new session."""
        captured = {}

        def fake_execute(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            return '{"type":"thread.started","thread_id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}\n{"type":"turn.started"}\n{"type":"item.completed","item":{"id":"i0","type":"agent_message","text":"hello"}}\n{"type":"turn.completed","usage":{}}'

        self.mgr._parse_mode_command = lambda p: (p, mode)
        self.mgr._resolve_permission_mode = lambda sd, m: m
        self.mgr.get_or_create_session_data = lambda sid: {"channel": "webui"}
        self.mgr.build_agent_context_prompt = lambda *a, **kw: "test prompt"
        self.mgr._execute_subprocess_with_tracking = fake_execute
        self.mgr.strip_metadata = SessionManager.strip_metadata.__get__(self.mgr)

        self.mgr.run_codex(
            prompt="hello",
            model="gpt-4o",
            agent="orchestrator",
            session_id=None,
            resume=False,
            n8n_session_id="test-session",
        )
        return captured.get("cmd", [])

    def _capture_cmd_resume_session(self):
        """Return the cmd list that run_codex would build when resuming."""
        captured = {}

        def fake_execute(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            return '{"type":"turn.started"}\n{"type":"item.completed","item":{"id":"i0","type":"agent_message","text":"resumed"}}\n{"type":"turn.completed","usage":{}}'

        self.mgr._parse_mode_command = lambda p: (p, "default")
        self.mgr._resolve_permission_mode = lambda sd, m: m
        self.mgr.get_or_create_session_data = lambda sid: {"channel": "webui"}
        self.mgr.build_agent_context_prompt = lambda *a, **kw: "test prompt"
        self.mgr._execute_subprocess_with_tracking = fake_execute
        self.mgr.strip_metadata = SessionManager.strip_metadata.__get__(self.mgr)

        self.mgr.run_codex(
            prompt="hello",
            model="gpt-4o",
            agent="orchestrator",
            session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            resume=True,
            n8n_session_id="test-session",
        )
        return captured.get("cmd", [])

    def test_new_session_uses_full_auto_not_p_flag(self):
        """Old -p flag must not appear; --full-auto must be present."""
        cmd = self._capture_cmd_new_session()
        self.assertNotIn(
            "-p", cmd, "Old -p flag still present — causes parse error on v0.125.0"
        )
        self.assertIn("--full-auto", cmd, "--full-auto missing from new session cmd")

    def test_new_session_no_verbose_flag(self):
        """--verbose flag must not appear — removed in v0.125.0."""
        cmd = self._capture_cmd_new_session()
        self.assertNotIn(
            "--verbose", cmd, "--verbose still present — causes parse error on v0.125.0"
        )

    def test_new_session_has_json_flag(self):
        """--json flag required for machine-readable JSONL output."""
        cmd = self._capture_cmd_new_session()
        self.assertIn("--json", cmd, "--json flag missing from new session cmd")

    def test_new_session_has_skip_git_repo_check(self):
        """--skip-git-repo-check prevents failure when not in a git repo."""
        cmd = self._capture_cmd_new_session()
        self.assertIn("--skip-git-repo-check", cmd)

    def test_resume_session_no_p_verbose_flags(self):
        """Resume must not use -p or --verbose."""
        cmd = self._capture_cmd_resume_session()
        self.assertNotIn("-p", cmd)
        self.assertNotIn("--verbose", cmd)

    def test_resume_session_has_json_flag(self):
        cmd = self._capture_cmd_resume_session()
        self.assertIn("--json", cmd)

    def test_resume_session_uses_resume_subcommand(self):
        """v0.125.0 resume uses subcommand: codex exec ... resume <id> <prompt>."""
        cmd = self._capture_cmd_resume_session()
        self.assertIn("resume", cmd, "resume subcommand missing")
        # resume must come AFTER exec (positional subcommand, not a flag)
        exec_idx = cmd.index("exec")
        resume_idx = cmd.index("resume")
        self.assertGreater(resume_idx, exec_idx)

    # ------------------------------------------------------------------
    # strip_metadata: JSONL parsing
    # ------------------------------------------------------------------

    def test_strip_metadata_extracts_jsonl_agent_message(self):
        """strip_metadata must extract text from item.completed JSONL events."""
        mgr = self.mgr
        jsonl = "\n".join(
            [
                '{"type":"thread.started","thread_id":"uuid-1"}',
                '{"type":"turn.started"}',
                '{"type":"item.completed","item":{"id":"i0","type":"agent_message","text":"The answer is 42."}}',
                '{"type":"turn.completed","usage":{}}',
            ]
        )
        result = mgr.strip_metadata(jsonl, "codex")
        self.assertIn("The answer is 42.", result)

    def test_strip_metadata_ignores_non_agent_message_items(self):
        """tool_call and other item types must not be returned as response text."""
        mgr = self.mgr
        jsonl = "\n".join(
            [
                '{"type":"item.completed","item":{"id":"i0","type":"tool_call","text":"internal"}}',
                '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"Real answer."}}',
            ]
        )
        result = mgr.strip_metadata(jsonl, "codex")
        self.assertIn("Real answer.", result)
        self.assertNotIn("internal", result)

    def test_strip_metadata_fallback_marker_parsing(self):
        """Legacy pre-v0.125.0 output (no JSONL) must still parse correctly."""
        mgr = self.mgr
        legacy = "\n".join(
            [
                "OpenAI Codex ...",
                "user",
                "some context",
                "thinking",
                "internal reasoning",
                "codex",
                "Legacy response text.",
                "",
                "tokens used: 42",
            ]
        )
        result = mgr.strip_metadata(legacy, "codex")
        self.assertIn("Legacy response text.", result)

    # ------------------------------------------------------------------
    # Thread ID extraction and session plumbing
    # ------------------------------------------------------------------

    def test_run_codex_stores_thread_id_from_jsonl(self):
        """run_codex() must populate _last_codex_thread_id from thread.started event."""
        expected_tid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        jsonl_output = "\n".join(
            [
                f'{{"type":"thread.started","thread_id":"{expected_tid}"}}',
                '{"type":"turn.started"}',
                '{"type":"item.completed","item":{"id":"i0","type":"agent_message","text":"hi"}}',
                '{"type":"turn.completed","usage":{}}',
            ]
        )

        self.mgr._parse_mode_command = lambda p: (p, "default")
        self.mgr._resolve_permission_mode = lambda sd, m: m
        self.mgr.get_or_create_session_data = lambda sid: {"channel": "webui"}
        self.mgr.build_agent_context_prompt = lambda *a, **kw: "test prompt"
        self.mgr._execute_subprocess_with_tracking = lambda *a, **kw: jsonl_output
        self.mgr.strip_metadata = SessionManager.strip_metadata.__get__(self.mgr)

        self.mgr.run_codex(
            prompt="hi",
            model="gpt-4o",
            agent="orchestrator",
            session_id=None,
            resume=False,
            n8n_session_id="sess1",
        )
        self.assertEqual(
            getattr(self.mgr, "_last_codex_thread_id", None),
            expected_tid,
        )

    def test_get_most_recent_session_id_returns_thread_id(self):
        """get_most_recent_session_id() must return _last_codex_thread_id and consume it."""
        tid = "12345678-1234-1234-1234-123456789abc"
        self.mgr._last_codex_thread_id = tid
        result = self.mgr.get_most_recent_session_id("codex")
        self.assertEqual(result, tid)
        # Must be consumed — second call returns None (no rollout files exist)
        result2 = self.mgr.get_most_recent_session_id("codex")
        self.assertIsNone(result2)

    def test_session_exists_accepts_uuid_format_thread_id(self):
        """session_exists() must return True for UUID-formatted thread IDs (v0.125.0+)."""
        valid_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        self.assertTrue(self.mgr.session_exists(valid_uuid, "codex"))

    def test_session_exists_rejects_non_uuid(self):
        """session_exists() must return False for non-UUID session IDs with no rollout file."""
        self.assertFalse(self.mgr.session_exists("not-a-uuid", "codex"))
        self.assertFalse(self.mgr.session_exists("", "codex"))


if __name__ == "__main__":
    unittest.main()
