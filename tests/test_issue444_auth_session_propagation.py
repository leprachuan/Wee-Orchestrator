"""Regression coverage for issue #444 internal delegation context."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _response(payload):
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode()
    return response


def _request_body(mock_urlopen):
    return json.loads(mock_urlopen.call_args.kwargs["data"].decode())


def _request_header(mock_urlopen, name):
    request = mock_urlopen.call_args.args[0]
    return request.get_header(name)


def test_wee_runtime_background_dispatch_uses_configured_auth_and_origin(monkeypatch):
    """The native runtime serializes its caller context without a token literal."""
    from wee_runtime import _call_agent_handler

    monkeypatch.delenv("WEE_ORCHESTRATOR_TOKEN", raising=False)
    monkeypatch.setenv("API_SHARED_KEY", "rotated-test-key")
    monkeypatch.setenv("WEE_ORIGIN_SESSION_ID", "origin-session")
    monkeypatch.setenv("WEE_ORCHESTRATOR_USER_IDENTITY", "user-444")
    monkeypatch.setenv("WEE_ORCHESTRATOR_AUTH_CHANNEL", "telegram")

    with patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value = _response({"task_id": "bg_444"})
        result = _call_agent_handler(
            {"agent": "orchestrator", "prompt": "delegate", "mode": "background"}
        )

    assert "bg_444" in result
    assert _request_body(urlopen)["origin_session_id"] == "origin-session"
    assert _request_header(urlopen, "Authorization") == "Bearer shared_rotated-test-key"
    assert _request_header(urlopen, "X-user-identity") == "user-444"
    assert _request_header(urlopen, "X-auth-channel") == "telegram"


def test_agent_manager_background_dispatch_uses_configured_auth_and_origin(monkeypatch):
    """The agent-manager tool path uses the same secure routing context."""
    from agent_manager import SessionManager, _wee_dispatch_context

    monkeypatch.delenv("WEE_ORCHESTRATOR_TOKEN", raising=False)
    monkeypatch.setenv("API_SHARED_KEY", "rotated-test-key")
    monkeypatch.delenv("WEE_SESSION_ID", raising=False)
    monkeypatch.delenv("WEE_ORIGIN_SESSION_ID", raising=False)
    monkeypatch.delenv("WEE_ORCHESTRATOR_USER_IDENTITY", raising=False)
    monkeypatch.delenv("WEE_ORCHESTRATOR_AUTH_CHANNEL", raising=False)
    manager = SimpleNamespace(
        AGENTS={"worker": {"primary_runtime": "wee", "primary_model": "model-444"}}
    )

    context_token = _wee_dispatch_context.set(
        {"origin_session_id": "origin-session", "identity": "user-444", "channel": "webui"}
    )
    try:
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = _response({"task_id": "bg_444"})
            result = SessionManager._wee_call_agent(
                manager, {"agent": "worker", "prompt": "delegate", "mode": "background"}
            )
    finally:
        _wee_dispatch_context.reset(context_token)

    assert "bg_444" in result
    assert _request_body(urlopen)["origin_session_id"] == "origin-session"
    assert _request_header(urlopen, "Authorization") == "Bearer shared_rotated-test-key"
    assert _request_header(urlopen, "X-user-identity") == "user-444"
    assert _request_header(urlopen, "X-auth-channel") == "webui"


def test_dispatch_fails_closed_without_configured_credentials(monkeypatch):
    """No dispatcher issues a request or leaks a credential when auth is absent."""
    from wee_runtime import _call_agent_handler
    from agent_manager import SessionManager

    monkeypatch.delenv("WEE_ORCHESTRATOR_TOKEN", raising=False)
    monkeypatch.delenv("API_SHARED_KEY", raising=False)
    manager = SimpleNamespace(AGENTS={})

    with patch("urllib.request.urlopen") as urlopen:
        runtime_result = _call_agent_handler(
            {"agent": "orchestrator", "prompt": "delegate", "mode": "background"}
        )
        manager_result = SessionManager._wee_call_agent(
            manager, {"agent": "worker", "prompt": "delegate", "mode": "background"}
        )

    assert "authentication is not configured" in runtime_result
    assert "authentication is not configured" in manager_result
    urlopen.assert_not_called()
