"""Tests for F017: In-thread notification when a background task completes."""
import json
import os
import sys
import threading
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

os.environ.setdefault("API_SHARED_KEY", "test_key_f017")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "8099")


# ---------------------------------------------------------------------------
# BackgroundTaskManager unit tests
# ---------------------------------------------------------------------------

class TestBgEventsStorage:
    """Test push_bg_event / pop_bg_events on BackgroundTaskManager."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        from agent_manager import BackgroundTaskManager

        self.mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
        self.mgr._path = str(tmp_path / "bg-tasks.json")
        self.mgr._lock = threading.Lock()
        self.mgr._bg_events = {}
        self.mgr._bg_events_lock = threading.Lock()
        with open(self.mgr._path, "w") as f:
            json.dump([], f)

    def test_push_and_pop_events(self):
        ev = {"task_id": "bg_abc", "status": "completed", "summary": "hi"}
        self.mgr.push_bg_event("sess-1", ev)
        assert self.mgr.pop_bg_events("sess-1") == [ev]
        assert self.mgr.pop_bg_events("sess-1") == []

    def test_push_ignores_none_session(self):
        self.mgr.push_bg_event(None, {"task_id": "x"})
        assert self.mgr._bg_events == {}

    def test_push_ignores_empty_session(self):
        self.mgr.push_bg_event("", {"task_id": "x"})
        assert self.mgr._bg_events == {}

    def test_pop_nonexistent_session(self):
        assert self.mgr.pop_bg_events("nonexistent") == []

    def test_multiple_events_same_session(self):
        self.mgr.push_bg_event("s1", {"task_id": "a"})
        self.mgr.push_bg_event("s1", {"task_id": "b"})
        events = self.mgr.pop_bg_events("s1")
        assert len(events) == 2
        assert events[0]["task_id"] == "a"
        assert events[1]["task_id"] == "b"

    def test_events_isolated_between_sessions(self):
        self.mgr.push_bg_event("s1", {"task_id": "a"})
        self.mgr.push_bg_event("s2", {"task_id": "b"})
        assert self.mgr.pop_bg_events("s1") == [{"task_id": "a"}]
        assert self.mgr.pop_bg_events("s2") == [{"task_id": "b"}]

    def test_thread_safety(self):
        errors = []

        def push_many(session, count):
            try:
                for i in range(count):
                    self.mgr.push_bg_event(
                        session, {"task_id": f"t{i}"}
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=push_many, args=("s1", 50)),
            threading.Thread(target=push_many, args=("s1", 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(self.mgr.pop_bg_events("s1")) == 100


class TestCreateTaskOriginSession:
    """Test that create_task stores origin_session_id."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        from agent_manager import BackgroundTaskManager

        self.mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
        self.mgr._path = str(tmp_path / "bg-tasks.json")
        self.mgr._lock = threading.Lock()
        self.mgr._bg_events = {}
        self.mgr._bg_events_lock = threading.Lock()
        with open(self.mgr._path, "w") as f:
            json.dump([], f)

    def test_origin_session_id_stored(self):
        task = self.mgr.create_task(
            task_id="bg_test1",
            session_id="sess-bg",
            user_identity="user1",
            channel="webui",
            agent="orchestrator",
            runtime="copilot",
            model="test",
            prompt="test prompt",
            origin_session_id="chat-session-123",
        )
        assert task["origin_session_id"] == "chat-session-123"
        loaded = self.mgr.get_task("bg_test1")
        assert loaded["origin_session_id"] == "chat-session-123"

    def test_origin_session_id_default_none(self):
        task = self.mgr.create_task(
            task_id="bg_test2",
            session_id="sess-bg2",
            user_identity="user1",
            channel="webui",
            agent="orchestrator",
            runtime="copilot",
            model="test",
            prompt="test prompt",
        )
        assert task["origin_session_id"] is None


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestBgEventsEndpoint:
    """Test GET /api/v1/sessions/{session_id}/bg-events."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from unittest.mock import patch as mock_patch

        from fastapi.testclient import TestClient

        import agent_manager

        self._telegram_patch = mock_patch.object(
            agent_manager,
            "_resolve_telegram_identity",
            side_effect=lambda identity: identity,
        )
        self._telegram_patch.start()
        self._send_patch = mock_patch.object(
            agent_manager,
            "_send_pairing_code",
            return_value=True,
        )
        self._send_patch.start()

        self.app = agent_manager.create_api_app()
        self.client = TestClient(self.app)
        key = os.environ.get("API_SHARED_KEY", "test_key_f017")
        self.headers = {
            "Authorization": f"Bearer shared_{key}",
            "X-User-Identity": "test_user",
            "X-Auth-Channel": "webui",
        }
        yield
        self._telegram_patch.stop()
        self._send_patch.stop()

    def test_bg_events_returns_empty(self):
        resp = self.client.get(
            "/api/v1/sessions/no-such-session/bg-events",
            headers=self.headers,
        )
        assert resp.status_code == 200
        assert resp.json()["events"] == []

    def test_bg_events_returns_and_clears(self):
        bg_mgr = self.app.state.bg_task_mgr
        bg_mgr.push_bg_event(
            "test-sess-f017",
            {
                "task_id": "bg_test",
                "summary": "test",
                "status": "completed",
            },
        )
        resp = self.client.get(
            "/api/v1/sessions/test-sess-f017/bg-events",
            headers=self.headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 1
        assert data["events"][0]["task_id"] == "bg_test"
        assert data["events"][0]["status"] == "completed"
        # Second call returns empty
        resp2 = self.client.get(
            "/api/v1/sessions/test-sess-f017/bg-events",
            headers=self.headers,
        )
        assert resp2.json()["events"] == []

    def test_bg_events_requires_auth(self):
        resp = self.client.get(
            "/api/v1/sessions/any-session/bg-events"
        )
        assert resp.status_code in (401, 403)
