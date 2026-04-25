"""Tests for wee_executor.py — session-aware capability framework.

Covers:
  - Session detection (valid, invalid, missing, env override)
  - Mode detection (interactive, background, sync, api)
  - Capability filtering by mode
  - create_background_task (success, API error, invalid agent, rate limit)
  - get_secret (elevation, name validation, subprocess, backends)
  - register_capability helper
  - list-capabilities and help output
  - Exit codes (0 success, 1 general, 2 mode restriction, 3 API error)
"""

import json
import os
import sys
from pathlib import Path

from unittest.mock import MagicMock, patch

import pytest

# Ensure API_SHARED_KEY is set for test imports
os.environ.setdefault("API_SHARED_KEY", "test_key_123")

# Allow importing from scripts/
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import wee_executor


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """Clear WEE_* env vars and redirect logs to tmp for each test."""
    for var in (
        "WEE_SESSION_ID",
        "SESSION_ID",
        "WEE_TASK_ID",
        "WEE_TASK_SYNC",
        "WEE_RUNTIME",
        "WEE_IDENTITY",
        "WEE_CHANNEL",
        "WEE_API_URL",
        "API_PORT",
        "WEE_ELEVATED",
    ):
        monkeypatch.delenv(var, raising=False)
    # Redirect log/rate-limit files to tmp
    monkeypatch.setattr(wee_executor, "LOG_DIR", tmp_path)
    monkeypatch.setattr(wee_executor, "LOG_FILE", tmp_path / "wee_executor.log")
    monkeypatch.setattr(
        wee_executor, "RATE_LIMIT_FILE", tmp_path / ".rate_limits.json"
    )


@pytest.fixture
def mock_agents_json(tmp_path):
    """Create a temporary agents.json with known agents."""
    agents = {
        "agents": [
            {"name": "orchestrator", "path": "/opt/orchestrator"},
            {"name": "research", "path": "/opt/research"},
            {"name": "devops", "path": "/opt/devops"},
            {"name": "email_triage", "path": "/opt/email_triage"},
        ]
    }
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(agents))
    return path


@pytest.fixture
def mock_sessions_json(tmp_path):
    """Create a temporary sessions.json with a known session."""
    sessions = {
        "session-abc-123": {
            "identity": "user_test",
            "channel": "telegram",
            "created_at": 1700000000,
        },
        "session-def-456": {
            "identity": "user_test_2",
            "channel": "webui",
            "created_at": 1700000100,
        },
    }
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps(sessions))
    return path


# ── Session Detection Tests ───────────────────────────────────────────


class TestSessionDetection:
    """Tests for detect_session() and helpers."""

    def test_session_from_wee_session_id(self, monkeypatch):
        """WEE_SESSION_ID env takes priority."""
        monkeypatch.setenv("WEE_SESSION_ID", "my-session-1")
        sid, mode, rt = wee_executor.detect_session()
        assert sid == "my-session-1"
        assert mode == wee_executor.MODE_INTERACTIVE
        assert rt == "copilot"

    def test_session_from_session_id_fallback(self, monkeypatch):
        """SESSION_ID env var is used if WEE_SESSION_ID is not set."""
        monkeypatch.setenv("SESSION_ID", "legacy-session")
        sid, mode, rt = wee_executor.detect_session()
        assert sid == "legacy-session"
        assert mode == wee_executor.MODE_INTERACTIVE

    def test_session_from_sessions_json(self, monkeypatch, mock_sessions_json):
        """Falls back to most recent session from sessions.json."""
        monkeypatch.setattr(wee_executor, "SESSIONS_JSON", mock_sessions_json)
        sid, mode, rt = wee_executor.detect_session()
        # Should pick session-def-456 (higher created_at)
        assert sid == "session-def-456"
        assert mode == wee_executor.MODE_INTERACTIVE

    def test_session_not_found_api_mode(self, monkeypatch, tmp_path):
        """No session → api mode."""
        monkeypatch.setattr(wee_executor, "SESSIONS_JSON", tmp_path / "nonexistent.json")
        sid, mode, rt = wee_executor.detect_session()
        assert sid is None
        assert mode == wee_executor.MODE_API


# ── Mode Detection Tests ──────────────────────────────────────────────


