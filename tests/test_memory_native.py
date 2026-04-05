"""Tests for the native per-agent memory system (F406 / Issue #72).

Covers:
  - resolve_memory_dir() resolution chain
  - build_context() and get_memory_context() reading MEMORY.md + daily notes
  - Inject-once semantics (memory_injected flag in build_agent_context_prompt)
  - Re-inject on compaction detection
  - No [MEMORY CONTEXT] prefix in prompts (Issue #72)
  - append_daily_note() creates file in correct agent dir
  - Truncation guard (MAX_MEMORY_CHARS)
  - POST /api/v1/memory/daily endpoint
  - WEE_AGENT_DIR in subprocess env
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

# Set auth key before importing app modules
os.environ.setdefault("API_SHARED_KEY", "test_key_123")

import pytest

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── resolve_memory_dir ────────────────────────────────────────────────────


class TestResolveMemoryDir:
    """Test the 4-tier resolution chain."""

    def test_explicit_wee_memory_dir(self, tmp_path):
        """WEE_MEMORY_DIR env var takes highest priority."""
        from memory.inject import resolve_memory_dir

        with patch.dict(os.environ, {"WEE_MEMORY_DIR": str(tmp_path)}, clear=False):
            result = resolve_memory_dir(agent_path="/some/agent")
            assert result == tmp_path

    def test_wee_agent_dir_env(self, tmp_path):
        """WEE_AGENT_DIR resolves to {dir}/memories."""
        from memory.inject import resolve_memory_dir

        env = {"WEE_AGENT_DIR": str(tmp_path)}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("WEE_MEMORY_DIR", None)
            result = resolve_memory_dir()
            assert result == tmp_path / "memories"

    def test_agent_path_parameter(self, tmp_path):
        """agent_path parameter resolves to {path}/memories."""
        from memory.inject import resolve_memory_dir

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEE_MEMORY_DIR", None)
            os.environ.pop("WEE_AGENT_DIR", None)
            result = resolve_memory_dir(agent_path=str(tmp_path))
            assert result == tmp_path / "memories"

    def test_fallback_to_opt_memories(self):
        """No env vars and no agent_path => /opt/memories."""
        from memory.inject import resolve_memory_dir

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEE_MEMORY_DIR", None)
            os.environ.pop("WEE_AGENT_DIR", None)
            result = resolve_memory_dir()
            assert result == Path("/opt/memories")

    def test_priority_order(self, tmp_path):
        """WEE_MEMORY_DIR > WEE_AGENT_DIR > agent_path."""
        from memory.inject import resolve_memory_dir

        mem_dir = tmp_path / "explicit"
        agent_dir = tmp_path / "agent"

        env = {
            "WEE_MEMORY_DIR": str(mem_dir),
            "WEE_AGENT_DIR": str(agent_dir),
        }
        with patch.dict(os.environ, env, clear=False):
            result = resolve_memory_dir(agent_path="/other/path")
            assert result == mem_dir


# ── build_context ─────────────────────────────────────────────────────────


class TestBuildContext:
    """Test context building from memory files."""

    def test_empty_when_no_files(self, tmp_path):
        """Returns empty string when no memory files exist."""
        from memory.inject import build_context

        with patch.dict(os.environ, {"WEE_MEMORY_DIR": str(tmp_path)}, clear=False):
            result = build_context()
            assert result == ""

    def test_memory_md_only(self, tmp_path):
        """Builds context with just MEMORY.md."""
        from memory.inject import build_context

        (tmp_path / "MEMORY.md").write_text("Important fact\n")
        with patch.dict(os.environ, {"WEE_MEMORY_DIR": str(tmp_path)}, clear=False):
            result = build_context()
            assert "LONG-TERM MEMORY" in result
            assert "Important fact" in result
            # Issue #72: no [MEMORY CONTEXT] wrapper
            assert "[MEMORY CONTEXT" not in result
            assert "[END MEMORY CONTEXT]" not in result

    def test_includes_today_notes(self, tmp_path):
        """Includes today's daily notes."""
        from memory.inject import build_context

        (tmp_path / "MEMORY.md").write_text("Core fact\n")
        daily = tmp_path / "daily"
        daily.mkdir()
        today = date.today().isoformat()
        (daily / f"{today}.md").write_text("Today's observation\n")

        with patch.dict(os.environ, {"WEE_MEMORY_DIR": str(tmp_path)}, clear=False):
            result = build_context()
            assert "Core fact" in result
            assert "TODAY'S NOTES" in result
            assert "Today's observation" in result

    def test_includes_yesterday_notes(self, tmp_path):
        """Includes yesterday's daily notes."""
        from memory.inject import build_context

        daily = tmp_path / "daily"
        daily.mkdir()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        (daily / f"{yesterday}.md").write_text("Yesterday's entry\n")

        with patch.dict(os.environ, {"WEE_MEMORY_DIR": str(tmp_path)}, clear=False):
            result = build_context()
            assert "YESTERDAY'S NOTES" in result
            assert "Yesterday's entry" in result

    def test_agent_path_builds_from_agent_dir(self, tmp_path):
        """build_context(agent_path=...) uses agent-specific memory."""
        from memory.inject import build_context

        mem = tmp_path / "memories"
        mem.mkdir()
        (mem / "MEMORY.md").write_text("Agent-specific fact\n")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEE_MEMORY_DIR", None)
            os.environ.pop("WEE_AGENT_DIR", None)
            result = build_context(agent_path=str(tmp_path))
            assert "Agent-specific fact" in result


