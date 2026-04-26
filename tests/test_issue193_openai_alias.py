"""Regression tests for Issue #193 Round 2: openai→wee runtime alias.

Tests verify:
1. strip_metadata handles runtime="openai" (passes through clean text like "wee")
2. session_exists handles runtime="openai" (looks up wee_messages)
3. Model defaults resolve correctly when session has runtime="openai"
4. _run_background_task normalizes runtime="openai" to "wee" before dispatching
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")
os.environ.setdefault("API_SHARED_KEY", "test_key_123")

MINIMAL_AGENTS_JSON = {
    "agents": [
        {"name": "orchestrator", "description": "Main orchestrator", "path": "/opt"}
    ]
}


def _make_manager(tmpdir):
    """Create a SessionManager backed by a temp dir."""
    import agent_manager as am

    cfg_path = Path(tmpdir) / "agents.json"
    cfg_path.write_text(json.dumps(MINIMAL_AGENTS_JSON))
    return am.SessionManager(str(cfg_path), app_env="DEV")


class TestIssue193StripMetadataOpenAIAlias(unittest.TestCase):
    """MINOR regression: strip_metadata must handle runtime='openai'."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mgr = _make_manager(self.tmpdir)

    def test_strip_metadata_openai_passes_through_text(self):
        result = self.mgr.strip_metadata("Hello\nWorld", "openai")
        self.assertIn("Hello", result)
        self.assertIn("World", result)

    def test_strip_metadata_openai_matches_wee(self):
        text = "Line one\nLine two\nLine three"
        self.assertEqual(
            self.mgr.strip_metadata(text, "openai"),
            self.mgr.strip_metadata(text, "wee"),
            "runtime='openai' output must match runtime='wee'",
        )


class TestIssue193SessionExistsOpenAIAlias(unittest.TestCase):
    """MINOR regression: session_exists must handle runtime='openai'."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mgr = _make_manager(self.tmpdir)

    def test_session_exists_openai_finds_wee_messages(self):
        """session_exists with runtime='openai' must look up
        wee_messages (same as 'wee').
        """
        session_data = {
            "session_id": "abc-123",
            "runtime": "openai",
            "wee_messages": [{"role": "user", "content": "hello"}],
        }
        sid = "test_openai_session"
        # Patch load_session_data to return our test data
        with patch.object(self.mgr, "load_session_data", return_value=session_data):
            result = self.mgr.session_exists("abc-123", "openai", n8n_session_id=sid)
        self.assertTrue(
            result, "session_exists must return True when wee_messages exist"
        )

    def test_session_exists_openai_false_without_wee_messages(self):
        session_data = {"session_id": "abc-456", "runtime": "openai"}
        sid = "test_openai_no_hist"
        with patch.object(self.mgr, "load_session_data", return_value=session_data):
            result = self.mgr.session_exists("abc-456", "openai", n8n_session_id=sid)
        self.assertFalse(result)

    def test_session_exists_openai_matches_wee_behaviour(self):
        """session_exists(runtime='openai') must return same result
        as (runtime='wee').
        """
        session_data_with = {
            "session_id": "abc-789",
            "wee_messages": [{"role": "user", "content": "hi"}],
        }
        with patch.object(
            self.mgr, "load_session_data", return_value=session_data_with
        ):
            result_openai = self.mgr.session_exists("", "openai", n8n_session_id="s1")
            result_wee = self.mgr.session_exists("", "wee", n8n_session_id="s1")
        self.assertEqual(result_openai, result_wee)


class TestIssue193ModelDefaultsOpenAIAlias(unittest.TestCase):
    """MAJOR regression: model defaults must resolve for sessions
    with runtime='openai'.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mgr = _make_manager(self.tmpdir)

    def test_model_defaults_openai_uses_wee_default_model(self):
        """Session with runtime='openai' and no model must get WEE_DEFAULT_MODEL."""
        session_map = {
            "test_openai_model": {"session_id": "uuid-111", "runtime": "openai"}
        }
        with (
            patch.object(self.mgr, "load_session_map", return_value=session_map),
            patch.object(self.mgr, "save_session_map"),
            patch.dict(os.environ, {"WEE_DEFAULT_MODEL": "ollama/test-model"}),
        ):
            data = self.mgr._get_or_create_session_data_unlocked("test_openai_model")

        self.assertEqual(
            data["model"],
            "ollama/test-model",
            f"Expected WEE_DEFAULT_MODEL but got {data['model']!r} — "
            "runtime='openai' falls through to generic default (regression)",
        )

    def test_model_defaults_openai_matches_wee(self):
        """Model defaults for runtime='openai' must match runtime='wee'."""
        session_map = {
            "sid_wee": {"session_id": "uuid-wee", "runtime": "wee"},
            "sid_openai": {"session_id": "uuid-openai", "runtime": "openai"},
        }
        with (
            patch.object(self.mgr, "load_session_map", return_value=session_map),
            patch.object(self.mgr, "save_session_map"),
            patch.dict(os.environ, {"WEE_DEFAULT_MODEL": "ollama/cmp-model"}),
        ):
            data_wee = self.mgr._get_or_create_session_data_unlocked("sid_wee")
            data_openai = self.mgr._get_or_create_session_data_unlocked("sid_openai")

        self.assertEqual(
            data_wee["model"],
            data_openai["model"],
            f"wee model={data_wee['model']!r} vs openai model={data_openai['model']!r}",
        )


class TestIssue193BackgroundTaskNormalization(unittest.TestCase):
    """MAJOR regression: _run_background_task must normalize
    runtime='openai' to 'wee'.
    """

    def test_run_background_task_source_has_openai_normalization(self):
        """_run_background_task (inside create_api_app) must normalize
        runtime='openai' to 'wee'.


        Uses source inspection — same pattern as test_issue193_dispatch_config.py —
        to confirm the normalization block exists without spawning subprocesses.
        """
        import inspect
        import re

        import agent_manager as am

        src = inspect.getsource(am.create_api_app)

        # The function must explicitly normalize openai to wee
        self.assertIn(
            "openai",
            src,
            "create_api_app source must mention 'openai' runtime somewhere",
        )

        # Look for the exact normalization pattern:
        # if runtime == "openai": runtime = "wee"
        normalization_present = bool(
            re.search(
                r'if\s+runtime\s*==\s*["\']openai["\']\s*'
                r':\s*runtime\s*=\s*["\']wee["\']+',
                src,
            )
        )
        # OR the branch check pattern: elif runtime in ("wee", "openai"):
        branch_present = bool(
            re.search(
                r'elif\s+runtime\s+in\s+\([^)]*["\']wee["\'][^)]*'
                r'["\']openai["\'][^)]*\)',
                src,
            )
        )

        self.assertTrue(
            normalization_present or branch_present,
            "create_api_app source must contain either:\n"
            "  if runtime == 'openai': runtime = 'wee'\n"
            "OR: elif runtime in (...'wee'...'openai'...)\n"
            "to prevent runtime='openai' from falling through to wrong defaults.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
