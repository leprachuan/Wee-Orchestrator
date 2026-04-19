"""Collaborators extracted from SessionManager.

These helpers keep SessionManager focused on session state while isolating
command parsing/registration, runtime dispatch, and streaming subprocess
plumbing behind explicit interfaces.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


class CliCommandHandler:
    """Own slash-command parsing and registry management."""

    def __init__(self, manager: Any):
        self.manager = manager
        self.registry: Dict[str, dict] = {}

    def register(self, command: str, handler, description: str) -> None:
        self.registry[command] = {
            "handler": handler,
            "description": description,
        }

    def initialize_default_commands(self) -> None:
        self.register("/help", self.manager._slash_help, "Show available commands")
        self.register("/status", self.manager._slash_status, "Check status of running query")
        self.register("/cancel", self.manager._slash_cancel, "Cancel running query")
        self.register(
            "/capabilities",
            self.manager._slash_capabilities,
            "Show agent capabilities",
        )
        self.register("/runtime", self.manager._slash_runtime, "Manage runtime (list/set/current)")
        self.register("/agent", self.manager._slash_agent, "Manage agent (list/set/current/invoke)")
        self.register("/model", self.manager._slash_model, "Manage model (list/set/current)")
        self.register("/session", self.manager._slash_session, "Manage session (list/reset/info)")
        self.register("/timeout", self.manager._slash_timeout, "Get/set execution timeout")
        self.register("/render", self.manager._slash_render, "Get/set output render format")
        self.register(
            "/notifications",
            self.manager._slash_notifications,
            "Toggle background notifications",
        )
        self.register("/silent", self.manager._slash_silent, "Toggle silent mode (hide tool calls)")
        self.register("/verbose", self.manager._slash_verbose, "Toggle verbose mode")
        self.register("/mode", self.manager._slash_mode, "Set permission mode")
        self.register("/schedule", self.manager._slash_schedule, "Manage scheduled jobs")
        self.register("/background", self.manager._slash_background, "Manage background tasks")
        self.register("/update", self.manager._slash_update, "Pull latest and restart")
        self.register("/upgrade", self.manager._slash_update, "Pull latest and restart")
        self.register("/pull", self.manager._slash_update, "Pull latest and restart")
        self.register("/secret", self.manager._slash_secret, "Manage secrets (set/delete/list)")

    def parse_slash_command(self, prompt: str):
        if not prompt.startswith("/"):
            return None, None

        parts = prompt.split(None, 1)
        command = parts[0].lower()
        argument = parts[1] if len(parts) > 1 else None
        return command, argument

    def get_commands(self) -> Dict[str, str]:
        return {
            cmd: entry["description"]
            for cmd, entry in self.registry.items()
        }


class StreamBuffer:
    """Replayable buffer for multi-consumer SSE streaming."""

    def __init__(self):
        self.chunks = []
        self.finished = False
        self.done_result = None
        self.created_at = time.time()
        self._consumers = []
        self._lock = threading.Lock()

    def push(self, kind, data):
        with self._lock:
            idx = len(self.chunks)
            self.chunks.append((kind, data))
            if kind == "done":
                self.finished = True
                self.done_result = data
            for q, lp, start_idx in self._consumers:
                if idx >= start_idx:
                    try:
                        lp.call_soon_threadsafe(q.put_nowait, (kind, data))
                    except Exception:
                        pass

    def add_consumer(self, queue, loop):
        with self._lock:
            replay_index = len(self.chunks)
            self._consumers.append((queue, loop, replay_index))
            return replay_index

    def remove_consumer(self, queue):
        with self._lock:
            self._consumers = [
                (q, lp, si) for q, lp, si in self._consumers if q is not queue
            ]

    def get_replay_chunks(self, up_to: int):
        with self._lock:
            return list(self.chunks[:up_to])

    def has_consumers(self) -> bool:
        with self._lock:
            return len(self._consumers) > 0


class StreamingManager:
    """Own streaming state and subprocess execution plumbing."""

    def __init__(self, manager: Any):
        self.manager = manager
        self.stream_queues: Dict[str, tuple] = {}
        self.stream_buffers: Dict[str, StreamBuffer] = {}

    def get_or_create_stream_buffer(self, session_id: str) -> StreamBuffer:
        buf = self.stream_buffers.get(session_id)
        if buf is None:
            buf = StreamBuffer()
            self.stream_buffers[session_id] = buf
        return buf

    def register_stream(self, session_id: str, queue, loop) -> None:
        self.stream_queues[session_id] = (queue, loop)
        buf = self.get_or_create_stream_buffer(session_id)
        buf.add_consumer(queue, loop)

    def unregister_stream(self, session_id: str, queue=None) -> None:
        self.stream_queues.pop(session_id, None)
        buf = self.stream_buffers.get(session_id)
        if buf and queue is not None:
            buf.remove_consumer(queue)

    def cleanup_stream_buffer(self, session_id: str) -> None:
        self.stream_buffers.pop(session_id, None)

    def cleanup_stale_stream_buffers(self, max_age: float = 600.0) -> None:
        now = time.time()
        stale = [
            sid
            for sid, buf in self.stream_buffers.items()
            if buf.finished and (now - buf.created_at) > max_age
        ]
        for sid in stale:
            self.stream_buffers.pop(sid, None)

    def execute_subprocess_with_tracking(
        self,
        cmd: list,
        cwd: str,
        timeout: int,
        runtime: str,
        agent: str,
        prompt: str,
        n8n_session_id: str,
        use_pty: bool = False,
    ) -> str:
        import threading as _threading

        stream_info = self.stream_queues.get(n8n_session_id)
        stream_buffer = self.stream_buffers.get(n8n_session_id)

        _pty_master = None
        if use_pty and stream_info:
            import pty as _pty_mod

            _pty_master, _pty_slave = _pty_mod.openpty()
            try:
                import fcntl as _fcntl_mod
                import struct as _struct_mod
                import termios as _termios_mod

                _ws = _struct_mod.pack("HHHH", 40, 120, 0, 0)
                _fcntl_mod.ioctl(_pty_master, _termios_mod.TIOCSWINSZ, _ws)
                _attrs = _termios_mod.tcgetattr(_pty_slave)
                _attrs[1] &= ~_termios_mod.OPOST
                _attrs[3] &= ~_termios_mod.ECHO
                _termios_mod.tcsetattr(_pty_slave, _termios_mod.TCSANOW, _attrs)
            except Exception:
                pass

        try:
            _sub_env = {**os.environ, "WEE_SESSION_ID": n8n_session_id}
            if _pty_master is not None:
                process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=_pty_slave,
                    stderr=subprocess.PIPE,
                    cwd=cwd,
                    env=_sub_env,
                )
                os.close(_pty_slave)
            else:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=cwd,
                    bufsize=1,
                    env=_sub_env,
                )

            self.manager.track_running_query(
                n8n_session_id, process.pid, runtime, agent, prompt
            )

            if stream_info:
                queue, loop = stream_info
                stderr_buf: list = []

                def _drain_stderr() -> None:
                    try:
                        raw = process.stderr.read()
                        stderr_buf.append(
                            raw.decode("utf-8", errors="replace")
                            if isinstance(raw, bytes)
                            else raw
                        )
                    except Exception:
                        pass

                stderr_thread = _threading.Thread(target=_drain_stderr, daemon=True)
                stderr_thread.start()

                stdout_chunks: list = []

                if _pty_master is not None:
                    import codecs as _codecs
                    import re as _re

                    _ansi_escape = _re.compile(
                        r"\x1b\[[0-9;]*[a-zA-Z]"
                        r"|\x1b\][^\x07]*\x07"
                        r"|\x1b\([A-Z0-9]"
                    )
                    _pty_tool_counter = [0]
                    _pty_tool_pattern = _re.compile(
                        r"(?:\[TOOL_CALL\]|\bCalling\s+tool|\bUsing\s+tool(?:\:|_)|Tool|Running|Executing|USING_TOOL)[\s:_]*(\w[\w\.]*)\s*(.*)",
                        _re.IGNORECASE,
                    )
                    _utf8_decoder = _codecs.getincrementaldecoder("utf-8")("replace")
                    try:
                        while True:
                            try:
                                data = os.read(_pty_master, 4096)
                            except OSError:
                                break
                            if not data:
                                break
                            text = _utf8_decoder.decode(data, final=False)
                            text = _ansi_escape.sub("", text)
                            stdout_chunks.append(text)
                            if text.strip():
                                for pty_line in text.split("\n"):
                                    _m = _pty_tool_pattern.match(pty_line.strip())
                                    if _m:
                                        _pty_tool_counter[0] += 1
                                        _tc_evt = {
                                            "event": "detected",
                                            "id": f"tc_{runtime}_{_pty_tool_counter[0]}",
                                            "name": _m.group(1),
                                            "input": _m.group(2).strip(),
                                            "runtime": runtime,
                                            "timestamp": time.strftime(
                                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                                            ),
                                        }
                                        if stream_buffer:
                                            stream_buffer.push("tool_call", _tc_evt)
                                        else:
                                            loop.call_soon_threadsafe(
                                                queue.put_nowait, ("tool_call", _tc_evt)
                                            )
                                if stream_buffer:
                                    stream_buffer.push("chunk", text)
                                else:
                                    loop.call_soon_threadsafe(
                                        queue.put_nowait, ("chunk", text)
                                    )
                    except Exception:
                        pass
                    finally:
                        try:
                            os.close(_pty_master)
                        except OSError:
                            pass
                        stderr_thread.join(timeout=5)
                        process.wait()
                else:
                    import json as _json

                    _claude_text_block_count = 0
                    _active_tool_calls = {}
                    _tool_call_counter = [0]
                    try:
                        for line in process.stdout:
                            stdout_chunks.append(line)
                            if runtime == "claude":
                                try:
                                    obj = _json.loads(line.strip())
                                    evt_type = obj.get("type")
                                    if evt_type == "stream_event":
                                        event = obj.get("event") or {}
                                        inner_type = event.get("type", "")
                                        if inner_type == "content_block_start":
                                            cb = event.get("content_block") or {}
                                            cb_type = cb.get("type")
                                            cb_index = event.get("index", 0)
                                            if cb_type == "text":
                                                if _claude_text_block_count > 0:
                                                    if stream_buffer:
                                                        stream_buffer.push(
                                                            "chunk", {"text": "\n\n"}
                                                        )
                                                    else:
                                                        loop.call_soon_threadsafe(
                                                            queue.put_nowait,
                                                            ("chunk", {"text": "\n\n"}),
                                                        )
                                                _claude_text_block_count += 1
                                            elif cb_type == "tool_use":
                                                tool_id = cb.get("id", f"tool_{cb_index}")
                                                tool_name = cb.get("name", "unknown")
                                                _active_tool_calls[cb_index] = {
                                                    "id": tool_id,
                                                    "name": tool_name,
                                                    "input_parts": [],
                                                    "started_at": time.strftime(
                                                        "%Y-%m-%dT%H:%M:%SZ",
                                                        time.gmtime(),
                                                    ),
                                                }
                                                tc_event = {
                                                    "event": "start",
                                                    "id": tool_id,
                                                    "name": tool_name,
                                                    "index": cb_index,
                                                }
                                                if stream_buffer:
                                                    stream_buffer.push("tool_call", tc_event)
                                                else:
                                                    loop.call_soon_threadsafe(
                                                        queue.put_nowait,
                                                        ("tool_call", tc_event),
                                                    )
                                        elif inner_type == "content_block_delta":
                                            delta = event.get("delta") or {}
                                            delta_type = delta.get("type")
                                            cb_index = event.get("index", 0)
                                            if delta_type == "text_delta":
                                                text = delta.get("text", "")
                                                if text:
                                                    if stream_buffer:
                                                        stream_buffer.push("chunk", {"text": text})
                                                    else:
                                                        loop.call_soon_threadsafe(
                                                            queue.put_nowait,
                                                            ("chunk", {"text": text}),
                                                        )
                                            elif delta_type == "input_json_delta":
                                                partial = delta.get("partial_json", "")
                                                if cb_index in _active_tool_calls:
                                                    _active_tool_calls[cb_index]["input_parts"].append(partial)
                                                    tc_event = {
                                                        "event": "input_delta",
                                                        "id": _active_tool_calls[cb_index]["id"],
                                                        "partial_json": partial,
                                                    }
                                                    if stream_buffer:
                                                        stream_buffer.push("tool_call", tc_event)
                                                    else:
                                                        loop.call_soon_threadsafe(
                                                            queue.put_nowait,
                                                            ("tool_call", tc_event),
                                                        )
                                        elif inner_type == "content_block_stop":
                                            cb_index = event.get("index", 0)
                                            if cb_index in _active_tool_calls:
                                                tc_info = _active_tool_calls.pop(cb_index)
                                                full_input = "".join(tc_info["input_parts"])
                                                try:
                                                    parsed_input = _json.loads(full_input) if full_input else {}
                                                except (ValueError, KeyError):
                                                    parsed_input = full_input
                                                tc_event = {
                                                    "event": "input_complete",
                                                    "id": tc_info["id"],
                                                    "name": tc_info["name"],
                                                    "input": parsed_input,
                                                    "started_at": tc_info["started_at"],
                                                }
                                                if stream_buffer:
                                                    stream_buffer.push("tool_call", tc_event)
                                                else:
                                                    loop.call_soon_threadsafe(
                                                        queue.put_nowait,
                                                        ("tool_call", tc_event),
                                                    )
                                    elif evt_type == "assistant":
                                        msg = obj.get("message") or {}
                                        for block in msg.get("content") or []:
                                            if block.get("type") == "tool_result":
                                                tc_event = {
                                                    "event": "result",
                                                    "id": block.get("tool_use_id", ""),
                                                    "is_error": block.get("is_error", False),
                                                }
                                                if stream_buffer:
                                                    stream_buffer.push("tool_call", tc_event)
                                                else:
                                                    loop.call_soon_threadsafe(
                                                        queue.put_nowait,
                                                        ("tool_call", tc_event),
                                                    )
                                except (ValueError, KeyError, AttributeError):
                                    pass
                            else:
                                _line_str = (
                                    line
                                    if isinstance(line, str)
                                    else line.decode("utf-8", errors="replace")
                                )
                                _line_stripped = _line_str.strip()
                                _tc_detected = None

                                if runtime == "gemini" and _line_stripped.startswith("{"):
                                    try:
                                        _gobj = _json.loads(_line_stripped)
                                        _gtype = _gobj.get("type", "")
                                        if _gtype == "message" and _gobj.get("role") == "assistant":
                                            _content = _gobj.get("content", "")
                                            if _content:
                                                if stream_buffer:
                                                    stream_buffer.push("chunk", _content)
                                                else:
                                                    loop.call_soon_threadsafe(
                                                        queue.put_nowait, ("chunk", _content)
                                                    )
                                            continue
                                        elif _gtype == "tool_use":
                                            _tool_call_counter[0] += 1
                                            tc_event = {
                                                "event": "detected",
                                                "id": _gobj.get("tool_id", f"tc_gemini_{_tool_call_counter[0]}"),
                                                "name": _gobj.get("tool_name", "tool"),
                                                "input": _json.dumps(_gobj.get("parameters", {})),
                                                "runtime": runtime,
                                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                            }
                                            if stream_buffer:
                                                stream_buffer.push("tool_call", tc_event)
                                            else:
                                                loop.call_soon_threadsafe(
                                                    queue.put_nowait, ("tool_call", tc_event)
                                                )
                                            continue
                                        elif _gtype in ("tool_result", "result", "init"):
                                            continue
                                    except (ValueError, KeyError, AttributeError):
                                        pass

                                if runtime == "copilot":
                                    import re as _re_tc

                                    _cp_tool_match = _re_tc.match(r"^[●⬤]\s+(.+)$", _line_stripped)
                                    if _cp_tool_match:
                                        _desc = _cp_tool_match.group(1).strip()
                                        _tool_name = "tool"
                                        if any(kw in _desc.lower() for kw in ["write", "edit", "create", "update", "modify"]):
                                            _tool_name = "write"
                                        elif any(kw in _desc.lower() for kw in ["delete", "remove", "rm"]):
                                            _tool_name = "shell"
                                        elif any(kw in _desc.lower() for kw in ["list", "ls", "find", "search", "glob"]):
                                            _tool_name = "glob"
                                        elif any(kw in _desc.lower() for kw in ["run", "exec", "install", "deploy", "build", "test"]):
                                            _tool_name = "shell"
                                        elif any(kw in _desc.lower() for kw in ["fetch", "curl", "http", "api", "download"]):
                                            _tool_name = "web_fetch"
                                        _tc_detected = {"name": _tool_name, "input": _desc}
                                    else:
                                        _cp_cmd_match = _re_tc.match(r"^\$\s+(.+)", _line_stripped)
                                        if _cp_cmd_match:
                                            _tc_detected = {"name": "shell", "input": _cp_cmd_match.group(1).strip()}
                                        if not _cp_tool_match:
                                            _cp_legacy = _re_tc.match(
                                                r"^(?:Running|Calling|Using)\s+(\w+)\s*(.*)",
                                                _line_stripped,
                                            )
                                            if _cp_legacy:
                                                _tc_detected = {
                                                    "name": _cp_legacy.group(1),
                                                    "input": _cp_legacy.group(2).strip(),
                                                }
                                        if not _tc_detected and _re_tc.match(r"^[│├└─]\s", _line_stripped):
                                            continue
                                elif runtime == "codex":
                                    import re as _re_tc

                                    _cx_match = _re_tc.match(
                                        r"^(?:Calling function|Tool|Executing|Running):\s*(\w[\w.]*)\s*(.*)",
                                        _line_stripped,
                                        _re_tc.IGNORECASE,
                                    )
                                    if _cx_match:
                                        _tc_detected = {
                                            "name": _cx_match.group(1),
                                            "input": _cx_match.group(2).strip(),
                                        }
                                    if not _tc_detected:
                                        _cx_cmd = _re_tc.match(r"^[$>]\s+(.+)", _line_stripped)
                                        if _cx_cmd:
                                            _tc_detected = {"name": "shell", "input": _cx_cmd.group(1).strip()}
                                    if not _tc_detected:
                                        _cx_fn = _re_tc.match(r"^(\w+)\((.+)\)\s*$", _line_stripped)
                                        if _cx_fn and any(
                                            kw in _cx_fn.group(1).lower()
                                            for kw in ["read", "write", "shell", "bash", "exec", "search", "list", "create", "edit", "patch", "apply"]
                                        ):
                                            _tc_detected = {
                                                "name": _cx_fn.group(1),
                                                "input": _cx_fn.group(2).strip(),
                                            }
                                elif runtime == "gemini":
                                    import re as _re_tc

                                    _gm_match = _re_tc.match(
                                        r"^[✦*]?\s*(?:Calling|Using tool|Function call|Running)\s+(\w[\w.]*)\s*(.*)",
                                        _line_stripped,
                                        _re_tc.IGNORECASE,
                                    )
                                    if _gm_match:
                                        _tc_detected = {
                                            "name": _gm_match.group(1),
                                            "input": _gm_match.group(2).strip(),
                                        }
                                    if not _tc_detected:
                                        _gm_fn = _re_tc.match(r"^[⚡✦*]?\s*(\w+)\((.+)\)\s*$", _line_stripped)
                                        if _gm_fn and any(
                                            kw in _gm_fn.group(1).lower()
                                            for kw in ["read", "write", "shell", "bash", "exec", "search", "list", "create", "edit", "file", "run", "cat", "ls", "find", "grep", "save", "update", "delete", "fetch", "curl", "get", "put"]
                                        ):
                                            _tc_detected = {
                                                "name": _gm_fn.group(1),
                                                "input": _gm_fn.group(2).strip(),
                                            }
                                    if not _tc_detected:
                                        _gm_cmd = _re_tc.match(
                                            r"^(?:[$>]\s+(.+)|Running\s+command:\s*(.+))",
                                            _line_stripped,
                                            _re_tc.IGNORECASE,
                                        )
                                        if _gm_cmd:
                                            _cmd_text = (_gm_cmd.group(1) or _gm_cmd.group(2) or "").strip()
                                            if _cmd_text:
                                                _tc_detected = {"name": "shell", "input": _cmd_text}

                                if _tc_detected:
                                    _tool_call_counter[0] += 1
                                    tc_event = {
                                        "event": "detected",
                                        "id": f"tc_{runtime}_{_tool_call_counter[0]}",
                                        "name": _tc_detected["name"],
                                        "input": _tc_detected.get("input", ""),
                                        "runtime": runtime,
                                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                    }
                                    if stream_buffer:
                                        stream_buffer.push("tool_call", tc_event)
                                    else:
                                        loop.call_soon_threadsafe(queue.put_nowait, ("tool_call", tc_event))
                                else:
                                    _su_match = None
                                    try:
                                        _su_line = line if isinstance(line, str) else line.decode("utf-8", errors="replace")
                                        _su_match = re.search(r"\[STATUS_UPDATE[:\s]*(.+?)\]", _su_line)
                                    except Exception:
                                        pass
                                    if _su_match:
                                        self.manager.set_live_status(n8n_session_id, _su_match.group(1).strip())
                                    else:
                                        if stream_buffer:
                                            stream_buffer.push("chunk", line)
                                        else:
                                            loop.call_soon_threadsafe(queue.put_nowait, ("chunk", line))
                    except Exception:
                        pass
                    finally:
                        self.manager.clear_live_status(n8n_session_id)
                        process.stdout.close()
                        stderr_thread.join(timeout=5)
                        process.wait()

                output = "".join(stdout_chunks) + ("".join(stderr_buf) if stderr_buf else "")
                self.manager.update_query_output(n8n_session_id, output)
                self.manager._last_exit_codes[n8n_session_id] = (
                    process.returncode if process.returncode is not None else 0
                )
                if stream_buffer:
                    stream_buffer.push("done", output)
                else:
                    loop.call_soon_threadsafe(queue.put_nowait, ("done", ""))
                return output

            import threading as _thr_bl

            _status_pattern = re.compile(r"\[STATUS_UPDATE[:\s]*(.+?)\]")
            _stderr_buf_bl: list = []

            def _drain_stderr_bl():
                try:
                    for _err_ln in process.stderr:
                        _stderr_buf_bl.append(_err_ln)
                except Exception:
                    pass

            _stderr_t = _thr_bl.Thread(target=_drain_stderr_bl, daemon=True)
            _stderr_t.start()

            _stdout_lines_bl: list = []
            try:
                for _line_bl in process.stdout:
                    _stdout_lines_bl.append(_line_bl)
                    _su_m = _status_pattern.search(_line_bl)
                    if _su_m:
                        self.manager.set_live_status(n8n_session_id, _su_m.group(1).strip())

                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    self.manager.clear_live_status(n8n_session_id)
                    timeout_min = timeout / 60
                    return f"Error: Command timed out (exceeded {timeout}s / {timeout_min:.1f}min)"
                finally:
                    _stderr_t.join(timeout=5)

                output = "".join(_stdout_lines_bl) + "".join(_stderr_buf_bl)
                output = re.sub(r"\[STATUS_UPDATE[:\s]*[^\]]*\]\s*\n?", "", output)
                self.manager.update_query_output(n8n_session_id, output)
                self.manager._last_exit_codes[n8n_session_id] = (
                    process.returncode if process.returncode is not None else 0
                )
                return output
            finally:
                self.manager.clear_live_status(n8n_session_id)
                self.manager.clear_running_query(n8n_session_id)

        except Exception as e:
            self.manager.clear_running_query(n8n_session_id)
            return f"Error: Failed to execute command: {e}"
        finally:
            if stream_info:
                self.manager.clear_running_query(n8n_session_id)
            if _pty_master is not None:
                try:
                    os.close(_pty_master)
                except OSError:
                    pass


@dataclass(frozen=True)
class RuntimeExecutionRequest:
    runtime: str
    prompt: str
    model: str
    agent: str
    session_id: Optional[str]
    can_resume: bool
    n8n_session_id: str
    effective_timeout: int
    render_type: str
    mode: str = "restricted"


class RuntimeExecutor:
    def execute(self, request: RuntimeExecutionRequest) -> str:
        raise NotImplementedError


class MethodRuntimeExecutor(RuntimeExecutor):
    """Strategy that delegates execution to an existing SessionManager method."""

    def __init__(self, manager: Any, method_name: str, include_mode: bool = False):
        self.manager = manager
        self.method_name = method_name
        self.include_mode = include_mode

    def execute(self, request: RuntimeExecutionRequest) -> str:
        method = getattr(self.manager, self.method_name)
        args = [
            request.prompt,
            request.model,
            request.agent,
            request.session_id if request.can_resume else None,
            request.can_resume,
            request.n8n_session_id,
            request.effective_timeout,
            request.render_type,
        ]
        if self.include_mode:
            args.append(request.mode)
        return method(*args)


class RuntimeExecutorRegistry:
    """Strategy registry for runtime-specific execution."""

    def __init__(self, manager: Any):
        self.executors: Dict[str, RuntimeExecutor] = {
            "copilot": MethodRuntimeExecutor(manager, "run_copilot"),
            "copilot-sdk": MethodRuntimeExecutor(manager, "run_copilot_sdk", include_mode=True),
            "opencode": MethodRuntimeExecutor(manager, "run_opencode"),
            "claude": MethodRuntimeExecutor(manager, "run_claude", include_mode=True),
            "claude-sdk": MethodRuntimeExecutor(manager, "run_claude_sdk", include_mode=True),
            "gemini": MethodRuntimeExecutor(manager, "run_gemini"),
            "codex": MethodRuntimeExecutor(manager, "run_codex"),
            "devin": MethodRuntimeExecutor(manager, "run_devin", include_mode=True),
            "cursor": MethodRuntimeExecutor(manager, "run_cursor"),
            "wee": MethodRuntimeExecutor(manager, "run_wee_native"),
        }

    def execute(self, request: RuntimeExecutionRequest) -> str:
        executor = self.executors.get(request.runtime)
        if executor is None:
            return f"Error: Unknown runtime '{request.runtime}'"
        return executor.execute(request)
