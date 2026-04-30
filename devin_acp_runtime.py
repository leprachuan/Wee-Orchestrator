"""Devin ACP runtime adapter for Wee-Orchestrator.

This module talks to ``devin acp`` over JSON-RPC stdio and translates ACP
``session/update`` notifications into Wee's existing stream-buffer events.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


StreamPush = Callable[[str, Any], None]
PidCallback = Callable[[int], None]


@dataclass
class DevinACPAdapter:
    """Synchronous wrapper around the async Devin ACP JSON-RPC protocol."""

    devin_bin: str = "devin"
    cwd: str = "."
    model: Optional[str] = None
    mode: str = "restricted"
    timeout: int = 900
    stream_push: Optional[StreamPush] = None
    on_pid: Optional[PidCallback] = None
    client_name: str = "wee-orchestrator-devin-acp"

    proc: Optional[asyncio.subprocess.Process] = None
    next_id: int = 1
    pending: dict[int, asyncio.Future] = field(default_factory=dict)
    session_id: Optional[str] = None
    collected_text: list[str] = field(default_factory=list)
    stderr_lines: list[str] = field(default_factory=list)
    _turn_done: Optional[asyncio.Event] = None
    _prompt_result: Any = None
    _prompt_error: Optional[BaseException] = None

    def run(self, prompt: str) -> str:
        """Run one ACP turn and return the final assistant text."""
        return asyncio.run(self._run(prompt))

    async def _run(self, prompt: str) -> str:
        await self._start()
        assert self.proc is not None
        self._turn_done = asyncio.Event()
        try:
            init_result = await self._rpc(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientInfo": {"name": self.client_name, "version": "0.1"},
                    "clientCapabilities": {
                        "terminal": False,
                        "fs": {"readTextFile": False, "writeTextFile": False},
                    },
                },
            )
            self._push("tool_call", {
                "event": "status",
                "id": "devin-acp-initialize",
                "name": "devin-acp",
                "output": f"Initialized Devin ACP: {self._agent_name(init_result)}",
                "runtime": "devin-acp",
                "timestamp": self._ts(),
            })

            new_params: dict[str, Any] = {"cwd": self.cwd, "mcpServers": []}
            if self.model:
                # Some ACP servers ignore model here and require a mode/config API;
                # passing it is harmless when unsupported by Devin's current schema.
                new_params["model"] = self.model
            session = await self._rpc("session/new", new_params)
            self.session_id = session.get("sessionId") or session.get("id")
            if not self.session_id:
                raise RuntimeError(f"Devin ACP session/new returned no sessionId: {session!r}")

            mode_id = self._mode_to_acp(self.mode)
            try:
                await self._rpc(
                    "session/set_mode",
                    {"sessionId": self.session_id, "modeId": mode_id},
                )
            except Exception as exc:  # non-fatal; older builds may not support this
                self._push("tool_call", {
                    "event": "status",
                    "id": "devin-acp-mode",
                    "name": "session/set_mode",
                    "output": f"Could not set ACP mode {mode_id}: {exc}",
                    "runtime": "devin-acp",
                    "timestamp": self._ts(),
                })

            prompt_task = asyncio.create_task(
                self._rpc(
                    "session/prompt",
                    {
                        "sessionId": self.session_id,
                        "prompt": [{"type": "text", "text": prompt}],
                    },
                )
            )

            done_task = asyncio.create_task(self._turn_done.wait())
            wait_timeout = max(1, int(self.timeout))
            done, pending = await asyncio.wait(
                {prompt_task, done_task},
                timeout=wait_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError(f"Devin ACP timed out after {wait_timeout}s")

            if prompt_task in done:
                self._prompt_result = prompt_task.result()
                if not done_task.done():
                    # Give final session/update notifications a brief chance to arrive.
                    try:
                        await asyncio.wait_for(done_task, timeout=2.0)
                    except asyncio.TimeoutError:
                        pass
            elif done_task in done:
                if not prompt_task.done():
                    # The stopped notification is authoritative; do not hang on RPC.
                    prompt_task.cancel()
                else:
                    self._prompt_result = prompt_task.result()

            result = "".join(self.collected_text).strip()
            if not result and self._prompt_result is not None:
                result = self._extract_text(self._prompt_result).strip()
            if not result and self.stderr_lines:
                result = "\n".join(self.stderr_lines[-20:]).strip()
            return result or ""
        finally:
            await self._stop()

    async def _start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            self.devin_bin,
            "acp",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
        )
        if self.on_pid and self.proc.pid:
            self.on_pid(self.proc.pid)
        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())

    async def _stop(self) -> None:
        if not self.proc:
            return
        if self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()

    async def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        assert self.proc and self.proc.stdin
        rid = self.next_id
        self.next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        body = json.dumps(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params},
            separators=(",", ":"),
        ) + "\n"
        self.proc.stdin.write(body.encode())
        await self.proc.stdin.drain()
        return await fut

    async def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        async for raw in self.proc.stdout:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                self._push("tool_call", {
                    "event": "status",
                    "id": f"devin-acp-stdout-{time.time_ns()}",
                    "name": "stdout",
                    "output": line[:2000],
                    "runtime": "devin-acp",
                    "timestamp": self._ts(),
                })
                continue
            if "id" in msg and msg["id"] in self.pending:
                fut = self.pending.pop(msg["id"])
                if "error" in msg:
                    fut.set_exception(RuntimeError(json.dumps(msg["error"])))
                else:
                    fut.set_result(msg.get("result"))
                continue
            if msg.get("method") == "session/update":
                self._handle_session_update(msg.get("params") or {})

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        async for raw in self.proc.stderr:
            line = raw.decode(errors="replace").rstrip()
            if not line:
                continue
            self.stderr_lines.append(line)
            # Surface auth/MCP warnings without spamming every debug line.
            lower = line.lower()
            if any(k in lower for k in ("mcp", "auth", "error", "warn", "failed")):
                self._push("tool_call", {
                    "event": "status",
                    "id": f"devin-acp-stderr-{time.time_ns()}",
                    "name": "devin-stderr",
                    "output": line[:2000],
                    "runtime": "devin-acp",
                    "timestamp": self._ts(),
                })

    def _handle_session_update(self, params: dict[str, Any]) -> None:
        update = params.get("update") or params
        kind = update.get("sessionUpdate") or update.get("type")
        if kind == "agent_message_chunk":
            text = self._extract_text(update.get("content"))
            if text:
                self.collected_text.append(text)
                self._push("chunk", {"text": text})
        elif kind == "agent_thought_chunk":
            text = self._extract_text(update.get("content"))
            if text:
                self._push("tool_call", {
                    "event": "status",
                    "id": f"devin-thought-{time.time_ns()}",
                    "name": "thought",
                    "output": text[:2000],
                    "runtime": "devin-acp",
                    "timestamp": self._ts(),
                })
        elif kind == "tool_call":
            tool_id = update.get("toolCallId") or update.get("id") or f"tc_devin_acp_{time.time_ns()}"
            self._push("tool_call", {
                "event": "start",
                "id": tool_id,
                "name": update.get("title") or update.get("name") or "tool",
                "input": update.get("content") or update,
                "runtime": "devin-acp",
                "timestamp": self._ts(),
            })
        elif kind == "tool_call_update":
            tool_id = update.get("toolCallId") or update.get("id") or f"tc_devin_acp_{time.time_ns()}"
            status = update.get("status") or update.get("state") or "completed"
            self._push("tool_call", {
                "event": "completed" if status in ("completed", "complete", "done") else "result",
                "id": tool_id,
                "name": update.get("title") or update.get("name") or "tool",
                "output": json.dumps(update, default=str)[:2000],
                "status": status,
                "is_error": status in ("error", "failed"),
                "runtime": "devin-acp",
                "timestamp": self._ts(),
            })
        elif kind == "usage_update":
            # The SessionManager done event handles token metadata for Wee-native.
            # For now, expose usage as a status event so it is visible/debuggable.
            self._push("tool_call", {
                "event": "status",
                "id": f"devin-usage-{time.time_ns()}",
                "name": "usage_update",
                "output": json.dumps(update, default=str)[:1000],
                "runtime": "devin-acp",
                "timestamp": self._ts(),
            })
        elif kind in ("_cognition.ai/agent_stopped", "agent_stopped"):
            if self._turn_done:
                self._turn_done.set()

    def _push(self, kind: str, data: Any) -> None:
        if self.stream_push:
            self.stream_push(kind, data)

    @staticmethod
    def _mode_to_acp(mode: str) -> str:
        if mode == "elevated":
            return "bypass"
        if mode == "sandboxed":
            return "plan"
        return "ask"

    @staticmethod
    def _extract_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            if isinstance(value.get("text"), str):
                return value["text"]
            if isinstance(value.get("content"), str):
                return value["content"]
        if isinstance(value, list):
            return "".join(DevinACPAdapter._extract_text(v) for v in value)
        return ""

    @staticmethod
    def _agent_name(init_result: Any) -> str:
        if isinstance(init_result, dict):
            agent = init_result.get("agent") or init_result.get("agentInfo") or {}
            if isinstance(agent, dict):
                return str(agent.get("name") or init_result.get("name") or "unknown")
            return str(agent)
        return "unknown"

    @staticmethod
    def _ts() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
