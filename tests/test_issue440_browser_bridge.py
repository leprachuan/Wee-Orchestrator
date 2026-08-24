import os
import threading
import time

import pytest

from browser_bridge import NativeBrowserBroker, execute_browser_command


def test_native_browser_commands_are_session_scoped():
    broker = NativeBrowserBroker(active_ttl=10)
    broker.register("session-a", "user", "macos", "client-a")
    broker.register("session-b", "user", "macos", "client-b")
    received = {}

    def client():
        command = broker.poll("session-a", "user", "macos", "client-a", 2)
        received.update(command)
        broker.submit_result(
            "session-a",
            "user",
            "macos",
            "client-a",
            command["id"],
            result={"text": "Session A"},
            url="https://example.com/a",
        )

    thread = threading.Thread(target=client)
    thread.start()
    result = broker.execute("session-a", {"action": "snapshot"}, timeout=2)
    thread.join(timeout=2)

    assert received["action"] == "snapshot"
    assert result["result"] == {"text": "Session A"}
    assert broker.poll("session-b", "user", "macos", "client-b", 0) is None


def test_registration_cannot_be_claimed_by_another_user():
    broker = NativeBrowserBroker()
    broker.register("session-a", "alice", "macos", "client-a")
    with pytest.raises(PermissionError):
        broker.register("session-a", "bob", "macos", "client-b")


def test_inactive_native_browser_falls_back_to_playwright(monkeypatch):
    from browser_bridge import native_browser_broker, playwright_browser_manager

    monkeypatch.setattr(native_browser_broker, "is_active", lambda _session: False)
    monkeypatch.setattr(
        playwright_browser_manager,
        "execute",
        lambda session, command: {"result": session, "url": command["url"]},
    )

    result = execute_browser_command(
        "web-session", {"action": "navigate", "url": "https://example.com"}
    )
    assert '"source": "playwright"' in result
    assert '"result": "web-session"' in result


def test_native_poll_refreshes_liveness():
    broker = NativeBrowserBroker(active_ttl=0.05)
    broker.register("session-a", "user", "macos", "client-a")
    time.sleep(0.03)
    assert broker.poll("session-a", "user", "macos", "client-a", 0) is None
    assert broker.is_active("session-a")


def test_rejected_native_browser_falls_back_to_playwright(monkeypatch):
    from browser_bridge import native_browser_broker, playwright_browser_manager

    monkeypatch.setattr(native_browser_broker, "is_active", lambda _session: True)
    monkeypatch.setattr(
        native_browser_broker,
        "execute",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("shared browser rejected the command")
        ),
    )
    monkeypatch.setattr(
        playwright_browser_manager,
        "execute",
        lambda session, command: {"result": session, "url": command["url"]},
    )

    result = execute_browser_command(
        "ios-session", {"action": "navigate", "url": "https://example.com"}
    )
    assert '"source": "playwright-fallback"' in result


def test_browser_api_requires_a_real_owned_session(monkeypatch, tmp_path):
    monkeypatch.setenv("API_SHARED_KEY", "issue440")
    monkeypatch.setenv("APP_ENV", "DEV")

    import agent_manager
    from fastapi.testclient import TestClient

    client = TestClient(agent_manager.create_api_app())
    session_file = tmp_path / "sessions.json"
    agent_manager._session_mgr.session_map_file = session_file
    agent_manager._api_auth_manager.shared_key = "issue440"
    headers = {"Authorization": "Bearer shared_issue440"}

    missing = client.post(
        "/api/v1/browser/sessions/not-real/register",
        headers=headers,
        json={"client_id": "mac-a"},
    )
    assert missing.status_code == 404

    created = client.post(
        "/api/v1/sessions/create",
        headers=headers,
        json={"session_id": "issue440-a"},
    )
    assert created.status_code == 200

    registered = client.post(
        "/api/v1/browser/sessions/issue440-a/register",
        headers=headers,
        json={"client_id": "mac-a"},
    )
    assert registered.status_code == 200

    polled = client.get(
        "/api/v1/browser/sessions/issue440-a/commands",
        headers=headers,
        params={"client_id": "mac-a", "timeout": 0},
    )
    assert polled.status_code == 200
    assert polled.json() == {"command": None}


def test_browser_api_rejects_unauthenticated_registration(monkeypatch, tmp_path):
    monkeypatch.setenv("API_SHARED_KEY", "issue440-auth")
    monkeypatch.setenv("APP_ENV", "DEV")

    import agent_manager
    from fastapi.testclient import TestClient

    client = TestClient(agent_manager.create_api_app())
    session_file = tmp_path / "sessions.json"
    agent_manager._session_mgr.session_map_file = session_file
    agent_manager._api_auth_manager.shared_key = "issue440-auth"
    response = client.post(
        "/api/v1/browser/sessions/anything/register",
        json={"client_id": "mac-a"},
    )
    assert response.status_code == 401