class TestModeDetection:
    """Tests for _detect_mode()."""

    def test_interactive_mode(self):
        """Default mode with a session is interactive."""
        assert wee_executor._detect_mode("some-session") == wee_executor.MODE_INTERACTIVE

    def test_background_mode(self, monkeypatch):
        """WEE_TASK_ID → background mode."""
        monkeypatch.setenv("WEE_TASK_ID", "bg_123")
        assert wee_executor._detect_mode("some-session") == wee_executor.MODE_BACKGROUND

    def test_sync_mode(self, monkeypatch):
        """WEE_TASK_ID + WEE_TASK_SYNC → sync mode."""
        monkeypatch.setenv("WEE_TASK_ID", "bg_123")
        monkeypatch.setenv("WEE_TASK_SYNC", "1")
        assert wee_executor._detect_mode("some-session") == wee_executor.MODE_SYNC

    def test_api_mode_no_session(self):
        """No session → api mode."""
        assert wee_executor._detect_mode(None) == wee_executor.MODE_API


# ── Capability Filtering Tests ────────────────────────────────────────


class TestCapabilityFiltering:
    """Tests for mode-based capability filtering."""

    def test_create_bg_task_allowed_interactive(self):
        """create_background_task is allowed in interactive mode."""
        caps = wee_executor.list_capabilities(wee_executor.MODE_INTERACTIVE)
        names = [c["name"] for c in caps]
        assert "create_background_task" in names

    def test_create_bg_task_allowed_sync(self):
        """create_background_task is allowed in sync mode."""
        caps = wee_executor.list_capabilities(wee_executor.MODE_SYNC)
        names = [c["name"] for c in caps]
        assert "create_background_task" in names

    def test_create_bg_task_denied_background(self):
        """create_background_task is NOT allowed in background mode."""
        caps = wee_executor.list_capabilities(wee_executor.MODE_BACKGROUND)
        names = [c["name"] for c in caps]
        assert "create_background_task" not in names

    def test_create_bg_task_denied_api(self):
        """create_background_task is NOT allowed in api mode."""
        caps = wee_executor.list_capabilities(wee_executor.MODE_API)
        names = [c["name"] for c in caps]
        assert "create_background_task" not in names

    def test_get_secret_allowed_interactive(self):
        """get_secret is allowed in interactive mode."""
        caps = wee_executor.list_capabilities(wee_executor.MODE_INTERACTIVE)
        names = [c["name"] for c in caps]
        assert "get_secret" in names

    def test_get_secret_allowed_sync(self):
        """get_secret is allowed in sync mode."""
        caps = wee_executor.list_capabilities(wee_executor.MODE_SYNC)
        names = [c["name"] for c in caps]
        assert "get_secret" in names

    def test_get_secret_denied_background(self):
        """get_secret is NOT allowed in background mode."""
        caps = wee_executor.list_capabilities(wee_executor.MODE_BACKGROUND)
        names = [c["name"] for c in caps]
        assert "get_secret" not in names

    def test_get_secret_denied_api(self):
        """get_secret is NOT allowed in api mode."""
        caps = wee_executor.list_capabilities(wee_executor.MODE_API)
        names = [c["name"] for c in caps]
        assert "get_secret" not in names


# ── register_capability Tests ─────────────────────────────────────────


class TestRegisterCapability:
    """Tests for register_capability() helper."""

    def test_register_new_capability(self):
        """Register and verify a new capability appears in the registry."""
        def dummy_handler(args, sid, mode):
            return {"ok": True}

        wee_executor.register_capability(
            name="test_cap",
            handler=dummy_handler,
            allowed_modes=[wee_executor.MODE_INTERACTIVE],
            description="A test capability",
            required_args=["foo"],
            optional_args=["bar"],
        )

        assert "test_cap" in wee_executor.CAPABILITIES
        cap = wee_executor.CAPABILITIES["test_cap"]
        assert cap["description"] == "A test capability"
        assert cap["required_args"] == ["foo"]
        assert cap["modes"] == [wee_executor.MODE_INTERACTIVE]

        # Clean up
        del wee_executor.CAPABILITIES["test_cap"]


# ── create_background_task Tests ──────────────────────────────────────