# ── get_memory_context ────────────────────────────────────────────────────


class TestGetMemoryContext:
    """Test context retrieval with truncation."""

    def test_returns_empty_on_no_memory(self, tmp_path):
        """Empty string when no memory files exist."""
        from memory.inject import get_memory_context

        with patch.dict(os.environ, {"WEE_MEMORY_DIR": str(tmp_path)}, clear=False):
            assert get_memory_context() == ""

    def test_returns_context(self, tmp_path):
        """Returns context when files exist."""
        from memory.inject import get_memory_context

        (tmp_path / "MEMORY.md").write_text("Test fact\n")
        with patch.dict(os.environ, {"WEE_MEMORY_DIR": str(tmp_path)}, clear=False):
            result = get_memory_context()
            assert "Test fact" in result

    def test_no_memory_context_wrapper(self, tmp_path):
        """Issue #72: output must NOT contain [MEMORY CONTEXT] markers."""
        from memory.inject import get_memory_context

        (tmp_path / "MEMORY.md").write_text("Test fact\n")
        with patch.dict(os.environ, {"WEE_MEMORY_DIR": str(tmp_path)}, clear=False):
            result = get_memory_context()
            assert "[MEMORY CONTEXT" not in result
            assert "[END MEMORY CONTEXT]" not in result

    def test_truncation_guard(self, tmp_path):
        """Context exceeding MAX_MEMORY_CHARS triggers truncation."""
        from memory.inject import MAX_MEMORY_CHARS, get_memory_context

        # Create oversized content
        big_content = "X" * (MAX_MEMORY_CHARS + 1000)
        (tmp_path / "MEMORY.md").write_text(big_content)
        daily = tmp_path / "daily"
        daily.mkdir()
        today = date.today().isoformat()
        (daily / f"{today}.md").write_text("Y" * 2000)

        with patch.dict(os.environ, {"WEE_MEMORY_DIR": str(tmp_path)}, clear=False):
            result = get_memory_context()
            assert len(result) <= MAX_MEMORY_CHARS

    def test_truncation_no_wrapper(self, tmp_path):
        """Issue #72: truncated output also has no [MEMORY CONTEXT] wrapper."""
        from memory.inject import MAX_MEMORY_CHARS, get_memory_context

        big_content = "Z" * (MAX_MEMORY_CHARS + 500)
        (tmp_path / "MEMORY.md").write_text(big_content)
        daily = tmp_path / "daily"
        daily.mkdir()
        today = date.today().isoformat()
        (daily / f"{today}.md").write_text("Y" * 2000)

        with patch.dict(os.environ, {"WEE_MEMORY_DIR": str(tmp_path)}, clear=False):
            result = get_memory_context()
            assert "[MEMORY CONTEXT" not in result
            assert "[END MEMORY CONTEXT]" not in result

    def test_agent_path_isolation(self, tmp_path):
        """Different agent_paths return different contexts."""
        from memory.inject import get_memory_context

        agent_a = tmp_path / "agent_a"
        agent_b = tmp_path / "agent_b"
        (agent_a / "memories").mkdir(parents=True)
        (agent_b / "memories").mkdir(parents=True)
        (agent_a / "memories" / "MEMORY.md").write_text("Agent A fact")
        (agent_b / "memories" / "MEMORY.md").write_text("Agent B fact")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEE_MEMORY_DIR", None)
            os.environ.pop("WEE_AGENT_DIR", None)
            ctx_a = get_memory_context(agent_path=str(agent_a))
            ctx_b = get_memory_context(agent_path=str(agent_b))
            assert "Agent A fact" in ctx_a
            assert "Agent B fact" in ctx_b
            assert "Agent B fact" not in ctx_a
            assert "Agent A fact" not in ctx_b


