"""
Browser control for every runtime, not just `wee`.

The `wee` runtime registers an in-process `browser` Tool and calls
`browser_bridge.execute_browser_command` directly. Subprocess runtimes (codex,
claude, gemini, opencode, cursor) cannot do that -- the broker's client
registrations live in the API process's memory -- so a chat on any of those
runtimes had a connected browser panel it could not reach. Observed as codex
falling back to its own MCP browser integration and reporting "no available
browser" while the Wee panel sat connected alongside it.

Two pieces close that gap and are covered here:

1. `POST /api/v1/browser/sessions/{id}/execute` -- an HTTP seam onto the same
   broker the `wee` tool uses.
2. `wee_browser_mcp.py` -- a stdio MCP server that calls that endpoint, handed
   to each subprocess runtime through its own MCP config mechanism.
"""

import json
import os
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_browser")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9470")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_SERVER = os.path.join(REPO_ROOT, "wee_browser_mcp.py")
AUTH = {"Authorization": f"Bearer shared_{os.environ['API_SHARED_KEY']}"}


# --------------------------------------------------------------------------
# The MCP server: protocol shape, and behaviour when it cannot reach the API.
# --------------------------------------------------------------------------


def _mcp_exchange(messages, env=None):
    """Run the MCP server over stdio and return its JSON responses."""
    payload = "".join(json.dumps(m) + "\n" for m in messages)
    process = subprocess.run(
        [sys.executable, MCP_SERVER],
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, **(env or {})},
    )
    return [json.loads(line) for line in process.stdout.splitlines() if line.strip()]


def test_mcp_server_completes_the_initialize_handshake():
    responses = _mcp_exchange(
        [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}]
    )
    assert len(responses) == 1
    result = responses[0]["result"]
    assert result["serverInfo"]["name"] == "wee-browser"
    assert "tools" in result["capabilities"]


def test_mcp_server_advertises_the_browser_tools():
    responses = _mcp_exchange([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
    names = {tool["name"] for tool in responses[0]["result"]["tools"]}
    assert {"browser_navigate", "browser_snapshot", "browser_click", "browser_type"} <= names
    for tool in responses[0]["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object", tool["name"]


def test_mcp_server_does_not_answer_notifications():
    """A notification has no id; replying to one corrupts the stream."""
    responses = _mcp_exchange([{"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert responses == []


def test_mcp_server_reports_missing_configuration_instead_of_crashing():
    responses = _mcp_exchange(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "browser_snapshot", "arguments": {}},
            }
        ],
        env={"WEE_BROWSER_SESSION_ID": "", "WEE_API_BASE": "", "WEE_API_TOKEN": ""},
    )
    text = responses[0]["result"]["content"][0]["text"]
    assert "not configured" in text
    assert "WEE_BROWSER_SESSION_ID" in text, "must name what is missing"


def test_mcp_server_rejects_an_unknown_tool():
    responses = _mcp_exchange(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "browser_launch_missiles", "arguments": {}},
            }
        ]
    )
    assert responses[0]["error"]["code"] == -32602


# --------------------------------------------------------------------------
# The HTTP seam, exercised against the real broker.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    try:
        from starlette.testclient import TestClient
    except ImportError:  # pragma: no cover - depends on installed extras
        from fastapi.testclient import TestClient

    from agent_manager import create_api_app

    return TestClient(create_api_app(), raise_server_exceptions=False)


@pytest.fixture
def session_id(client):
    response = client.post(
        "/api/v1/sessions/create", headers=AUTH, json={"agent": "orchestrator"}
    )
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def test_execute_falls_back_to_playwright_without_a_native_panel(client, session_id):
    """With no panel attached, browser_bridge routes to Playwright by design.

    So this endpoint must not claim "no browser" -- it must report whatever
    that path did. Either it worked (200) or it failed for a stated reason;
    what it must never do is surface as an unhandled 500.
    """
    response = client.post(
        f"/api/v1/browser/sessions/{session_id}/execute",
        headers=AUTH,
        json={"action": "snapshot"},
    )
    assert response.status_code in (200, 502, 504), response.text
    if response.status_code != 200:
        assert response.json()["detail"], "a failure must say why"


def test_execute_maps_a_disconnected_browser_to_409(client, session_id, monkeypatch):
    """When the bridge itself reports no browser, say so actionably.

    Exercised directly because reaching it through the bridge requires
    Playwright to also be unavailable.
    """
    import browser_bridge

    def _no_browser(*args, **kwargs):
        raise RuntimeError("No native browser is connected to this session")

    monkeypatch.setattr(browser_bridge, "execute_browser_command", _no_browser)

    response = client.post(
        f"/api/v1/browser/sessions/{session_id}/execute",
        headers=AUTH,
        json={"action": "snapshot"},
    )
    assert response.status_code == 409, response.text
    assert "no native browser" in response.json()["detail"].lower()


def test_execute_requires_an_action(client, session_id):
    response = client.post(
        f"/api/v1/browser/sessions/{session_id}/execute", headers=AUTH, json={}
    )
    assert response.status_code == 400