class TestCreateBackgroundTask:
    """Tests for cap_create_background_task()."""

    def test_missing_required_fields(self):
        """Missing agent or prompt returns MISSING_FIELDS."""
        result = wee_executor.cap_create_background_task(
            {"agent": "research"}, "session-1", wee_executor.MODE_INTERACTIVE
        )
        assert "error" in result
        assert result["code"] == "MISSING_FIELDS"

    def test_invalid_agent(self, monkeypatch, mock_agents_json):
        """Unknown agent returns INVALID_AGENT."""
        monkeypatch.setattr(
            wee_executor, "DEFAULT_AGENTS_JSON", mock_agents_json
        )
        result = wee_executor.cap_create_background_task(
            {"agent": "nonexistent", "prompt": "hello"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        assert "error" in result
        assert result["code"] == "INVALID_AGENT"

    @patch.object(wee_executor, "_api_request")
    def test_success_creates_task(self, mock_api, monkeypatch, mock_agents_json):
        """Successful task creation returns task_id and status."""
        monkeypatch.setattr(
            wee_executor, "DEFAULT_AGENTS_JSON", mock_agents_json
        )
        monkeypatch.setattr(wee_executor, "_get_api_key", lambda: "test_key")

        # First call = POST, second = GET verify
        mock_api.side_effect = [
            {"task_id": "bg_abc123", "status": "running"},
            {"task_id": "bg_abc123", "status": "running"},
        ]

        result = wee_executor.cap_create_background_task(
            {"agent": "research", "prompt": "test task"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        assert result["task_id"] == "bg_abc123"
        assert result["status"] == "running"
        assert result["agent"] == "research"
        assert result["session_id"] == "session-1"
        assert "monitor_url" in result

    @patch.object(wee_executor, "_api_request")
    def test_api_error(self, mock_api, monkeypatch, mock_agents_json):
        """API errors are propagated."""
        monkeypatch.setattr(
            wee_executor, "DEFAULT_AGENTS_JSON", mock_agents_json
        )
        monkeypatch.setattr(wee_executor, "_get_api_key", lambda: "test_key")

        mock_api.return_value = {
            "error": "HTTP 500: Internal Server Error",
            "code": "HTTP_500",
        }

        result = wee_executor.cap_create_background_task(
            {"agent": "research", "prompt": "test"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        assert "error" in result
        assert result["code"] == "HTTP_500"

    def test_rate_limit(self, monkeypatch, mock_agents_json, tmp_path):
        """Rate limiter blocks after MAX_RATE_PER_MINUTE calls."""
        monkeypatch.setattr(
            wee_executor, "DEFAULT_AGENTS_JSON", mock_agents_json
        )
        # Pre-fill rate limit file with max calls
        import time

        now = time.time()
        limits = {"session-1": [now - i for i in range(wee_executor.MAX_RATE_PER_MINUTE)]}
        rate_file = tmp_path / ".rate_limits.json"
        rate_file.write_text(json.dumps(limits))
        monkeypatch.setattr(wee_executor, "RATE_LIMIT_FILE", rate_file)

        result = wee_executor.cap_create_background_task(
            {"agent": "research", "prompt": "test"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        assert "error" in result
        assert result["code"] == "RATE_LIMITED"


# ── CLI/Main Tests ────────────────────────────────────────────────────


class TestCLI:
    """Tests for main() argument parsing and exit codes."""

    def test_list_capabilities_json(self, monkeypatch, capsys):
        """--list-capabilities --json outputs valid JSON."""
        monkeypatch.setenv("WEE_SESSION_ID", "test-session")
        monkeypatch.setattr(
            "sys.argv",
            ["wee_executor.py", "--list-capabilities", "--json"],
        )
        wee_executor.main()
        captured = capsys.readouterr()
        caps = json.loads(captured.out)
        assert isinstance(caps, list)
        assert len(caps) >= 1
        assert caps[0]["name"] == "create_background_task"

    def test_list_capabilities_runs(self, monkeypatch, capsys):
        """--list-capabilities produces text output without error."""
        monkeypatch.setenv("WEE_SESSION_ID", "test-session")
        monkeypatch.setattr(
            "sys.argv",
            ["wee_executor.py", "--list-capabilities"],
        )
        # Should not raise
        wee_executor.main()
        captured = capsys.readouterr()
        assert "create_background_task" in captured.out

    def test_unknown_capability_exit_1(self, monkeypatch, capsys):
        """Unknown capability exits with code 1."""
        monkeypatch.setenv("WEE_SESSION_ID", "test-session")
        monkeypatch.setattr(
            "sys.argv",
            ["wee_executor.py", "-c", "nonexistent_cap"],
        )
        with pytest.raises(SystemExit) as exc_info:
            wee_executor.main()
        assert exc_info.value.code == 1

    def test_mode_restricted_exit_2(self, monkeypatch, capsys):
        """Capability not allowed in mode exits with code 2."""
        monkeypatch.setenv("WEE_TASK_ID", "bg_123")
        monkeypatch.setenv("WEE_SESSION_ID", "test-session")
        monkeypatch.setattr(
            "sys.argv",
            [
                "wee_executor.py",
                "-c",
                "create_background_task",
                "-a",
                '{"agent": "research", "prompt": "test"}',
            ],
        )
        with pytest.raises(SystemExit) as exc_info:
            wee_executor.main()
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["code"] == "MODE_RESTRICTED"

    @patch.object(wee_executor, "_api_request")
    def test_api_error_exit_3(self, mock_api, monkeypatch, capsys, mock_agents_json):
        """API error exits with code 3."""
        monkeypatch.setenv("WEE_SESSION_ID", "test-session")
        monkeypatch.setattr(
            wee_executor, "DEFAULT_AGENTS_JSON", mock_agents_json
        )
        monkeypatch.setattr(wee_executor, "_get_api_key", lambda: "test_key")
        mock_api.return_value = {
            "error": "Connection failed: Connection refused",
            "code": "CONNECTION_FAILED",
        }
        monkeypatch.setattr(
            "sys.argv",
            [
                "wee_executor.py",
                "-c",
                "create_background_task",
                "-a",
                '{"agent": "research", "prompt": "test"}',
            ],
        )
        with pytest.raises(SystemExit) as exc_info:
            wee_executor.main()
        assert exc_info.value.code == 3


# ── Security Tests ────────────────────────────────────────────────────


class TestSecurity:
    """Tests for HMAC signing and agent validation."""

    def test_hmac_sign_deterministic(self):
        """HMAC signature is deterministic for same inputs."""
        sig1 = wee_executor._hmac_sign("payload", "key")
        sig2 = wee_executor._hmac_sign("payload", "key")
        assert sig1 == sig2
        assert len(sig1) == 64  # SHA-256 hex

    def test_hmac_sign_different_for_different_inputs(self):
        """Different payloads produce different signatures."""
        sig1 = wee_executor._hmac_sign("payload1", "key")
        sig2 = wee_executor._hmac_sign("payload2", "key")
        assert sig1 != sig2

    def test_validate_agent_valid(self, monkeypatch, mock_agents_json):
        """Valid agent passes validation."""
        monkeypatch.setattr(
            wee_executor, "DEFAULT_AGENTS_JSON", mock_agents_json
        )
        assert wee_executor._validate_agent("research") is True

    def test_validate_agent_invalid(self, monkeypatch, mock_agents_json):
        """Invalid agent fails validation."""
        monkeypatch.setattr(
            wee_executor, "DEFAULT_AGENTS_JSON", mock_agents_json
        )
        assert wee_executor._validate_agent("nonexistent") is False


# ── Identity Resolution Tests ─────────────────────────────────────────


class TestIdentityResolution:
    """Tests for _resolve_identity()."""

    def test_identity_from_env(self, monkeypatch):
        """WEE_IDENTITY env var takes priority."""
        monkeypatch.setenv("WEE_IDENTITY", "test_user_42")
        monkeypatch.setenv("WEE_CHANNEL", "webui")
        identity, channel = wee_executor._resolve_identity()
        assert identity == "test_user_42"
        assert channel == "webui"

    def test_identity_from_sessions_json(self, monkeypatch, mock_sessions_json):
        """Falls back to sessions.json for identity."""
        monkeypatch.setattr(wee_executor, "SESSIONS_JSON", mock_sessions_json)
        identity, channel = wee_executor._resolve_identity()
        # Most recent session is session-def-456 with user_test_2
        assert identity == "user_test_2"
        assert channel == "webui"

    def test_identity_default_empty(self, monkeypatch, tmp_path):
        """Returns empty identity when nothing is available."""
        monkeypatch.setattr(wee_executor, "SESSIONS_JSON", tmp_path / "nope.json")
        identity, channel = wee_executor._resolve_identity()
        assert identity == ""
        assert channel == "api"


# ── get_secret Tests ──────────────────────────────────────────────────


class TestGetSecret:
    """Tests for cap_get_secret() capability."""

    def test_missing_name_field(self):
        """Missing name returns MISSING_FIELDS."""
        result = wee_executor.cap_get_secret(
            {}, "session-1", wee_executor.MODE_INTERACTIVE
        )
        assert "error" in result
        assert result["code"] == "MISSING_FIELDS"

    def test_invalid_name_path_traversal(self):
        """Path traversal in name returns INVALID_NAME."""
        result = wee_executor.cap_get_secret(
            {"name": "../etc/passwd"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        assert result["code"] == "INVALID_NAME"

    def test_invalid_name_spaces(self):
        """Spaces in name returns INVALID_NAME."""
        result = wee_executor.cap_get_secret(
            {"name": "has spaces"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        assert result["code"] == "INVALID_NAME"

    def test_no_elevation_returns_error(self):
        """Without WEE_ELEVATED, returns ELEVATION_REQUIRED."""
        result = wee_executor.cap_get_secret(
            {"name": "valid_key"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        assert result["code"] == "ELEVATION_REQUIRED"
        assert "elevated mode" in result["error"].lower()

    def test_elevation_false_returns_error(self, monkeypatch):
        """WEE_ELEVATED=false still returns ELEVATION_REQUIRED."""
        monkeypatch.setenv("WEE_ELEVATED", "false")
        result = wee_executor.cap_get_secret(
            {"name": "valid_key"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        assert result["code"] == "ELEVATION_REQUIRED"

    def test_invalid_backend(self, monkeypatch):
        """Invalid backend returns INVALID_BACKEND."""
        monkeypatch.setenv("WEE_ELEVATED", "true")
        result = wee_executor.cap_get_secret(
            {"name": "valid_key", "backend": "mysql"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        assert result["code"] == "INVALID_BACKEND"

    @patch("subprocess.run")
    def test_success_keyring(self, mock_run, monkeypatch):
        """Successful secret retrieval from keyring backend."""
        monkeypatch.setenv("WEE_ELEVATED", "true")
        mock_run.return_value = MagicMock(
            returncode=0, stdout="my_secret_value\n", stderr=""
        )
        result = wee_executor.cap_get_secret(
            {"name": "db_password"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        assert result["status"] == "success"
        assert result["value"] == "my_secret_value"
        assert result["name"] == "db_password"
        assert result["backend"] == "keyring"

    @patch("subprocess.run")
    def test_success_file_backend(self, mock_run, monkeypatch):
        """Successful secret retrieval from file backend."""
        monkeypatch.setenv("WEE_ELEVATED", "true")
        mock_run.return_value = MagicMock(
            returncode=0, stdout="file_secret\n", stderr=""
        )
        result = wee_executor.cap_get_secret(
            {"name": "api_token", "backend": "file"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        assert result["status"] == "success"
        assert result["value"] == "file_secret"
        assert result["backend"] == "file"

    @patch("subprocess.run")
    def test_subprocess_calls_secret_tool(self, mock_run, monkeypatch):
        """Verify subprocess calls secret_tool.py with correct args."""
        monkeypatch.setenv("WEE_ELEVATED", "true")
        mock_run.return_value = MagicMock(
            returncode=0, stdout="val", stderr=""
        )
        wee_executor.cap_get_secret(
            {"name": "my_key", "backend": "file"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert any("secret_tool.py" in str(c) for c in cmd)
        assert "get" in cmd
        assert "--name" in cmd
        name_idx = cmd.index("--name")
        assert cmd[name_idx + 1] == "my_key"
        assert "--backend" in cmd
        backend_idx = cmd.index("--backend")
        assert cmd[backend_idx + 1] == "file"

    @patch("subprocess.run")
    def test_not_found_json(self, mock_run, monkeypatch):
        """Secret not found returns NOT_FOUND (JSON error from tool)."""
        monkeypatch.setenv("WEE_ELEVATED", "true")
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout='{"status": "failure", "message": "not found"}',
            stderr="",
        )
        result = wee_executor.cap_get_secret(
            {"name": "missing_key"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        assert result["code"] == "NOT_FOUND"

    @patch("subprocess.run")
    def test_subprocess_timeout(self, mock_run, monkeypatch):
        """Subprocess timeout returns TIMEOUT."""
        monkeypatch.setenv("WEE_ELEVATED", "true")
        import subprocess as sp

        mock_run.side_effect = sp.TimeoutExpired(cmd="test", timeout=10)
        result = wee_executor.cap_get_secret(
            {"name": "slow_key"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        assert result["code"] == "TIMEOUT"

    @patch("subprocess.run")
    def test_tool_not_found(self, mock_run, monkeypatch):
        """Missing secret_tool.py returns TOOL_NOT_FOUND."""
        monkeypatch.setenv("WEE_ELEVATED", "true")
        mock_run.side_effect = FileNotFoundError("No such file")
        result = wee_executor.cap_get_secret(
            {"name": "some_key"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        assert result["code"] == "TOOL_NOT_FOUND"

    def test_rate_limit(self, monkeypatch, tmp_path):
        """Rate-limited get_secret returns RATE_LIMITED."""
        monkeypatch.setenv("WEE_ELEVATED", "true")
        import time as t

        now = t.time()
        limits = {"session-rl": [now - i for i in range(10)]}
        rate_file = tmp_path / ".rate_limits.json"
        rate_file.write_text(json.dumps(limits))
        monkeypatch.setattr(wee_executor, "RATE_LIMIT_FILE", rate_file)

        result = wee_executor.cap_get_secret(
            {"name": "rate_test"},
            "session-rl",
            wee_executor.MODE_INTERACTIVE,
        )
        assert result["code"] == "RATE_LIMITED"

    @patch("subprocess.run")
    def test_elevation_with_1(self, mock_run, monkeypatch):
        """WEE_ELEVATED=1 also grants elevation."""
        monkeypatch.setenv("WEE_ELEVATED", "1")
        mock_run.return_value = MagicMock(
            returncode=0, stdout="secret_val", stderr=""
        )
        result = wee_executor.cap_get_secret(
            {"name": "key_1"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        assert result["status"] == "success"

    def test_valid_name_with_dots_hyphens(self, monkeypatch):
        """Names with dots and hyphens are valid (elevation still needed)."""
        result = wee_executor.cap_get_secret(
            {"name": "my.api-key_v2"},
            "session-1",
            wee_executor.MODE_INTERACTIVE,
        )
        # Should pass name validation, fail on elevation
        assert result["code"] == "ELEVATION_REQUIRED"

    def test_cli_get_secret_exit_1_no_elevation(self, monkeypatch, capsys):
        """CLI get_secret without elevation exits with code 1."""
        monkeypatch.setenv("WEE_SESSION_ID", "test-session")
        monkeypatch.setattr(
            "sys.argv",
            [
                "wee_executor.py",
                "-c",
                "get_secret",
                "-a",
                '{"name": "test_key"}',
            ],
        )
        with pytest.raises(SystemExit) as exc_info:
            wee_executor.main()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["code"] == "ELEVATION_REQUIRED"

    @patch("subprocess.run")
    def test_cli_get_secret_success(self, mock_run, monkeypatch, capsys):
        """CLI get_secret with elevation succeeds."""
        monkeypatch.setenv("WEE_SESSION_ID", "test-session")
        monkeypatch.setenv("WEE_ELEVATED", "true")
        mock_run.return_value = MagicMock(
            returncode=0, stdout="the_secret", stderr=""
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "wee_executor.py",
                "-c",
                "get_secret",
                "-a",
                '{"name": "prod_key"}',
            ],
        )
        wee_executor.main()
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["status"] == "success"
        assert output["value"] == "the_secret"

    def test_cli_get_secret_mode_restricted_background(
        self, monkeypatch, capsys
    ):
        """get_secret is mode-restricted in background mode."""
        monkeypatch.setenv("WEE_SESSION_ID", "test-session")
        monkeypatch.setenv("WEE_TASK_ID", "bg_123")
        monkeypatch.setattr(
            "sys.argv",
            [
                "wee_executor.py",
                "-c",
                "get_secret",
                "-a",
                '{"name": "test_key"}',
            ],
        )
        with pytest.raises(SystemExit) as exc_info:
            wee_executor.main()
        assert exc_info.value.code == 2