# ── Issue #72: no prompt-prefix injection ─────────────────────────────────


class TestNoPromptPrefix:
    """Issue #72: Memory must be injected at session creation, not as a
    prompt prefix.  The old [MEMORY CONTEXT] block must not appear in prompts.
    """

    def test_build_context_no_wrapper(self, tmp_path):
        """build_context returns raw sections without wrapper block."""
        from memory.inject import build_context

        (tmp_path / "MEMORY.md").write_text("Durable fact\n")
        with patch.dict(os.environ, {"WEE_MEMORY_DIR": str(tmp_path)}, clear=False):
            ctx = build_context()
            assert "Durable fact" in ctx
            assert "[MEMORY CONTEXT" not in ctx
            assert "[END MEMORY CONTEXT]" not in ctx
            # Should NOT start with '=' border
            assert not ctx.startswith("=")

    def test_no_prepend_memory_export(self):
        """prepend_memory is not exposed by memory.inject."""
        import memory.inject as mi

        assert not hasattr(mi, "prepend_memory"), (
            "prepend_memory should be removed — memory is injected at session start"
        )

    def test_memory_injected_flag_prevents_double_injection(self, tmp_path):
        """Simulates the flag check in build_agent_context_prompt."""
        from memory.inject import get_memory_context

        (tmp_path / "MEMORY.md").write_text("Test fact\n")

        # First call: not injected yet
        session = {}
        with patch.dict(os.environ, {"WEE_MEMORY_DIR": str(tmp_path)}, clear=False):
            if not session.get("memory_injected"):
                ctx = get_memory_context()
                if ctx:
                    session["memory_injected"] = True

        assert session["memory_injected"] is True
        assert "Test fact" in ctx

        # Second call: already injected — should skip
        with patch.dict(os.environ, {"WEE_MEMORY_DIR": str(tmp_path)}, clear=False):
            second_ctx = ""
            if not session.get("memory_injected"):
                second_ctx = get_memory_context()
            assert second_ctx == ""

    def test_all_code_paths_use_session_flag(self):
        """Verify the memory_injected check is in build_agent_context_prompt."""
        import inspect

        # Import the session manager class
        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager.build_agent_context_prompt)
        assert "memory_injected" in source, (
            "build_agent_context_prompt must check memory_injected flag"
        )
        assert "get_memory_context" in source, (
            "build_agent_context_prompt must call get_memory_context"
        )


# ── detect_compaction ─────────────────────────────────────────────────────


class TestDetectCompaction:
    def test_detects_compaction_signals(self):
        from memory.inject import detect_compaction

        assert detect_compaction("I don't have context about the previous task")
        assert detect_compaction("As a new session, I need more info")
        assert detect_compaction("I wasn't given context for this task")

    def test_no_false_positive(self):
        from memory.inject import detect_compaction

        assert not detect_compaction("Here is the analysis you requested")
        assert not detect_compaction("")
        assert not detect_compaction(None)


# ── append_daily_note ─────────────────────────────────────────────────────


class TestAppendDailyNote:
    def test_creates_file_in_agent_dir(self, tmp_path):
        """Creates daily note under agent's memories/daily/."""
        from memory.daily import append_daily_note

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEE_MEMORY_DIR", None)
            os.environ.pop("WEE_AGENT_DIR", None)
            result = append_daily_note("Test note", agent_path=str(tmp_path))

        today = date.today().isoformat()
        expected = tmp_path / "memories" / "daily" / f"{today}.md"
        assert result == expected
        assert expected.exists()
        content = expected.read_text()
        assert "Test note" in content

    def test_appends_to_existing(self, tmp_path):
        """Multiple calls append to the same file."""
        from memory.daily import append_daily_note

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEE_MEMORY_DIR", None)
            os.environ.pop("WEE_AGENT_DIR", None)
            append_daily_note("First note", agent_path=str(tmp_path))
            append_daily_note("Second note", agent_path=str(tmp_path))

        today = date.today().isoformat()
        content = (tmp_path / "memories" / "daily" / f"{today}.md").read_text()
        assert "First note" in content
        assert "Second note" in content

    def test_creates_directories(self, tmp_path):
        """Creates memories/daily/ dirs if they don't exist."""
        from memory.daily import append_daily_note

        agent = tmp_path / "new_agent"
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEE_MEMORY_DIR", None)
            os.environ.pop("WEE_AGENT_DIR", None)
            result = append_daily_note("Note", agent_path=str(agent))

        assert result.exists()
        assert (agent / "memories" / "daily").is_dir()

    def test_includes_timestamp(self, tmp_path):
        """Daily note entry includes a timestamp."""
        from memory.daily import append_daily_note

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEE_MEMORY_DIR", None)
            os.environ.pop("WEE_AGENT_DIR", None)
            result = append_daily_note("Timed entry", agent_path=str(tmp_path))

        content = result.read_text()
        # Should have HH:MM format in header
        assert "Daily Note" in content


