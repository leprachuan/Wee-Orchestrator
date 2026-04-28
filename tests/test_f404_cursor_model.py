"""Tests for F404: Cursor runtime model validation.

Ensures the cursor CLI always receives --model auto (or a valid cursor
model name) so that free-plan accounts do not get the
"Named models unavailable" error.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_manager import SessionManager


@pytest.fixture
def session_mgr(tmp_path):
    """Create a SessionManager with a minimal agents.json config."""
    agents_config = tmp_path / "agents.json"
    agents_config.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "name": "orchestrator",
                        "description": "test",
                        "path": str(tmp_path),
                    }
                ]
            }
        )
    )
    with patch.dict(os.environ, {"COPILOT_HOME": str(tmp_path)}):
        mgr = SessionManager(str(agents_config))
    return mgr


def _run_cursor_capture_cmd(session_mgr, model, n8n_session_id, env_overrides=None):
    """Helper: call run_cursor with mocked internals, return the cmd list."""
    env = env_overrides or {}
    with (
        patch.dict(os.environ, env),
        patch.object(
            session_mgr,
            "build_agent_context_prompt",
            return_value="mocked prompt",
        ),
        patch.object(
            session_mgr,
            "_execute_subprocess_with_tracking",
            return_value="test output",
        ) as mock_exec,
    ):
        session_mgr.run_cursor(
            prompt="hello",
            model=model,
            agent="orchestrator",
            session_id=None,
            resume=False,
            n8n_session_id=n8n_session_id,
        )
        return mock_exec.call_args[0][0]


class TestRunCursorModelValidation:
    """Tests for model validation inside run_cursor()."""

    def test_empty_model_defaults_to_auto(self, session_mgr):
        cmd = _run_cursor_capture_cmd(session_mgr, "", "test-empty")
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "auto"

    def test_none_model_defaults_to_auto(self, session_mgr):
        cmd = _run_cursor_capture_cmd(session_mgr, None, "test-none")
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "auto"

    def test_invalid_model_defaults_to_auto(self, session_mgr):
        cmd = _run_cursor_capture_cmd(
            session_mgr, "opencode/gpt-5-nano", "test-invalid"
        )
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "auto"

    def test_valid_auto_model_preserved(self, session_mgr):
        cmd = _run_cursor_capture_cmd(session_mgr, "auto", "test-auto")
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "auto"

    def test_named_cursor_model_preserved(self, session_mgr):
        cmd = _run_cursor_capture_cmd(session_mgr, "claude-4-sonnet", "test-named")
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "claude-4-sonnet"

    def test_composer_model_preserved(self, session_mgr):
        cmd = _run_cursor_capture_cmd(session_mgr, "composer-2", "test-composer")
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "composer-2"

    def test_model_flag_always_present(self, session_mgr):
        for model_val in ["", None, "auto", "nonexistent-model"]:
            cmd = _run_cursor_capture_cmd(
                session_mgr, model_val, f"test-always-{model_val}"
            )
            assert "--model" in cmd, f"--model missing for model={model_val!r}"

    def test_env_override_cursor_default_model(self, session_mgr):
        cmd = _run_cursor_capture_cmd(
            session_mgr,
            "",
            "test-env",
            env_overrides={"CURSOR_DEFAULT_MODEL": "composer-2"},
        )
        assert cmd[cmd.index("--model") + 1] == "composer-2"

    def test_copilot_model_rejected(self, session_mgr):
        """A copilot-only model should not reach the cursor CLI."""
        cmd = _run_cursor_capture_cmd(session_mgr, "gpt-5-nano", "test-copilot-model")
        assert cmd[cmd.index("--model") + 1] == "auto"

    def test_print_mode_flag_present(self, session_mgr):
        cmd = _run_cursor_capture_cmd(session_mgr, "auto", "test-flags")
        assert "-p" in cmd
        assert "--trust" in cmd

    def test_workspace_flag_present(self, session_mgr):
        cmd = _run_cursor_capture_cmd(session_mgr, "auto", "test-workspace")
        assert "--workspace" in cmd
