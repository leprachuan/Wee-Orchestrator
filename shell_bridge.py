"""Session-scoped shell control for Wee native clients.

Mirrors browser_bridge.py's native broker: a native client (the macOS app)
registers a PTY-backed shell against a chat session and long-polls a small
authenticated command queue. The LLM (in-process for wee/copilot-sdk/claude-sdk,
or via the wee-shell MCP server for codex/claude/copilot) sends run/write/key/read
commands and gets back the terminal output those commands produced.

Unlike the browser, there is no headless fallback. A shell nobody can see
defeats the point of "the user should also be able to run commands in it" --
if no native shell is attached, the tool should say so, not silently spin up
something the user can't watch or type into.
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4


@dataclass
class ShellRegistration:
    identity: str
    channel: str
    client_id: str
    last_seen: float = field(default_factory=time.monotonic)


class NativeShellBroker:
    def __init__(self, active_ttl: float = 45.0):
        self.active_ttl = active_ttl
        self._condition = threading.Condition()
        self._registrations: dict[str, ShellRegistration] = {}
        self._commands: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        self._results: dict[str, dict[str, Any]] = {}

    def register(
        self, session_id: str, identity: str, channel: str, client_id: str
    ) -> None:
        with self._condition:
            current = self._registrations.get(session_id)
            if current and (current.identity, current.channel) != (identity, channel):
                raise PermissionError("Shell session belongs to another user")
            self._registrations[session_id] = ShellRegistration(
                identity=identity, channel=channel, client_id=client_id
            )
            self._condition.notify_all()

    def _registration(
        self, session_id: str, identity: str, channel: str, client_id: str
    ) -> ShellRegistration:
        registration = self._registrations.get(session_id)
        if not registration or (
            registration.identity,
            registration.channel,
            registration.client_id,
        ) != (identity, channel, client_id):
            raise PermissionError("Native shell is not registered for this session")
        return registration

    def poll(
        self,
        session_id: str,
        identity: str,
        channel: str,
        client_id: str,
        timeout: float = 25.0,
    ) -> Optional[dict[str, Any]]:
        deadline = time.monotonic() + max(0.0, min(timeout, 30.0))
        with self._condition:
            registration = self._registration(
                session_id, identity, channel, client_id
            )
            registration.last_seen = time.monotonic()
            while not self._commands[session_id]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    registration.last_seen = time.monotonic()
                    return None
                self._condition.wait(remaining)
                registration = self._registration(
                    session_id, identity, channel, client_id
                )
                registration.last_seen = time.monotonic()
            return self._commands[session_id].popleft()

    def submit_result(
        self,
        session_id: str,
        identity: str,
        channel: str,
        client_id: str,
        command_id: str,
        output: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._condition:
            registration = self._registration(
                session_id, identity, channel, client_id
            )
            registration.last_seen = time.monotonic()
            self._results[command_id] = {"output": output, "error": error}
            self._condition.notify_all()

    def is_active(self, session_id: str) -> bool:
        with self._condition:
            registration = self._registrations.get(session_id)
            return bool(
                registration
                and time.monotonic() - registration.last_seen <= self.active_ttl
            )

    def execute(
        self, session_id: str, command: dict[str, Any], timeout: float = 45.0
    ) -> dict[str, Any]:
        command_id = str(uuid4())
        payload = {"id": command_id, **command}
        deadline = time.monotonic() + max(1.0, min(timeout, 90.0))
        with self._condition:
            if not self.is_active(session_id):
                raise RuntimeError("No native shell is connected to this session")
            self._commands[session_id].append(payload)
            self._condition.notify_all()
            while command_id not in self._results:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Native shell command timed out")
                self._condition.wait(remaining)
            result = self._results.pop(command_id)
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        return result


native_shell_broker = NativeShellBroker()


def execute_shell_command(
    session_id: str, command: dict[str, Any], timeout: float = 45.0
) -> str:
    response = native_shell_broker.execute(session_id, command, timeout=timeout)
    output = response.get("output") or ""
    return output[:16_000]
