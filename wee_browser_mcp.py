#!/usr/bin/env python3
"""stdio MCP server exposing one Wee chat session's browser to any runtime.

The `wee` runtime drives the browser by calling `browser_bridge` in-process.
The subprocess runtimes (codex, claude, gemini, opencode, cursor) cannot: the
broker's client registrations live in the API process's memory. This server is
the bridge -- it speaks MCP over stdio to whichever CLI launched it, and calls
`POST /api/v1/browser/sessions/{id}/execute` on the API to do the work.

Configuration is entirely by environment, because that is what every CLI's MCP
config can set:

    WEE_BROWSER_SESSION_ID  the chat session whose browser to drive (required)
    WEE_API_BASE            e.g. http://127.0.0.1:8001 (required)
    WEE_API_TOKEN           bearer token for that API (required)
    WEE_API_IDENTITY        X-User-Identity, when the API expects one
    WEE_API_CHANNEL         X-Auth-Channel, when the API expects one
    WEE_API_INSECURE_TLS    "1" to skip TLS verification (self-signed prod cert)

The JSON-RPC framing is hand-rolled rather than taken from the `mcp` package:
that package is not in requirements.txt, and this server has to start reliably
inside whatever interpreter a given CLI happens to spawn.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "wee-browser"
SERVER_VERSION = "1.0.0"

# Matches the API's own ceiling for a browser command so this layer never times
# out first and reports a confusing error for a command still in flight.
REQUEST_TIMEOUT = 95.0

TOOLS = [
    {
        "name": "browser_navigate",
        "description": (
            "Navigate this chat session's browser to a URL and return the "
            "resulting page text. Use this before reading or clicking."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Absolute URL to open"}},
            "required": ["url"],
        },
    },
    {
        "name": "browser_snapshot",
        "description": (
            "Read the current page in this chat session's browser: its URL, "
            "title, and visible text. Use this to see what is on screen."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_click",
        "description": "Click an element, found by CSS selector or by its visible text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector"},
                "text": {"type": "string", "description": "Visible text to match instead"},
            },
        },
    },
    {
        "name": "browser_type",
        "description": "Type text into the element matching a CSS selector.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "submit": {"type": "boolean", "description": "Press Enter afterwards"},
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "browser_evaluate",
        "description": "Evaluate JavaScript in the current page and return its result.",
        "inputSchema": {
            "type": "object",
            "properties": {"script": {"type": "string"}},
            "required": ["script"],
        },
    },
    {
        "name": "browser_back",
        "description": "Go back one entry in this session's browser history.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_forward",
        "description": "Go forward one entry in this session's browser history.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_reload",
        "description": "Reload the current page.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

# MCP tool name -> the action understood by browser_bridge.
_ACTIONS = {
    "browser_navigate": "navigate",
    "browser_snapshot": "snapshot",
    "browser_click": "click",
    "browser_type": "type",
    "browser_evaluate": "evaluate",
    "browser_back": "back",
    "browser_forward": "forward",
    "browser_reload": "reload",
}


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _ssl_context():
    if _env("WEE_API_INSECURE_TLS") in {"1", "true", "yes"}:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    return None


def _execute(action: str, arguments: dict) -> str:
    """Send one browser command to the API and return a human-readable result."""
    session_id = _env("WEE_BROWSER_SESSION_ID")
    api_base = _env("WEE_API_BASE").rstrip("/")
    token = _env("WEE_API_TOKEN")

    missing = [
        name
        for name, value in (
            ("WEE_BROWSER_SESSION_ID", session_id),
            ("WEE_API_BASE", api_base),
            ("WEE_API_TOKEN", token),
        )
        if not value
    ]
    if missing:
        return f"Browser control is not configured for this session (missing {', '.join(missing)})."

    payload = {"action": action, **{k: v for k, v in arguments.items() if v is not None}}
    request = urllib.request.Request(
        f"{api_base}/api/v1/browser/sessions/{session_id}/execute",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"Bearer {token}")
    if identity := _env("WEE_API_IDENTITY"):
        request.add_header("X-User-Identity", identity)
    if channel := _env("WEE_API_CHANNEL"):
        request.add_header("X-Auth-Channel", channel)

    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT, context=_ssl_context()
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
        return str(body.get("result", ""))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        if exc.code == 409:
            # The one failure worth explaining: the panel is not attached, and
            # the user is the only one who can fix that.
            return (
                "No browser is attached to this chat session. Open the browser "
                "panel for this chat in the Wee app, then try again. "
                f"({detail})"
            )
        return f"Browser command failed (HTTP {exc.code}): {detail}"
    except Exception as exc:
        return f"Browser command failed: {exc}"


def _respond(message_id, result=None, error=None) -> None:
    payload = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _handle(message: dict) -> None:
    method = message.get("method")
    message_id = message.get("id")

    # Notifications carry no id and must not be answered.
    if message_id is None:
        return

    if method == "initialize":
        _respond(
            message_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    elif method == "tools/list":
        _respond(message_id, {"tools": TOOLS})
    elif method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name") or ""
        action = _ACTIONS.get(name)
        if action is None:
            _respond(message_id, error={"code": -32602, "message": f"Unknown tool: {name}"})
            return
        output = _execute(action, params.get("arguments") or {})
        _respond(message_id, {"content": [{"type": "text", "text": output}]})
    elif method in {"ping", "shutdown"}:
        _respond(message_id, {})
    else:
        _respond(message_id, error={"code": -32601, "message": f"Unknown method: {method}"})


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            _handle(message)
        except Exception as exc:  # pragma: no cover - defensive
            # A crash here would take browser control down for the whole run,
            # so report and keep serving.
            _respond(message.get("id"), error={"code": -32603, "message": str(exc)})


if __name__ == "__main__":
    main()