def test_execute_requires_authentication(client, session_id):
    response = client.post(
        f"/api/v1/browser/sessions/{session_id}/execute", json={"action": "snapshot"}
    )
    assert response.status_code == 401


def test_execute_drives_an_attached_browser_client(client, session_id):
    """Full round trip: register a browser client, then drive it over HTTP.

    Stands in for the macOS WKWebView panel, which does exactly this -- register,
    long-poll for commands, post results.
    """
    client_id = "test-browser-client"
    registered = client.post(
        f"/api/v1/browser/sessions/{session_id}/register",
        headers=AUTH,
        json={"client_id": client_id},
    )
    assert registered.status_code == 200, registered.text

    delivered = {}

    def fake_browser():
        # Poll for the command the way the real panel does, then answer it.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            polled = client.get(
                f"/api/v1/browser/sessions/{session_id}/commands",
                headers=AUTH,
                params={"client_id": client_id, "timeout": 5},
            )
            command = (polled.json() or {}).get("command")
            if not command:
                continue
            delivered["command"] = command
            client.post(
                f"/api/v1/browser/sessions/{session_id}/results",
                headers=AUTH,
                json={
                    "client_id": client_id,
                    "command_id": command["id"],
                    "result": "PAGE TEXT FROM PANEL",
                    "url": "https://example.invalid/page",
                    "title": "Example",
                },
            )
            return

    worker = threading.Thread(target=fake_browser, daemon=True)
    worker.start()

    response = client.post(
        f"/api/v1/browser/sessions/{session_id}/execute",
        headers=AUTH,
        json={"action": "navigate", "url": "https://example.invalid/page", "timeout": 30},
    )
    worker.join(timeout=35)

    assert response.status_code == 200, response.text
    assert delivered.get("command", {}).get("action") == "navigate"
    assert "PAGE TEXT FROM PANEL" in response.json()["result"]


# --------------------------------------------------------------------------
# Per-runtime wiring: each CLI gets the server through its own mechanism.
# --------------------------------------------------------------------------


def _manager():
    import agent_manager

    for name in dir(agent_manager):
        obj = getattr(agent_manager, name)
        if isinstance(obj, type) and hasattr(obj, "_wee_browser_mcp_server_spec"):
            return obj
    raise AssertionError("No class exposing _wee_browser_mcp_server_spec")


def test_codex_receives_the_browser_server_as_config_overrides(monkeypatch):
    manager = _manager()
    instance = manager.__new__(manager)
    monkeypatch.setattr(
        manager, "get_or_create_session_data", lambda self, sid: {"identity": "u", "channel": "webui"}
    )

    args = manager._wee_browser_codex_config_args(instance, "sess-codex")
    joined = " ".join(args)
    assert "mcp_servers.wee-browser.command=" in joined
    assert "wee_browser_mcp.py" in joined
    assert "WEE_BROWSER_SESSION_ID" in joined and "sess-codex" in joined
    # Codex parses each value as TOML; every value must be quoted so a token
    # containing '=' or '#' cannot break the parse.
    for index, token in enumerate(args):
        if token == "-c":
            assert '="' in args[index + 1] or "=[" in args[index + 1], args[index + 1]


def test_claude_receives_the_browser_server_as_an_mcp_config_file(monkeypatch, tmp_path):
    manager = _manager()
    instance = manager.__new__(manager)
    monkeypatch.setattr(
        manager, "get_or_create_session_data", lambda self, sid: {"identity": "u", "channel": "webui"}
    )

    path = manager._wee_browser_mcp_config_file(instance, "sess-claude")
    assert path and os.path.exists(path)
    with open(path) as handle:
        config = json.load(handle)
    server = config["mcpServers"]["wee-browser"]
    assert server["args"][0].endswith("wee_browser_mcp.py")
    assert server["env"]["WEE_BROWSER_SESSION_ID"] == "sess-claude"


def test_each_session_gets_its_own_config_file(monkeypatch):
    """Two chats must never be handed the same file and drive each other's browser."""
    manager = _manager()
    instance = manager.__new__(manager)
    monkeypatch.setattr(
        manager, "get_or_create_session_data", lambda self, sid: {"identity": "u", "channel": "webui"}
    )

    first = manager._wee_browser_mcp_config_file(instance, "sess-a")
    second = manager._wee_browser_mcp_config_file(instance, "sess-b")
    assert first != second


def test_no_browser_wiring_without_a_shared_key(monkeypatch):
    """Better to offer no browser tools than tools that always fail."""
    manager = _manager()
    instance = manager.__new__(manager)
    monkeypatch.setattr(
        manager, "get_or_create_session_data", lambda self, sid: {"identity": "u", "channel": "webui"}
    )
    monkeypatch.delenv("API_SHARED_KEY", raising=False)

    assert manager._wee_browser_mcp_server_spec(instance, "sess") is None
    assert manager._wee_browser_codex_config_args(instance, "sess") == []
    assert manager._wee_browser_mcp_config_file(instance, "sess") is None