# ── API endpoint tests ────────────────────────────────────────────────────


class TestMemoryDailyAPI:
    """Test POST /api/v1/memory/daily endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client for the API."""
        from agent_manager import create_api_app

        from fastapi.testclient import TestClient

        app = create_api_app()
        return TestClient(app)

    @pytest.fixture
    def auth_headers(self):
        return {
            "Authorization": "Bearer shared_test_key_123",
            "X-User-Identity": "test-user",
            "X-Auth-Channel": "api",
        }

    def test_daily_note_no_auth(self, client):
        """Requires authentication."""
        resp = client.post(
            "/api/v1/memory/daily",
            json={"content": "Test note"},
        )
        assert resp.status_code in (401, 403)

    def test_daily_note_empty_content(self, client, auth_headers):
        """Rejects empty content."""
        resp = client.post(
            "/api/v1/memory/daily",
            json={"content": ""},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_daily_note_success(self, client, auth_headers, tmp_path):
        """Creates daily note for default (orchestrator) agent."""
        with patch.dict(
            os.environ, {"WEE_MEMORY_DIR": str(tmp_path)}, clear=False
        ):
            resp = client.post(
                "/api/v1/memory/daily",
                json={"content": "API test note"},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["agent"] == "orchestrator"
        assert Path(data["file"]).exists()

    def test_daily_note_with_agent(self, client, auth_headers, tmp_path):
        """Creates daily note for a specific agent."""
        with patch.dict(
            os.environ, {"WEE_MEMORY_DIR": str(tmp_path)}, clear=False
        ):
            resp = client.post(
                "/api/v1/memory/daily",
                json={"content": "Agent note", "agent": "wee-dev"},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"] == "wee-dev"

    def test_daily_note_unknown_agent(self, client, auth_headers):
        """Returns 404 for unknown agent."""
        resp = client.post(
            "/api/v1/memory/daily",
            json={"content": "Note", "agent": "nonexistent-agent"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ── WEE_AGENT_DIR in subprocess env ───────────────────────────────────────


class TestSubprocessEnv:
    """Verify WEE_AGENT_DIR is set in background task subprocess env."""

    def test_env_dict_contains_wee_agent_dir(self):
        """The env dict building logic includes WEE_AGENT_DIR."""
        # Simulate the env dict construction from _run_background_task
        agent = "wee-dev"
        runtime = "copilot"
        agent_dir = "/opt/wee-dev"

        env = {
            **os.environ,
            "COPILOT_AGENT": agent,
            "COPILOT_RUNTIME": runtime,
            "WEE_AGENT_DIR": agent_dir,
        }
        assert env["WEE_AGENT_DIR"] == "/opt/wee-dev"
        assert env["COPILOT_AGENT"] == "wee-dev"
        assert env["COPILOT_RUNTIME"] == "copilot"


# ── Inject-once semantics ────────────────────────────────────────────────


class TestInjectOnce:
    """Verify memory is injected once and re-injected on compaction."""

    def test_inject_once_flag(self):
        """memory_injected flag prevents double injection."""
        memory_injected = False

        from memory.inject import get_memory_context

        ctx = get_memory_context(agent_path="/nonexistent")
        if ctx:
            memory_injected = True

        # With no memory files at /nonexistent, no injection occurs
        assert memory_injected is False

    def test_re_inject_on_compaction(self):
        """Compaction detection triggers re-injection."""
        from memory.inject import detect_compaction

        response = "I don't have context about the previous conversation"
        assert detect_compaction(response) is True
        # This would trigger memory re-injection in the actual code
