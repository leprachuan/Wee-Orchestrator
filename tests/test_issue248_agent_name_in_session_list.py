"""
Regression tests for Issue #248: Display agent name in session list.

Tests verify that:
1. HistoryManager.create_session() stores the agent field
2. get_sessions() returns the agent field
3. update_session_agent() updates the agent field in history
4. Sessions created without agent default to empty string
"""
import sys
import os
import time
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_history_manager():
    """Create a HistoryManager with a temp file."""
    import agent_manager as am
    mgr = am.HistoryManager.__new__(am.HistoryManager)
    # Point to a temp file
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    json.dump({}, tmp)
    tmp.close()
    import threading
    mgr._path = tmp.name
    mgr._lock = threading.Lock()
    return mgr, tmp.name


def test_create_session_stores_agent():
    """Session created with agent arg should have agent in history."""
    mgr, path = _make_history_manager()
    try:
        mgr.create_session("telegram", "testuser", "sess001", agent="wee-dev")
        sessions = mgr.get_sessions("telegram", "testuser")
        assert len(sessions) == 1
        assert (
            sessions[0]["agent"] == "wee-dev"
        ), f"Expected 'wee-dev', got {sessions[0].get('agent')}"
    finally:
        os.unlink(path)


def test_create_session_no_agent_defaults_empty():
    """Session created without agent should have empty agent string."""
    mgr, path = _make_history_manager()
    try:
        mgr.create_session("telegram", "testuser", "sess002")
        sessions = mgr.get_sessions("telegram", "testuser")
        assert len(sessions) == 1
        assert (
            sessions[0]["agent"] == ""
        ), f"Expected empty string, got {sessions[0].get('agent')}"
    finally:
        os.unlink(path)


def test_get_sessions_returns_agent_field():
    """get_sessions() must include agent in returned data."""
    mgr, path = _make_history_manager()
    try:
        mgr.create_session(
            "telegram", "testuser", "sess003", agent="orchestrator"
        )
        sessions = mgr.get_sessions("telegram", "testuser")
        assert "agent" in sessions[0], "agent field missing from get_sessions()"
        assert sessions[0]["agent"] == "orchestrator"
    finally:
        os.unlink(path)


def test_update_session_agent():
    """update_session_agent() should update the agent on an existing session."""
    mgr, path = _make_history_manager()
    try:
        mgr.create_session(
            "telegram", "testuser", "sess004", agent="orchestrator"
        )
        result = mgr.update_session_agent("telegram", "testuser", "sess004", "wee-qa")
        assert result is True, "update_session_agent should return True on success"
        sessions = mgr.get_sessions("telegram", "testuser")
        assert (
            sessions[0]["agent"] == "wee-qa"
        ), f"Expected 'wee-qa', got {sessions[0].get('agent')}"
    finally:
        os.unlink(path)


def test_update_session_agent_returns_false_for_unknown_session():
    """update_session_agent() should return False if session not found."""
    mgr, path = _make_history_manager()
    try:
        result = mgr.update_session_agent(
            "telegram", "testuser", "nonexistent", "wee-dev"
        )
        assert (
            result is False
        ), "update_session_agent should return False for unknown session"
    finally:
        os.unlink(path)


def test_multiple_agents_in_session_list():
    """Multiple sessions with different agents should each show their agent."""
    mgr, path = _make_history_manager()
    try:
        mgr.create_session("telegram", "testuser", "sess010", agent="wee-dev")
        time.sleep(0.01)
        mgr.create_session("telegram", "testuser", "sess011", agent="email-triage")
        time.sleep(0.01)
        mgr.create_session("telegram", "testuser", "sess012", agent="")
        sessions = mgr.get_sessions("telegram", "testuser")
        assert len(sessions) == 3
        # Sessions are sorted newest-first
        by_id = {s["session_id"]: s["agent"] for s in sessions}
        assert by_id["sess010"] == "wee-dev"
        assert by_id["sess011"] == "email-triage"
        assert by_id["sess012"] == ""
    finally:
        os.unlink(path)


def test_agent_field_not_in_messages_leak():
    """get_sessions() should never include messages — agent must still be present."""
    mgr, path = _make_history_manager()
    try:
        mgr.create_session("telegram", "testuser", "sess020", agent="research")
        mgr.append_message("telegram", "testuser", "sess020", "user", "hello")
        sessions = mgr.get_sessions("telegram", "testuser")
        assert "messages" not in sessions[0], "messages must not leak into session list"
        assert sessions[0]["agent"] == "research"
    finally:
        os.unlink(path)
