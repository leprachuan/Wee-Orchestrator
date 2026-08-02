#!/usr/bin/env python3
"""stdio MCP server exposing one Wee chat session's shell to any runtime.

The `wee` runtime drives the shell by calling `shell_bridge` in-process. The
subprocess runtimes (codex, claude, gemini, opencode, cursor) cannot: the
broker's client registrations live in the API process's memory. This server is
the bridge -- it speaks MCP over stdio to whichever CLI launched it, and calls
`POST /api/v1/shell/sessions/{id}/execute` on the API to do the work.

There is no headless fallback here (unlike the browser). If no native shell is
attached, the right answer is to say so, not to spin up something the user
can't see or type into -- that would defeat the point of a shell the user is
meant to share with the agent.

Configuration is entirely by environment, because that is what every CLI's MCP
config can set:

    WEE_SHELL_SESSION_ID    the chat session whose shell to drive (required)
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
SERVER_NAME = "wee-shell"
SERVER_VERSION = "1.0.0"

# Matches the API's own ceiling for a shell command so this layer never times
# out first and reports a confusing error for a command still in flight.
REQUEST_TIMEOUT = 95.0

TOOLS = [
    {
        "name": "shell_run",
        "description": (
            "Run a command line in this chat session's shell (as if the user "
            "typed it and pressed Enter) and return the terminal output it "
            "produced. The user can see this shell and everything run in it, "
            "and may have typed commands of their own -- read the output "
            "carefully, it is a shared terminal, not a private scratch shell."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "Command line to run"}},
            "required": ["command"],
        },
    },
    {
        "name": "shell_write",
        "description": (
            "Type raw text into this session's shell without pressing Enter. "
            "Use this to answer an interactive prompt (e.g. a password or "
            "y/n confirmation) that a running command is waiting on."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "shell_key",
        "description": (
            "Send a control key to this session's shell: ctrl-c, ctrl-d, "
            "tab, up, down, left, right, enter, escape, or backspace."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "enum": [
                        "ctrl-c", "ctrl-d", "tab", "up", "down", "left",
                        "right", "enter", "escape", "backspace",
                    ],
                }
            },
            "required": ["key"],
        },
    },
    {
        "name": "shell_read",
        "description": (
            "Read this session's shell without sending any input -- use this "
            "to see what the user just typed, or the output of a long-running "
            "command since you last looked."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

# MCP tool name -> the action understood by shell_bridge.
_ACTIONS = {
    "shell_run": "run",
    "shell_write": "write",
    "shell_key": "key",
    "shell_read": "read",
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
    """Send one shell command to the API and return the resulting output."""
    session_id = _env("WEE_SHELL_SESSION_ID")
    api_base = _env("WEE_API_BASE").rstrip("/")
    token = _env("WEE_API_TOKEN")

    missing = [
        name
        for name, value in (
            ("WEE_SHELL_SESSION_ID", session_id),
            ("WEE_API_BASE", api_base),
            ("WEE_API_TOKEN", token),
        )
        if not value
    ]
    if missing:
        return f"Shell control is not configured for this session (missing {', '.join(missing)})."

    payload = {"action": action, **{k: v for k, v in arguments.items() if v is not None}}
    request = urllib.request.Request(
        f"{api_base}/api/v1/shell/sessions/{session_id}/execute",
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
                "No shell is attached to this chat session. Open the shell "
                "panel for this chat in the Wee app, then try again. "
                f"({detail})"
            )
        return f"Shell command failed (HTTP {exc.code}): {detail}"
    except Exception as exc:
        return f"Shell command failed: {exc}"


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
            # A crash here would take shell control down for the whole run,
            # so report and keep serving.
            _respond(message.get("id"), error={"code": -32603, "message": str(exc)})


if __name__ == "__main__":
    main()
