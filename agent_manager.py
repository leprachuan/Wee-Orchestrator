#!/usr/bin/env python3
"""
Unified AI Session Wrapper for N8N Integration
Wraps GitHub Copilot CLI and OpenCode CLI
Manages session ID mapping between N8N chat sessions and AI backend sessions
"""

import argparse
import hashlib
import json
import logging
import os
import re
import secrets as _secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

# Dynamically determine the repo base directory (works regardless of where repo is cloned)
SCRIPT_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_MANIFEST_PATH = Path(SCRIPT_BASE_DIR) / "model-manifest.json"

# ── Theme constants (F025) ──────────────────────────────────────────────
_BUILTIN_THEMES = [
    {
        "name": "emerald",
        "label": "Emerald",
        "description": "Default glassmorphism",
        "builtin": True,
    },
    {
        "name": "midnight",
        "label": "Midnight",
        "description": "Deep blue ocean",
        "builtin": True,
    },
    {
        "name": "sunrise",
        "label": "Sunrise",
        "description": "Warm light mode",
        "builtin": True,
    },
    {
        "name": "cyberpunk",
        "label": "Cyberpunk",
        "description": "Neon pink & cyan",
        "builtin": True,
    },
]
_THEME_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
_themes_dir = Path(os.path.abspath(__file__)).parent / "webui" / "themes"


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Credential sanitization for UI display
# ---------------------------------------------------------------------------

# Pre-compiled patterns for sensitive header detection in curl/HTTP commands
_SENSITIVE_HEADER_RE = re.compile(
    r"""(-H\s+["'])"""
    r"""(Authorization|X-API-Key|X-Auth-Token|X-Auth-Secret|"""
    r"""X-Access-Token|Proxy-Authorization|api-key|api_key"""
    r"""):\s*[^"']*(["'])""",
    re.IGNORECASE,
)
_SENSITIVE_HEADER_GENERIC_RE = re.compile(
    r"""(-H\s+["'])([^"']*(?:password|secret|token|key|credential|bearer)[^:]*):\s*[^"']*(["'])""",
    re.IGNORECASE,
)
_BEARER_TOKEN_RE = re.compile(
    r"""(Bearer\s+)\S+""",
    re.IGNORECASE,
)
_BASIC_AUTH_RE = re.compile(
    r"""(Basic\s+)\S+""",
    re.IGNORECASE,
)
_COOKIE_HEADER_RE = re.compile(
    r"""(-H\s+["'])Cookie:\s*[^"']*(["'])""",
    re.IGNORECASE,
)
_CURL_USER_RE = re.compile(
    r"""(-u\s+)(\S+)""",
)

_ELEVATED_MODE_INSTRUCTIONS = (
    "\n\n[ELEVATED MODE ENABLED]\n"
    "Full permissions granted. ALL commands requiring elevated privileges MUST automatically "
    "prefix with 'sudo' — no exceptions. This includes:\n"
    "• Service management: sudo systemctl restart/start/stop/reload/enable/disable <service>\n"
    "• Network commands: sudo ping, sudo ssh, sudo iptables, sudo ip, etc.\n"
    "• System administration: sudo journalctl, sudo systemd-*, sudo chmod/chown on system paths\n"
    "• Any command that would fail due to insufficient permissions\n"
    "Sudo is configured without password prompt (NOPASSWD:ALL). "
    "Never ask for confirmation — execute privileged commands immediately with sudo."
)


def _sanitize_command_for_display(text: str) -> str:
    """Redact sensitive headers and credentials from command strings for UI display.

    Handles curl-style -H headers, Bearer/Basic auth tokens, cookies, and
    -u user:password patterns.  Only modifies the display string — the actual
    command executed is never altered.
    """
    if not isinstance(text, str) or not text:
        return text
    # Named sensitive headers: Authorization, X-API-Key, etc.
    text = _SENSITIVE_HEADER_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}: [REDACTED]{m.group(3)}", text
    )
    # Cookie header
    text = _COOKIE_HEADER_RE.sub(
        lambda m: f"{m.group(1)}Cookie: [REDACTED]{m.group(2)}", text
    )
    # Generic headers containing password/secret/token/key
    text = _SENSITIVE_HEADER_GENERIC_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}: [REDACTED]{m.group(3)}", text
    )
    # Inline Bearer / Basic tokens (e.g. in JSON or plain text)
    text = _BEARER_TOKEN_RE.sub(r"\1[REDACTED]", text)
    text = _BASIC_AUTH_RE.sub(r"\1[REDACTED]", text)
    # curl -u user:password
    text = _CURL_USER_RE.sub(r"\1[REDACTED]", text)
    return text


def _sanitize_tool_call_for_display(data: dict) -> dict:
    """Return a shallow copy of a tool_call event dict with sensitive
    credentials redacted from the ``input`` and ``output`` fields.
    Other fields are passed through unchanged."""
    if not isinstance(data, dict):
        return data
    sanitized = dict(data)
    inp = data.get("input")
    if inp is not None:
        if isinstance(inp, str):
            sanitized["input"] = _sanitize_command_for_display(inp)
        elif isinstance(inp, dict):
            new_inp = dict(inp)
            for field in ("command", "input", "code", "content", "url", "body"):
                if field in new_inp and isinstance(new_inp[field], str):
                    new_inp[field] = _sanitize_command_for_display(new_inp[field])
            sanitized["input"] = new_inp
    out = data.get("output")
    if out is not None and isinstance(out, str):
        sanitized["output"] = _sanitize_command_for_display(out)
    pj = data.get("partial_json")
    if pj and isinstance(pj, str):
        sanitized["partial_json"] = _sanitize_command_for_display(pj)
    return sanitized


def _resolve_silent_default(channel: str) -> bool:
    """Resolve silent_mode default from WEE_VERBOSE env var or channel.

    Priority: WEE_VERBOSE env var > channel-based default.
    WEE_VERBOSE=true means verbose (silent=false).
    WEE_VERBOSE=false means not verbose (silent=true).
    """
    env_val = os.environ.get("WEE_VERBOSE", "").strip().lower()
    if env_val in ("true", "1", "on"):
        return False  # verbose = not silent
    if env_val in ("false", "0", "off"):
        return True  # not verbose = silent
    # Default: channel-based
    return channel in ("telegram", "webex")


def _parse_claude_stream_json_line(line: str, active_tool_calls: dict) -> list:
    """Parse one stream-json line from the Claude CLI.

    Returns a list of ``(channel, event_dict)`` tuples.  The caller is
    responsible for dispatching each tuple to the appropriate queue or
    stream buffer.

    Special channel ``"text_block_start"`` signals that a new text block
    has begun; the caller should emit a newline separator if needed.

    *active_tool_calls* is mutated in place to track in-flight tool calls.
    """
    import json as _json
    import time as _time

    results: list = []
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
                    results.append(("text_block_start", {}))
                elif cb_type == "tool_use":
                    tool_id = cb.get("id", f"tool_{cb_index}")
                    tool_name = cb.get("name", "unknown")
                    active_tool_calls[cb_index] = {
                        "id": tool_id,
                        "name": tool_name,
                        "input_parts": [],
                        "started_at": _time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", _time.gmtime()
                        ),
                    }
                    results.append(
                        (
                            "tool_call",
                            {
                                "event": "start",
                                "id": tool_id,
                                "name": tool_name,
                                "index": cb_index,
                            },
                        )
                    )

            elif inner_type == "content_block_delta":
                delta = event.get("delta") or {}
                delta_type = delta.get("type")
                cb_index = event.get("index", 0)
                if delta_type == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        results.append(("chunk", {"text": text}))
                elif delta_type == "input_json_delta":
                    partial = delta.get("partial_json", "")
                    if cb_index in active_tool_calls:
                        active_tool_calls[cb_index]["input_parts"].append(partial)
                        results.append(
                            (
                                "tool_call",
                                {
                                    "event": "input_delta",
                                    "id": active_tool_calls[cb_index]["id"],
                                    "partial_json": partial,
                                },
                            )
                        )

            elif inner_type == "content_block_stop":
                cb_index = event.get("index", 0)
                if cb_index in active_tool_calls:
                    tc_info = active_tool_calls.pop(cb_index)
                    full_input = "".join(tc_info["input_parts"])
                    try:
                        parsed_input = _json.loads(full_input) if full_input else {}
                    except (ValueError, KeyError):
                        parsed_input = full_input
                    results.append(
                        (
                            "tool_call",
                            {
                                "event": "input_complete",
                                "id": tc_info["id"],
                                "name": tc_info["name"],
                                "input": parsed_input,
                                "started_at": tc_info["started_at"],
                            },
                        )
                    )

        elif evt_type == "user":
            # Claude CLI emits tool_result blocks in user-role messages,
            # not assistant messages.
            msg = obj.get("message") or {}
            for block in msg.get("content") or []:
                if block.get("type") == "tool_result":
                    raw = block.get("content", "")
                    if isinstance(raw, list):
                        output = " ".join(
                            p.get("text", "")
                            for p in raw
                            if isinstance(p, dict) and p.get("type") == "text"
                        )
                    else:
                        output = str(raw) if raw else ""
                    is_err = block.get("is_error", False)
                    results.append(
                        (
                            "tool_call",
                            {
                                "event": "result",
                                "id": block.get("tool_use_id", ""),
                                "status": "error" if is_err else "completed",
                                "output": output[:500],
                                "is_error": is_err,
                            },
                        )
                    )

    except (ValueError, KeyError, AttributeError):
        pass
    return results


class RateLimiter:
    """In-memory per-IP rate limiter with sliding window."""

    def __init__(self):
        self.records: Dict[str, Dict[str, List[float]]] = {}
        self._lock = threading.Lock()

    def check(self, ip: str, endpoint: str, max_requests: int, window: int) -> bool:
        """Return True if request is allowed, False if rate limited."""
        now = time.time()
        with self._lock:
            ep_list = self.records.setdefault(ip, {}).setdefault(endpoint, [])
            ep_list[:] = [t for t in ep_list if now - t < window]
            if len(ep_list) >= max_requests:
                return False
            ep_list.append(now)
            return True

    def cleanup(self):
        """Remove all empty entries."""
        with self._lock:
            for ip in list(self.records):
                for ep in list(self.records[ip]):
                    if not self.records[ip][ep]:
                        del self.records[ip][ep]
                if not self.records[ip]:
                    del self.records[ip]


class AuthManager:
    """Manages pairing codes, session tokens, and shared key validation."""

    def __init__(
        self,
        shared_key: str,
        pairing_code_length: int = 6,
        pairing_code_ttl: int = 300,
        session_token_ttl: int = 3600,
        session_token_absolute_ttl: int = 86400,
        sessions_file: Optional[str] = None,
    ):
        self.shared_key = shared_key
        self.pairing_code_length = pairing_code_length
        self.pairing_code_ttl = pairing_code_ttl
        self.session_token_ttl = session_token_ttl
        self.session_token_absolute_ttl = session_token_absolute_ttl
        self.sessions_file = sessions_file or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".task-scheduler",
            "sessions.json",
        )
        self.pairing_codes: Dict[str, dict] = {}
        self.session_tokens: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load_sessions()

    def validate_shared_key(self, token: str) -> bool:
        """Validate a Bearer token as a shared key. Expects 'shared_<key>'."""
        if not token.startswith("shared_"):
            return False
        return token[7:] == self.shared_key

    def _load_sessions(self):
        """Load persisted sessions from file on startup."""
        if not os.path.exists(self.sessions_file):
            return
        try:
            with open(self.sessions_file) as f:
                data = json.load(f)
                now = time.time()
                with self._lock:
                    for token, entry in data.items():
                        created_at = entry.get("created_at", now)
                        absolute_expires_at = entry.get(
                            "absolute_expires_at",
                            created_at + self.session_token_absolute_ttl,
                        )
                        entry["absolute_expires_at"] = absolute_expires_at
                        if (
                            entry.get("expires_at", 0) > now
                            and absolute_expires_at > now
                        ):
                            self.session_tokens[token] = entry
        except Exception:
            pass

    def _save_sessions(self):
        """Persist sessions to file."""
        try:
            os.makedirs(os.path.dirname(self.sessions_file), exist_ok=True)
            with open(self.sessions_file, "w") as f:
                json.dump(self.session_tokens, f)
        except Exception:
            pass

    def generate_pairing_code(self, identity: str, channel: str) -> str:
        """Generate a numeric pairing code and store it."""
        code = "".join(
            [str(_secrets.randbelow(10)) for _ in range(self.pairing_code_length)]
        )
        now = time.time()
        with self._lock:
            self.pairing_codes[code] = {
                "identity": identity,
                "channel": channel,
                "created_at": now,
                "expires_at": now + self.pairing_code_ttl,
            }
        return code

    def verify_pairing_code(self, code: str, identity: str) -> Optional[str]:
        """Verify pairing code. Returns session token on success, None on failure."""
        with self._lock:
            entry = self.pairing_codes.get(code)
            if not entry:
                return None
            if entry["identity"] != identity:
                return None
            if time.time() > entry["expires_at"]:
                del self.pairing_codes[code]
                return None
            del self.pairing_codes[code]
        token = f"session_{_secrets.token_urlsafe(32)}"
        now = time.time()
        with self._lock:
            self.session_tokens[token] = {
                "identity": identity,
                "channel": entry["channel"],
                "created_at": now,
                "last_used": now,
                "expires_at": now + self.session_token_ttl,
                "absolute_expires_at": now + self.session_token_absolute_ttl,
            }
        self._save_sessions()
        return token

    def validate_session_token(self, token: str) -> Optional[dict]:
        """Validate session token. Returns identity info or None."""
        with self._lock:
            entry = self.session_tokens.get(token)
            if not entry:
                return None
            now = time.time()
            created_at = entry.get("created_at", now)
            absolute_expires_at = entry.get(
                "absolute_expires_at", created_at + self.session_token_absolute_ttl
            )
            entry["absolute_expires_at"] = absolute_expires_at
            if now > entry["expires_at"] or now > absolute_expires_at:
                del self.session_tokens[token]
                self._save_sessions()
                return None
            entry["last_used"] = now
            # Sliding expiration still applies, but it cannot pass the hard cap.
            entry["expires_at"] = min(now + self.session_token_ttl, absolute_expires_at)
            self._save_sessions()
            return {"identity": entry["identity"], "channel": entry["channel"]}

    def cleanup_expired(self):
        """Remove expired pairing codes and session tokens."""
        now = time.time()
        with self._lock:
            for code in list(self.pairing_codes):
                if now > self.pairing_codes[code]["expires_at"]:
                    del self.pairing_codes[code]
            for token in list(self.session_tokens):
                entry = self.session_tokens[token]
                created_at = entry.get("created_at", now)
                absolute_expires_at = entry.get(
                    "absolute_expires_at", created_at + self.session_token_absolute_ttl
                )
                entry["absolute_expires_at"] = absolute_expires_at
                if now > entry["expires_at"] or now > absolute_expires_at:
                    del self.session_tokens[token]
        self._save_sessions()


_FALLBACK_PATTERNS = [
    r"429",
    r"rate.?limit",
    r"quota.?exceeded",
    r"401",
    r"unauthorized",
    r"missing.?authentication",
    r"api[_\-]?key.?(invalid|expired|missing)",
    r"503",
    r"service.?unavailable",
    r"502",
    r"bad.?gateway",
    r"connection.?refused",
    r"timed?.?out",
    r"etimedout",
    r"overloaded",
]

# Copilot permission-mode instruction blocks (issue #190).
# Defined as module-level constants to avoid duplication across original,
# proactive-restart, and reactive-recovery paths in run_copilot().
_COPILOT_ELEVATED_MODE_INSTRUCTIONS = (
    "\n\n[ELEVATED MODE ENABLED]\n"
    "Full permissions granted. ALL commands requiring elevated privileges MUST automatically "  # noqa: E501
    "prefix with 'sudo' \u2014 no exceptions. This includes:\n"
    "\u2022 Service management: sudo systemctl restart/start/stop/reload/enable/disable <service>\n"  # noqa: E501
    "\u2022 Network commands: sudo ping, sudo ssh, sudo iptables, sudo ip, etc.\n"
    "\u2022 System administration: sudo journalctl, sudo systemd-*, sudo chmod/chown on system paths\n"  # noqa: E501
    "\u2022 Any command that would fail due to insufficient permissions\n"
    "Sudo is configured without password prompt (NOPASSWD:ALL). "
    "Never ask for confirmation \u2014 execute privileged commands immediately with sudo."  # noqa: E501
)
_COPILOT_SANDBOXED_MODE_INSTRUCTIONS = (
    "\n\n[SANDBOXED MODE ENABLED]\n"
    "Read-only access only. Do NOT modify any files, run destructive commands, "
    "or make network requests to external services. Analysis and reporting only."
)
# Session expiry constants (issue #190).
# Used in both proactive age check and reactive token expiry recovery.
_COPILOT_SESSION_MAX_AGE_SEC = 25 * 60  # 25 minutes
_TOKEN_EXPIRED_MARKER = "Session token expired"


class BackgroundTaskManager:
    """Manages background task lifecycle: creation, tracking, output capture, cleanup."""  # noqa: E501

    MAX_TASKS_PER_USER = int(os.environ.get("BG_MAX_TASKS_PER_USER", "5"))
    MAX_OUTPUT_LINES = 500
    CLEANUP_AGE_HOURS = int(os.environ.get("BG_CLEANUP_HOURS", "24"))

    def __init__(self):
        home = os.path.expanduser("~")
        copilot_dir = os.path.join(home, ".copilot")
        os.makedirs(copilot_dir, exist_ok=True)
        # Use environment-specific file path to avoid prod/dev contamination
        api_port = os.environ.get("API_PORT", "8001")
        env_suffix = "-dev" if api_port == "8001" else ""
        self._path = os.path.join(copilot_dir, f"background-tasks{env_suffix}.json")
        self._lock = threading.Lock()
        self._bg_events = {}  # {origin_session_id: [event_dicts]}
        self._bg_events_lock = threading.Lock()

    def _load(self) -> list:
        try:
            with open(self._path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self, tasks: list):
        with open(self._path, "w") as f:
            json.dump(tasks, f, indent=2, default=str)

    def _user_key(self, channel: str, identity: str) -> str:
        # Strip channel prefix from identity to avoid double-prefixing
        prefix = f"{channel}_"
        if identity.startswith(prefix):
            return identity
        return f"{channel}_{identity}"

    def create_task(
        self,
        task_id: str,
        session_id: str,
        user_identity: str,
        channel: str,
        agent: str,
        runtime: str,
        model: str,
        prompt: str,
        pid: int = 0,
        status: str = "running",
        timeout: int = None,
        notify: bool = True,
        origin_session_id: str = None,
        permission_mode: str = "restricted",
        fallback_runtime: str = None,
        fallback_model: str = None,
    ) -> dict:
        task = {
            "task_id": task_id,
            "session_id": session_id,
            "user_key": self._user_key(channel, identity=user_identity),
            "channel": channel,
            "user_identity": user_identity,
            "agent": agent,
            "runtime": runtime,
            "model": model,
            "prompt": prompt,
            "status": status,
            "pid": pid,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "started_at": (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                if status == "running"
                else None
            ),
            "completed_at": None,
            "output_lines": [],
            "tool_calls": [],
            "final_response": None,
            "error": None,
            "timeout": timeout,
            "notify": notify,
            "origin_session_id": origin_session_id,
            "permission_mode": permission_mode,
            "fallback_runtime": fallback_runtime,
            "fallback_model": fallback_model,
            "used_fallback": False,
            "actual_runtime": None,
            "actual_model": None,
        }
        with self._lock:
            tasks = self._load()
            tasks.append(task)
            self._save(tasks)
        return task

    def get_task(self, task_id: str) -> Optional[dict]:
        with self._lock:
            for t in self._load():
                if t["task_id"] == task_id:
                    return t
        return None

    def _identity_matches(self, task: dict, channel: str, identity: str) -> bool:
        """Check if a task belongs to this user (for rate-limiting/queue management only).
        NOT used for visibility -- all authorized users can see all tasks.
        """
        stored_identity = task.get("user_identity")
        if stored_identity is not None:
            return stored_identity == identity
        # Fallback: check user_key with any channel prefix
        return task.get("user_key") == self._user_key(channel, identity)

    def list_tasks(self, channel: str, identity: str) -> list:
        with self._lock:
            return [
                t for t in self._load() if self._identity_matches(t, channel, identity)
            ]

    def list_all_tasks(self) -> list:
        """Return all tasks regardless of identity/channel."""
        with self._lock:
            return list(self._load())

    def count_running(self, channel: str, identity: str, agent: str = None) -> int:
        tasks = self.list_tasks(channel, identity)
        if agent:
            tasks = [t for t in tasks if t.get("agent") == agent]
        return sum(1 for t in tasks if t["status"] == "running")

    def count_queued(self, channel: str, identity: str, agent: str = None) -> int:
        tasks = self.list_tasks(channel, identity)
        if agent:
            tasks = [t for t in tasks if t.get("agent") == agent]
        return sum(1 for t in tasks if t["status"] == "queued")

    def get_next_queued(
        self, channel: str, identity: str, agent: str = None
    ) -> Optional[dict]:
        """Return the oldest queued task for this user, optionally filtered by agent."""
        queued = [
            t
            for t in self.list_tasks(channel, identity)
            if t["status"] == "queued" and (not agent or t.get("agent") == agent)
        ]
        if not queued:
            return None
        return min(queued, key=lambda t: t.get("created_at", ""))

    def promote_queued_task(self, task_id: str, session_id: str):
        """Transition a queued task to running status with a fresh session_id."""
        self.update_task(
            task_id,
            status="running",
            session_id=session_id,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def push_bg_event(self, origin_session_id, event):
        """Push an in-thread completion event."""
        if not origin_session_id:
            return
        with self._bg_events_lock:
            self._bg_events.setdefault(origin_session_id, []).append(event)

    def pop_bg_events(self, session_id):
        """Return and clear pending bg-task events."""
        with self._bg_events_lock:
            return self._bg_events.pop(session_id, [])

    def update_task(self, task_id: str, **fields):
        with self._lock:
            tasks = self._load()
            for t in tasks:
                if t["task_id"] == task_id:
                    t.update(fields)
                    break
            self._save(tasks)

    def append_output(self, task_id: str, line: str):
        with self._lock:
            tasks = self._load()
            for t in tasks:
                if t["task_id"] == task_id:
                    t["output_lines"].append(line)
                    # Keep only last MAX_OUTPUT_LINES
                    if len(t["output_lines"]) > self.MAX_OUTPUT_LINES:
                        t["output_lines"] = t["output_lines"][-self.MAX_OUTPUT_LINES :]
                    break
            self._save(tasks)

    MAX_TOOL_CALLS = 200

    def append_tool_call(self, task_id: str, tool_call: dict):
        """Append a new tool call event to the task."""
        with self._lock:
            tasks = self._load()
            for t in tasks:
                if t["task_id"] == task_id:
                    if "tool_calls" not in t:
                        t["tool_calls"] = []
                    t["tool_calls"].append(tool_call)
                    if len(t["tool_calls"]) > self.MAX_TOOL_CALLS:
                        t["tool_calls"] = t["tool_calls"][-self.MAX_TOOL_CALLS :]
                    break
            self._save(tasks)

    def update_tool_call(self, task_id: str, call_id: str, **fields):
        """Update an existing tool call by its id."""
        with self._lock:
            tasks = self._load()
            for t in tasks:
                if t["task_id"] == task_id:
                    for tc in t.get("tool_calls", []):
                        if tc.get("id") == call_id:
                            tc.update(fields)
                            break
                    break
            self._save(tasks)

    def mark_fallback_used(
        self, task_id: str, fallback_runtime: str, fallback_model: str
    ):
        """Record that a task retried on a fallback runtime."""
        self.update_task(
            task_id,
            used_fallback=True,
            actual_runtime=fallback_runtime,
            actual_model=fallback_model,
        )

    def complete_task(self, task_id: str, final_response: str):
        self.update_task(
            task_id,
            status="completed",
            final_response=final_response,
            completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def fail_task(self, task_id: str, error: str):
        self.update_task(
            task_id,
            status="failed",
            error=error,
            completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def kill_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if not task:
            return False
        if task["status"] == "queued":
            # Cancel queued tasks directly (no process to kill)
            self.update_task(
                task_id,
                status="killed",
                completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            return True
        if task["status"] != "running":
            return False
        pid = task.get("pid", 0)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.update_task(
            task_id,
            status="killed",
            completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        return True

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            tasks = self._load()
            before = len(tasks)
            tasks = [t for t in tasks if t["task_id"] != task_id]
            if len(tasks) < before:
                self._save(tasks)
                return True
        return False

    def cleanup_old(self):
        cutoff = time.time() - (self.CLEANUP_AGE_HOURS * 3600)
        with self._lock:
            tasks = self._load()
            kept = []
            for t in tasks:
                if t["status"] == "running":
                    kept.append(t)
                    continue
                completed = t.get("completed_at")
                if completed:
                    try:
                        ct = time.mktime(time.strptime(completed, "%Y-%m-%dT%H:%M:%SZ"))
                        if ct > cutoff:
                            kept.append(t)
                            continue
                    except (ValueError, OverflowError):
                        kept.append(t)
                        continue
                else:
                    kept.append(t)
            if len(kept) < len(tasks):
                self._save(kept)

    def reconcile_stale_tasks(self) -> dict:
        """Reconcile orphaned tasks after a service restart.

        - Mark 'running' tasks as 'failed' if their PID is no longer alive.
        - Return a summary dict with counts of what was reconciled.
        """
        now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        reconciled = {"stale_running": 0, "queued_ready": 0}
        with self._lock:
            tasks = self._load()
            changed = False
            for t in tasks:
                if t["status"] == "running":
                    pid = t.get("pid", 0)
                    alive = False
                    if pid:
                        try:
                            os.kill(pid, 0)
                            alive = True
                        except (ProcessLookupError, PermissionError):
                            pass
                    if not alive:
                        t["status"] = "failed"
                        t["error"] = "Orphaned after service restart (PID not found)"
                        t["completed_at"] = now_str
                        reconciled["stale_running"] += 1
                        changed = True
            if changed:
                self._save(tasks)
            # Count queued tasks that are now promotable
            reconciled["queued_ready"] = sum(
                1 for t in tasks if t["status"] == "queued"
            )
        return reconciled

    # -- Steering --------------------------------------------------------
    STEERING_DIR = os.path.join(
        os.environ.get("SCRIPT_BASE_DIR", os.path.dirname(os.path.abspath(__file__))),
        ".task-scheduler",
        "steering",
    )

    def get_steering_path(self, task_id: str) -> str:
        return os.path.join(self.STEERING_DIR, f"{task_id}.md")

    def write_steering(self, task_id: str, instruction: str) -> str:
        os.makedirs(self.STEERING_DIR, exist_ok=True)
        path = self.get_steering_path(task_id)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        entry = f"\n### {ts}\n{instruction}\n"
        if not os.path.exists(path):
            entry = f"## Steering Instructions\n{entry}"
        with open(path, "a") as f:
            f.write(entry)
        return path

    def read_steering(self, task_id: str) -> Optional[str]:
        path = self.get_steering_path(task_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r") as f:
                content = f.read().strip()
            return content if content else None
        except OSError:
            return None

    def cleanup_steering(self, task_id: str) -> None:
        path = self.get_steering_path(task_id)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


# Executable resolution
def find_executable(name: str) -> Optional[str]:
    """Find executable in multiple common locations

    Searches in order:
    1. System PATH (using shutil.which)
    2. Homebrew ARM64 (M1/M2 Macs): /opt/homebrew/bin/
    3. Homebrew Intel: /usr/local/bin/
    4. Standard system bin: /usr/bin/

    Args:
        name: Name of the executable (e.g., "copilot", "claude")

    Returns:
        Full path to executable if found, None otherwise
    """
    # First try system PATH
    which_result = shutil.which(name)
    if which_result:
        return which_result

    # Search additional common locations
    search_paths = [
        Path.home() / ".local" / "bin" / name,  # User-local installs
        Path("/opt/homebrew/bin") / name,  # Homebrew ARM64 (M1/M2 Macs)
        Path("/usr/local/bin") / name,  # Homebrew Intel / manual installs
        Path("/usr/bin") / name,  # Standard system location
    ]

    for path in search_paths:
        if path.exists() and os.access(path, os.X_OK):
            return str(path)

    return None


# Environment-based configuration
def get_default_agent() -> str:
    """Get default agent from environment or use orchestrator"""
    return os.environ.get("COPILOT_DEFAULT_AGENT", "orchestrator")


def get_default_model() -> str:
    """Get default model from environment or use gpt-5-mini"""
    return os.environ.get("COPILOT_DEFAULT_MODEL", "gpt-5-mini")


def get_default_runtime() -> str:
    """Get default runtime from environment or use copilot"""
    return os.environ.get("COPILOT_DEFAULT_RUNTIME", "copilot")


def check_runtime_available(runtime: str) -> bool:
    """Check if a runtime is available on the system.

    Args:
        runtime: Runtime ID (e.g., 'copilot', 'claude', 'copilot-sdk')

    Returns:
        True if the runtime is available, False otherwise
    """
    # Map runtime IDs to their executable/module names
    runtime_map = {
        "copilot": "copilot",
        "copilot-sdk": "copilot",  # Python package
        "claude": "claude",
        "claude-sdk": "claude-sdk",  # Python package
        "gemini": "gemini",
        "codex": "codex",
        "devin": "devin",
        "cursor": "agent",  # Cursor uses 'agent' binary
        "opencode": "opencode",
        "wee": "openai",  # OpenAI-compatible API (no binary needed),
    }

    executable_name = runtime_map.get(runtime)
    if not executable_name:
        return False

    # For Python packages (SDK runtimes), try importing
    if runtime in ("copilot-sdk", "claude-sdk", "wee"):
        try:
            if runtime == "claude-sdk":
                # Package is installed as claude_agent_sdk
                __import__("claude_agent_sdk")
            else:
                module_name = executable_name.replace("-", "_")
                __import__(module_name)
            return True
        except ImportError:
            return False

    # For CLI runtimes, check if executable exists
    # First try system PATH
    if shutil.which(executable_name):
        return True

    # Search additional common locations
    search_paths = [
        Path.home() / ".local" / "bin" / executable_name,
        Path("/opt/homebrew/bin") / executable_name,
        Path("/usr/local/bin") / executable_name,
        Path("/usr/bin") / executable_name,
    ]

    for path in search_paths:
        if path.exists() and path.is_file():
            return True

    return False



# ── Runtime Disable/Enable Manager ──────────────────────────────────────

class DisabledRuntimesManager:
    """Manages disabled runtimes - persisted in a JSON file."""
    
    def __init__(self, config_dir: str = ".settings"):
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(exist_ok=True)
        self.config_file = self.config_dir / "disabled_runtimes.json"
        self._load()
    
    def _load(self):
        """Load disabled runtimes from config file."""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    data = json.load(f)
                    self.disabled_runtimes = set(data.get("disabled", []))
            except Exception as e:
                logging.warning(f"Failed to load disabled runtimes: {e}")
                self.disabled_runtimes = set()
        else:
            self.disabled_runtimes = set()
    
    def _save(self):
        """Save disabled runtimes to config file."""
        try:
            with open(self.config_file, 'w') as f:
                json.dump({"disabled": sorted(list(self.disabled_runtimes))}, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save disabled runtimes: {e}")
    
    def is_disabled(self, runtime_id: str) -> bool:
        """Check if a runtime is disabled."""
        return runtime_id in self.disabled_runtimes
    
    def disable(self, runtime_id: str) -> bool:
        """Disable a runtime. Returns True if changed."""
        if runtime_id not in self.disabled_runtimes:
            self.disabled_runtimes.add(runtime_id)
            self._save()
            return True
        return False
    
    def enable(self, runtime_id: str) -> bool:
        """Enable a runtime. Returns True if changed."""
        if runtime_id in self.disabled_runtimes:
            self.disabled_runtimes.discard(runtime_id)
            self._save()
            return True
        return False
    
    def set_disabled(self, disabled_list: List[str]):
        """Set the entire list of disabled runtimes."""
        self.disabled_runtimes = set(disabled_list)
        self._save()
    
    def get_disabled(self) -> List[str]:
        """Get list of disabled runtimes."""
        return sorted(list(self.disabled_runtimes))


# Global instance
_disabled_runtimes_manager = None

def get_disabled_runtimes_manager() -> DisabledRuntimesManager:
    """Get or create the global DisabledRuntimesManager instance."""
    global _disabled_runtimes_manager
    if _disabled_runtimes_manager is None:
        _disabled_runtimes_manager = DisabledRuntimesManager()
    return _disabled_runtimes_manager


def get_all_runtimes() -> List[Dict[str, str]]:
    """Get list of all runtimes (both available and unavailable).
    
    Returns:
        List of all runtime dicts with 'id', 'label', and 'available' keys
    """
    all_runtimes = [
        {"id": "copilot", "label": "copilot"},
        {"id": "copilot-sdk", "label": "copilot-sdk", "icon": "🤖"},
        {"id": "opencode", "label": "opencode"},
        {"id": "claude", "label": "claude"},
        {"id": "claude-sdk", "label": "claude-sdk", "icon": "🧠"},
        {"id": "gemini", "label": "gemini"},
        {"id": "codex", "label": "codex"},
        {"id": "devin", "label": "devin"},
        {"id": "cursor", "label": "cursor", "icon": "🖱️"},
        {"id": "wee", "label": "wee", "icon": "🍀"},
    ]
    
    # Add availability status
    for rt in all_runtimes:
        rt["available"] = check_runtime_available(rt["id"])
    
    return all_runtimes


def get_available_runtimes() -> List[Dict[str, str]]:
    """Get list of available runtimes on this system.

    Returns:
        List of runtime dicts with 'id' and 'label' keys
    """
    all_runtimes = [
        {"id": "copilot", "label": "copilot"},
        {"id": "copilot-sdk", "label": "copilot-sdk", "icon": "🤖"},
        {"id": "opencode", "label": "opencode"},
        {"id": "claude", "label": "claude"},
        {"id": "claude-sdk", "label": "claude-sdk", "icon": "🧠"},
        {"id": "gemini", "label": "gemini"},
        {"id": "codex", "label": "codex"},
        {"id": "devin", "label": "devin"},
        {"id": "cursor", "label": "cursor", "icon": "🖱️"},
        {"id": "wee", "label": "wee", "icon": "🍀"},
    ]

    available = [rt for rt in all_runtimes if check_runtime_available(rt["id"])]
    
    # Filter out disabled runtimes
    disabled_mgr = get_disabled_runtimes_manager()
    available = [rt for rt in available if not disabled_mgr.is_disabled(rt["id"])]
    
    return available


def get_command_timeout() -> int:
    """Get command execution timeout from environment or use default 300 seconds"""
    try:
        timeout_str = os.environ.get("COMMAND_TIMEOUT", "300")
        timeout = int(timeout_str)
        # Ensure minimum timeout of 30 seconds
        if timeout < 30:
            print(
                f"Warning: COMMAND_TIMEOUT must be at least 30 seconds, using 30",
                file=sys.stderr,
            )
            return 30
        return timeout
    except ValueError:
        print(
            f"Warning: COMMAND_TIMEOUT must be an integer, using default 300 seconds",
            file=sys.stderr,
        )


def get_bg_command_timeout() -> int:
    """Get background task timeout from environment or use default 900 seconds (15 minutes)"""
    try:
        timeout_str = os.environ.get("BG_COMMAND_TIMEOUT", "900")
        timeout = int(timeout_str)
        if timeout < 30:
            return 30
        return timeout
    except ValueError:
        return 900


def estimate_background_timeout(prompt: str, default: int = 900) -> int:
    """Estimate an appropriate timeout for a background task based on the prompt.

    Uses keyword heuristics to assign a timeout that fits the expected task duration:
    - Quick lookups / status checks: 2 min
    - Standard tasks: default (15 min)
    - Long-running: deploy, migrate, process all, index, scan, crawl: 20 min
    - Very long: full backup, batch process large, generate report for all: 30 min
    """
    text = prompt.lower()

    # Very long tasks (~30 min)
    very_long_keywords = [
        "backup",
        "full scan",
        "batch process",
        "process all",
        "index all",
        "crawl all",
        "generate report for all",
        "migrate all",
        "import all",
        "export all",
        "sync all",
        "reindex",
    ]
    # Long tasks (~20 min)
    long_keywords = [
        "deploy",
        "migrate",
        "migration",
        "build",
        "compile",
        "install",
        "process",
        "analyze all",
        "scan",
        "crawl",
        "index",
        "reprocess",
        "refactor",
        "generate report",
        "summarize all",
        "bulk",
    ]
    # Quick tasks (~2 min)
    quick_keywords = [
        "status",
        "check",
        "list",
        "show",
        "get",
        "fetch",
        "ping",
        "is running",
        "are running",
        "what is",
        "what are",
        "tell me",
        "how many",
        "count",
        "current",
        "latest",
        "recent",
    ]

    for kw in very_long_keywords:
        if kw in text:
            return 1800  # 30 min

    for kw in long_keywords:
        if kw in text:
            return 1200  # 20 min

    for kw in quick_keywords:
        if kw in text:
            return 120  # 2 min

    return default


class HistoryManager:
    """Persists per-user chat history in ~/.copilot/chat-history.json."""

    MAX_SESSIONS_PER_USER: int = 100
    MAX_MESSAGES_PER_SESSION: int = 500

    def __init__(self):
        home = os.path.expanduser("~")
        copilot_dir = os.path.join(home, ".copilot")
        os.makedirs(copilot_dir, exist_ok=True)
        self._path = os.path.join(copilot_dir, "chat-history.json")
        self._lock = threading.Lock()
        # Read MAX_SESSIONS from environment or use default
        try:
            HistoryManager.MAX_SESSIONS_PER_USER = int(
                os.environ.get("MAX_SESSIONS", "100")
            )
        except (ValueError, TypeError):
            HistoryManager.MAX_SESSIONS_PER_USER = 100
        if not os.path.exists(self._path):
            self._save({})

    def _user_key(self, channel: str, identity: str) -> str:
        # Strip channel prefix from identity to avoid double-prefixing
        prefix = f"{channel}_"
        if identity.startswith(prefix):
            return identity
        return f"{channel}_{identity}"

    def _load(self) -> dict:
        try:
            with open(self._path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, data: dict) -> None:
        with open(self._path, "w") as f:
            json.dump(data, f, indent=2)

    def get_sessions(self, channel: str, identity: str) -> list:
        """Return session list (no messages) sorted newest-first."""
        with self._lock:
            data = self._load()
            key = self._user_key(channel, identity)
            sessions = data.get(key, {}).get("sessions", [])
            # Return without messages, sorted newest-first
            result = []
            for s in sorted(
                sessions, key=lambda x: x.get("updated_at", 0), reverse=True
            ):
                result.append({k: v for k, v in s.items() if k != "messages"})
            return result

    def get_session_messages(self, channel: str, identity: str, session_id: str):
        """Return messages for a session, or None if not found."""
        with self._lock:
            data = self._load()
            key = self._user_key(channel, identity)
            for s in data.get(key, {}).get("sessions", []):
                if s["session_id"] == session_id:
                    return s.get("messages", [])
        return None

    def create_session(
        self, channel: str, identity: str, session_id: str, agent: str = ""
    ) -> dict:
        """Create a new session entry, pruning oldest if over cap."""
        with self._lock:
            data = self._load()
            key = self._user_key(channel, identity)
            user_data = data.setdefault(key, {"sessions": []})
            sessions = user_data["sessions"]
            # Prune if at cap
            if len(sessions) >= self.MAX_SESSIONS_PER_USER:
                sessions.sort(key=lambda x: x.get("updated_at", 0))
                sessions = sessions[-(self.MAX_SESSIONS_PER_USER - 1) :]
                user_data["sessions"] = sessions
            now = time.time()
            session = {
                "session_id": session_id,
                "title": "",
                "preview": "",
                "agent": agent or "",
                "created_at": now,
                "updated_at": now,
                "messages": [],
            }
            sessions.append(session)
            self._save(data)
            return session

    def append_message(
        self,
        channel: str,
        identity: str,
        session_id: str,
        role: str,
        content: str,
        files=None,
    ) -> bool:
        """Append a message to a session. Returns False if session not found."""
        with self._lock:
            data = self._load()
            key = self._user_key(channel, identity)
            for s in data.get(key, {}).get("sessions", []):
                if s["session_id"] == session_id:
                    msg: dict = {
                        "role": role,
                        "content": content,
                        "timestamp": time.time(),
                    }
                    if files:
                        msg["files"] = files
                    messages = s.setdefault("messages", [])
                    messages.append(msg)
                    # Auto-set title from first user message
                    if role == "user" and not s.get("title"):
                        s["title"] = content[:60]
                        s["title_source"] = "auto"
                    # Auto-set preview from first assistant message
                    if role == "assistant" and not s.get("preview"):
                        s["preview"] = content[:120]
                    # Prune if too many messages
                    if len(messages) > self.MAX_MESSAGES_PER_SESSION:
                        s["messages"] = messages[-self.MAX_MESSAGES_PER_SESSION :]
                    s["updated_at"] = time.time()
                    self._save(data)
                    return True
        return False

    def rename_session(
        self, channel: str, identity: str, session_id: str, title: str
    ) -> bool:
        """Rename a session. Returns False if not found."""
        with self._lock:
            data = self._load()
            key = self._user_key(channel, identity)
            for s in data.get(key, {}).get("sessions", []):
                if s["session_id"] == session_id:
                    s["title"] = title[:120]
                    s["title_source"] = "user"
                    s["updated_at"] = time.time()
                    self._save(data)
                    return True
        return False

    def update_title_llm(
        self,
        channel: str,
        identity: str,
        session_id: str,
        title: str,
        source: str = "llm",
    ) -> bool:
        """Update session title from auto-generation. Won't overwrite user-set titles."""
        with self._lock:
            data = self._load()
            key = self._user_key(channel, identity)
            for s in data.get(key, {}).get("sessions", []):
                if s["session_id"] == session_id:
                    if s.get("title_source") == "user":
                        return False
                    s["title"] = title[:120]
                    s["title_source"] = source
                    s["title_generated_at"] = time.time()
                    s["message_count_at_title_gen"] = len(s.get("messages", []))
                    s["updated_at"] = time.time()
                    self._save(data)
                    return True
        return False

    def get_session_for_title_check(
        self, channel: str, identity: str, session_id: str
    ) -> Optional[dict]:
        """Get session data needed to decide if title generation is needed."""
        with self._lock:
            data = self._load()
            key = self._user_key(channel, identity)
            for s in data.get(key, {}).get("sessions", []):
                if s["session_id"] == session_id:
                    return {
                        "title": s.get("title", ""),
                        "title_source": s.get("title_source", "auto"),
                        "message_count": len(s.get("messages", [])),
                        "message_count_at_title_gen": s.get(
                            "message_count_at_title_gen", 0
                        ),
                        "messages": s.get("messages", [])[-20:],
                    }
        return None

    def delete_session(self, channel: str, identity: str, session_id: str) -> bool:
        """Delete a session. Returns False if not found."""
        with self._lock:
            data = self._load()
            key = self._user_key(channel, identity)
            sessions = data.get(key, {}).get("sessions", [])
            new_sessions = [s for s in sessions if s["session_id"] != session_id]
            if len(new_sessions) == len(sessions):
                return False
            data[key]["sessions"] = new_sessions
            self._save(data)
            return True

    def update_session_agent(
        self, channel: str, identity: str, session_id: str, agent: str
    ) -> bool:
        """Update the agent associated with a session."""
        with self._lock:
            data = self._load()
            key = self._user_key(channel, identity)
            for s in data.get(key, {}).get("sessions", []):
                if s["session_id"] == session_id:
                    s["agent"] = agent or ""
                    s["updated_at"] = time.time()
                    self._save(data)
                    return True
        return False


class RuntimeUsageTracker:
    """Queries GitHub Copilot premium request usage from the billing API."""

    COPILOT_PLAN_QUOTAS = {
        "free": 50,
        "pro": 300,
        "pro+": 1500,
        "business": 300,
        "enterprise": 1000,
    }

    def __init__(self):
        self._cache: Dict[str, dict] = {}
        self._cache_ttl = 120  # seconds

    @staticmethod
    def _month_key() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y-%m")

    @staticmethod
    def _next_reset() -> str:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        if now.month == 12:
            reset = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            reset = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
        return reset.isoformat()

    def _fetch_copilot_usage(self) -> dict:
        """Query GitHub billing API for Copilot premium request usage this month."""
        cache_key = f"copilot:{self._month_key()}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and now - cached["ts"] < self._cache_ttl:
            return cached["data"]

        try:
            gh_bin = shutil.which("gh")
            if not gh_bin:
                return {}
            user_raw = subprocess.run(
                [gh_bin, "api", "/user", "--jq", ".login"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            gh_user = user_raw.stdout.strip()
            if not gh_user:
                return {}

            billing_raw = subprocess.run(
                [gh_bin, "api", f"/users/{gh_user}/settings/billing/usage"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if billing_raw.returncode != 0:
                return {}

            billing = json.loads(billing_raw.stdout)
            month = self._month_key()

            premium = 0.0
            coding_agent = 0.0
            for item in billing.get("usageItems", []):
                if item.get("product") != "copilot":
                    continue
                if not item.get("date", "").startswith(month):
                    continue
                sku = item.get("sku", "")
                qty = item.get("quantity", 0)
                if "Coding Agent" in sku:
                    coding_agent += qty
                elif "Premium Request" in sku:
                    premium += qty

            data = {
                "premium_requests": round(premium, 1),
                "coding_agent_requests": round(coding_agent, 1),
                "total": round(premium + coding_agent, 1),
            }
            self._cache[cache_key] = {"ts": now, "data": data}
            return data
        except Exception as exc:
            print(
                f"[RuntimeUsage] Copilot billing fetch failed: {exc}", file=sys.stderr
            )
            return {}

    def _get_copilot_quota(self) -> int:
        env_val = os.environ.get("RUNTIME_QUOTA_COPILOT")
        if env_val and env_val.isdigit():
            return int(env_val)
        plan = os.environ.get("COPILOT_PLAN", "pro").lower()
        return self.COPILOT_PLAN_QUOTAS.get(plan, 300)

    def get_usage(self) -> dict:
        """Return Copilot premium request usage from GitHub billing."""
        month = self._month_key()
        reset_date = self._next_reset()
        quota = self._get_copilot_quota()

        billing = self._fetch_copilot_usage()
        if billing:
            used = billing["total"]
            return {
                "runtime": "copilot",
                "requests_used": used,
                "quota_limit": quota,
                "requests_remaining": max(0, quota - used),
                "reset_date": reset_date,
                "period": month,
                "source": "github_billing",
                "breakdown": {
                    "premium_requests": billing["premium_requests"],
                    "coding_agent_requests": billing["coding_agent_requests"],
                },
            }
        return {
            "runtime": "copilot",
            "requests_used": 0,
            "quota_limit": quota,
            "requests_remaining": quota,
            "reset_date": reset_date,
            "period": month,
            "source": "unavailable",
        }


class SessionManager:
    """Manages AI CLI sessions (Copilot & OpenCode) for N8N integration"""

    # Query tracking constants
    MAX_PROMPT_LENGTH = 200  # Maximum chars to store from prompt
    MAX_OUTPUT_LENGTH = 500  # Maximum chars to store from output
    MAX_OUTPUT_DISPLAY = 300  # Maximum chars to display in status output
    _WEE_DEFAULT_CONTEXT_LIMIT = 4096

    # Model configurations
    # Note: Claude Code CLI does not support dynamic model listing via flag.
    # We use CLI aliases (sonnet, haiku, opus) as primary IDs to let the CLI resolve to the latest versions.
    CLAUDE_MODELS = {
        "Anthropic Models": [
            (
                "sonnet",
                "Claude Sonnet (Latest)",
                [
                    "claude-sonnet",
                    "claude-sonnet-4-6",
                    "claude-sonnet-4.6",
                    "claude-sonnet-4.5",
                    "sonnet-4.6",
                ],
            ),
            (
                "haiku",
                "Claude Haiku (Latest)",
                ["claude-haiku", "claude-haiku-4-5", "claude-haiku-4.5", "haiku-4.5"],
            ),
            (
                "opus",
                "Claude Opus (Latest)",
                ["claude-opus", "claude-opus-4-6", "claude-opus-4.6", "opus-4.6"],
            ),
        ],
        "US Frontier Models (Comparison)": [
            (
                "claude-3-5-sonnet-latest",
                "Claude 3.5 Sonnet (V2)",
                ["claude-3-5-sonnet-20241022"],
            ),
            (
                "claude-3-5-haiku-latest",
                "Claude 3.5 Haiku",
                ["claude-3-5-haiku-20241022"],
            ),
            ("claude-3-opus-latest", "Claude 3 Opus", ["claude-3-opus-20240229"]),
        ],
    }

    OPENCODE_MODELS = {
        "openai-compatible": [
            (
                "openai-compatible/mistral-7b-instruct-v0.1",
                "Mistral 7B Instruct v0.1",
                ["mistral-7b"],
            ),
        ],
        "Meta (US Models)": [
            ("llama-3.3-70b-versatile", "Llama 3.3 70B", ["llama-3.3", "llama-3-70b"]),
            ("llama-3.1-405b", "Llama 3.1 405B", ["llama-405b"]),
            ("llama-3.2-90b-vision", "Llama 3.2 90B Vision", ["llama-90b-vision"]),
        ],
        "xAI (US Models)": [
            ("grok-2", "Grok-2", ["grok"]),
            ("grok-2-mini", "Grok-2 Mini", ["grok-mini"]),
        ],
    }

    # Gemini models configuration
    # Note: These are common Gemini models; the CLI may support additional models
    GEMINI_MODELS = {
        "Google Models": [
            (
                "gemini-3-pro-preview",
                "Gemini 3 Pro (Preview)",
                ["gemini-3-pro", "pro-3"],
            ),
            (
                "gemini-3-flash-preview",
                "Gemini 3 Flash (Preview)",
                ["gemini-3-flash", "flash-3"],
            ),
            (
                "gemini-2.5-pro",
                "Gemini 2.5 Pro",
                ["gemini-pro-2.5", "pro-2.5"],
            ),
            (
                "gemini-2.5-flash",
                "Gemini 2.5 Flash",
                ["gemini-flash-2.5", "flash-2.5"],
            ),
            (
                "gemini-2.5-flash-lite",
                "Gemini 2.5 Flash Lite",
                ["gemini-flash-lite-2.5", "flash-lite-2.5"],
            ),
            (
                "gemini-2.0-flash-exp",
                "Gemini 2.0 Flash (Experimental)",
                ["gemini-2.0-flash", "flash-2.0"],
            ),
            ("gemini-1.5-pro", "Gemini 1.5 Pro", ["gemini-pro-1.5", "pro-1.5"]),
            ("gemini-1.5-flash", "Gemini 1.5 Flash", ["gemini-flash-1.5", "flash-1.5"]),
            ("gemini-pro", "Gemini Pro", ["gemini-1.0-pro"]),
        ],
        "US Frontier Models (Comparison)": [
            ("gemini-1.5-pro-latest", "Gemini 1.5 Pro", ["gemini-1.5-pro"]),
            ("gemini-1.5-flash-latest", "Gemini 1.5 Flash", ["gemini-1.5-flash"]),
            ("gemini-2.0-flash-001", "Gemini 2.0 Flash", ["gemini-2.0-flash"]),
        ],
    }

    # CODEX models configuration (from copilot CLI --model choices)
    CODEX_MODELS = {
        "OpenAI Models": [
            ("gpt-5.5", "GPT-5.5", ["gpt-5.5"]),
            ("gpt-5.4", "GPT-5.4", ["gpt-5.4", "gpt-5.4-pro"]),
            ("gpt-5.4-mini", "GPT-5.4 Mini", ["gpt-5.4-mini"]),
            ("gpt-5.3-codex", "GPT-5.3 Codex", ["gpt-5.3", "codex-latest"]),
            ("gpt-5.2-codex", "GPT-5.2 Codex", ["gpt-5.2-codex"]),
            ("gpt-5.2", "GPT-5.2", ["gpt-5.2"]),
            ("gpt-5.1-codex-max", "GPT-5.1 Codex Max", ["gpt-5.1", "codex-max"]),
            ("gpt-5.1-codex", "GPT-5.1 Codex", ["codex"]),
            ("gpt-5.1", "GPT-5.1", []),
            ("gpt-5.1-codex-mini", "GPT-5.1 Codex Mini", ["codex-mini"]),
            ("gpt-5-mini", "GPT-5 Mini", ["gpt-5", "mini"]),
            ("gpt-4.1", "GPT-4.1", ["gpt-4"]),
        ],
        "US Frontier Models (Comparison)": [
            ("gpt-4o", "GPT-4o (Omni)", ["gpt-4o-latest"]),
            ("gpt-4o-mini", "GPT-4o Mini", ["gpt-4o-mini-latest"]),
            ("gpt-4-turbo", "GPT-4 Turbo", ["gpt-4-turbo-latest"]),
            ("o1-preview", "OpenAI o1-preview", ["o1-preview-2024-09-12"]),
            ("o1-mini", "OpenAI o1-mini", ["o1-mini-2024-09-12"]),
        ],
    }

    # DEVIN models configuration
    DEVIN_MODELS = {
        "Anthropic Models": [
            ("claude-sonnet-4", "Claude Sonnet 4", ["sonnet-4", "sonnet"]),
            ("claude-sonnet-4.5", "Claude Sonnet 4.5", ["sonnet-4.5"]),
            (
                "claude-sonnet-4.5-thinking",
                "Claude Sonnet 4.5 Thinking",
                ["sonnet-thinking"],
            ),
            ("claude-sonnet-4.6", "Claude Sonnet 4.6", ["sonnet-4.6"]),
            ("claude-opus-4.7", "Claude Opus 4.7", ["opus-4.7"]),
            ("claude-opus-4.5", "Claude Opus 4.5", ["opus-4.5"]),
            ("claude-opus-4.6", "Claude Opus 4.6", ["opus-4.6", "opus"]),
            ("claude-haiku-4.5", "Claude Haiku 4.5", ["haiku-4.5", "haiku"]),
        ],
        "Google Models": [
            ("gemini-3-flash", "Gemini 3 Flash", ["gemini-flash"]),
            ("gemini-3-pro", "Gemini 3 Pro", ["gemini-pro"]),
            ("gemini-3.1-pro", "Gemini 3.1 Pro", ["gemini-3.1"]),
        ],
        "OpenAI Models": [
            ("gpt-5.2", "GPT-5.2", []),
            ("gpt-5.3-codex", "GPT-5.3 Codex", ["codex"]),
            ("gpt-5.4", "GPT-5.4", []),
        ],
        "Other Models": [
            ("phoenix-alpha", "Phoenix Alpha", ["phoenix"]),
            ("swe-1.5", "SWE-1.5", ["swe"]),
            ("swe-1.5-fast", "SWE-1.5 Fast", ["swe-fast"]),
        ],
    }

    CURSOR_MODELS = {
        "Composer Models": [
            ("auto", "Auto (Recommended)", ["auto-select"]),
            ("composer-2-fast", "Composer 2 Fast", ["comp-2-fast"]),
            ("composer-2", "Composer 2", ["comp-2"]),
            ("composer-1.5", "Composer 1.5", ["comp-1.5"]),
        ],
        "GPT Models": [
            ("gpt-5.3-codex", "GPT-5.3 Codex", ["codex-5.3"]),
            ("gpt-5.3-codex-fast", "GPT-5.3 Codex Fast", []),
            ("gpt-5.3-codex-high", "GPT-5.3 Codex High", []),
            ("gpt-5.2", "GPT-5.2", []),
            ("gpt-5.2-codex", "GPT-5.2 Codex", ["codex-5.2"]),
            ("gpt-5.2-codex-fast", "GPT-5.2 Codex Fast", []),
            ("gpt-5.2-codex-high", "GPT-5.2 Codex High", []),
            ("gpt-5.1", "GPT-5.1", []),
            ("gpt-5.1-codex-max-low", "GPT-5.1 Codex Max Low", []),
            ("gpt-5.4-mini-medium", "GPT-5.4 Mini Medium", ["gpt-mini"]),
            ("gpt-5.4-nano-medium", "GPT-5.4 Nano Medium", ["gpt-nano"]),
            ("gpt-5-mini", "GPT-5 Mini", []),
        ],
        "Claude Models": [
            ("claude-4.5-sonnet", "Claude 4.5 Sonnet", ["sonnet-4.5"]),
            ("claude-4.5-sonnet-thinking", "Claude 4.5 Sonnet Thinking", []),
            ("claude-4-sonnet", "Claude 4 Sonnet", ["sonnet-4"]),
            ("claude-4-sonnet-1m", "Claude 4 Sonnet 1M", []),
            ("claude-4-sonnet-thinking", "Claude 4 Sonnet Thinking", []),
            ("claude-4-sonnet-1m-thinking", "Claude 4 Sonnet 1M Thinking", []),
        ],
        "Google Models": [
            ("gemini-3.1-pro", "Gemini 3.1 Pro", ["gemini-pro"]),
            ("gemini-3-flash", "Gemini 3 Flash", ["gemini-flash"]),
        ],
        "Other Models": [
            ("grok-4-20", "Grok 4-20", ["grok"]),
            ("grok-4-20-thinking", "Grok 4-20 Thinking", []),
            ("kimi-k2.5", "Kimi K2.5", ["kimi"]),
        ],
    }

    def __init__(self, config_file: Optional[str] = None, app_env: str = "PROD"):
        # Copilot Paths
        self.copilot_home = Path.home() / ".copilot"
        # Dev and prod instances MUST use separate session map files to
        # prevent save_session_map() in one instance from overwriting the
        # other's sessions (root cause of "Stream request failed: HTTP 404").
        _env_suffix = "-dev" if app_env == "DEV" else ""
        self.session_map_file = self.copilot_home / f"n8n-session-map{_env_suffix}.json"
        self.session_state_dir = self.copilot_home / "session-state"
        self.logs_dir = self.copilot_home / "logs"
        self.running_queries_file = (
            self.copilot_home / f"running-queries{_env_suffix}.json"
        )

        # OpenCode Paths
        self.opencode_home = Path.home() / ".opencode"
        # Resolve OpenCode executable like other runtimes; keep legacy path as fallback.
        self.opencode_bin = Path(
            find_executable("opencode") or str(self.opencode_home / "bin" / "opencode")
        )
        self.opencode_session_storage = (
            Path.home()
            / ".local"
            / "share"
            / "opencode"
            / "storage"
            / "session"
            / "global"
        )

        # Claude Paths
        self.claude_home = Path.home() / ".claude"
        self.claude_debug_dir = self.claude_home / "debug"

        # Gemini Paths
        self.gemini_home = Path.home() / ".gemini"
        self.gemini_session_dir = self.gemini_home / "sessions"

        # CODEX Paths
        self.codex_home = Path.home() / ".codex"
        self.codex_session_dir = self.codex_home / "sessions"

        # Devin Paths
        self.devin_home = Path.home() / ".devin"
        self.devin_session_dir = self.devin_home / "sessions"

        # Executable paths (resolved dynamically)
        self.copilot_bin = find_executable("copilot")
        self.claude_bin = find_executable("claude")
        self.devin_bin = find_executable("devin")
        self.cursor_bin = find_executable("agent")

        # Cursor Paths
        self.cursor_home = Path.home() / ".cursor-agent"
        self.cursor_session_dir = self.cursor_home / "sessions"

        # CLI mode setting (elevated, restricted, or sandboxed)
        self.mode = None

        # Ensure directories exist
        self.copilot_home.mkdir(exist_ok=True)
        self.session_state_dir.mkdir(exist_ok=True)
        self.logs_dir.mkdir(exist_ok=True)
        self.opencode_home.mkdir(exist_ok=True)
        self.claude_home.mkdir(exist_ok=True)
        self.gemini_home.mkdir(exist_ok=True)
        self.gemini_session_dir.mkdir(exist_ok=True)
        self.codex_home.mkdir(exist_ok=True)
        self.codex_session_dir.mkdir(exist_ok=True)
        self.devin_home.mkdir(exist_ok=True)
        self.devin_session_dir.mkdir(exist_ok=True)
        self.cursor_home.mkdir(exist_ok=True)
        self.cursor_session_dir.mkdir(exist_ok=True)

        # Load agents from config file (also sets _agents_config_path and _agents_json_mtime)
        self._agents_config_path: Optional[Path] = None
        self._agents_json_mtime: float = 0.0
        self.AGENTS = self._load_agents_config(config_file)

        # Load skill repositories from configuration
        self.skill_repositories = self._load_skill_repositories()

        # Cache env-loaded model configurations for runtime description lookup
        self._env_claude_models = None
        self._env_gemini_models = None
        self._env_codex_models = None
        self._env_devin_models = None
        self._env_cursor_models = None
        self._env_wee_models = None  # Cache for dynamically-discovered wee models
        self._openrouter_cache_ts = 0  # TTL timestamp for OpenRouter discovery cache
        self._ollama_models_cache: list = []  # Live-discovered Ollama model names
        self._ollama_cache_ts: float = 0  # TTL timestamp for Ollama discovery (60s)

        # Load command timeout from environment
        self.command_timeout = get_command_timeout()

        # Session idle timeout — sessions inactive longer than this are
        # candidates for cleanup.  Defaults to 30 min; override via env var.
        self.session_idle_timeout = int(os.environ.get("SESSION_IDLE_TIMEOUT", "1800"))

        # Lock for session map file read-modify-write to prevent TOCTOU races
        self._session_map_lock = threading.Lock()

        # Per-session streaming queues: session_id -> (asyncio.Queue, event_loop)
        # Populated by the /stream API endpoint; read by _execute_subprocess_with_tracking.
        self._stream_queues: Dict[str, tuple] = {}

        # Per-session stream buffers for multi-session streaming support.
        # Buffers all chunks so disconnected clients can reconnect and replay.
        # session_id -> _StreamBuffer
        self._stream_buffers: Dict[str, "_StreamBuffer"] = {}  # noqa: F821

        # Last subprocess exit code per n8n_session_id (for debugging/monitoring)
        self._last_exit_codes: Dict[str, int] = {}

        # Copilot session start times for proactive token refresh (issue #190).
        # Maps n8n_session_id -> float (epoch seconds when session was started).
        # Copilot session tokens expire ~30 min after creation; we restart at 25 min.
        self._copilot_session_start: Dict[str, float] = {}

        # Per-session live status for mobile channel progress updates (F004).
        # Maps n8n_session_id -> {"text": str, "updated_at": float}
        self._live_status: Dict[str, Dict] = {}
        self._live_status_lock = threading.Lock()

        # Slash command registry (F020): maps command -> {handler, description}
        # Commands with a handler callable bypass the LLM entirely.
        # Commands with handler=None are handled by the legacy if/elif chain.
        self._slash_command_registry: Dict[str, dict] = {}
        self._init_slash_commands()

    # ── Live status helpers for mobile channel progress (F004) ──────────

    def set_live_status(self, n8n_session_id: str, text: str) -> None:
        """Store a live status update for a session (thread-safe)."""
        with self._live_status_lock:
            self._live_status[n8n_session_id] = {
                "text": text,
                "updated_at": time.time(),
            }

    def get_live_status(self, n8n_session_id: str) -> Optional[Dict]:
        """Return the latest live status for a session, or None."""
        with self._live_status_lock:
            return self._live_status.get(n8n_session_id)

    def clear_live_status(self, n8n_session_id: str) -> None:
        """Remove live status for a session (call when execution finishes)."""
        with self._live_status_lock:
            self._live_status.pop(n8n_session_id, None)

    # ── Slash command registry (F020) ───────────────────────────────────

    def _register_slash(self, command: str, handler, description: str):
        """Register a slash command in the registry."""
        self._slash_command_registry[command] = {
            "handler": handler,
            "description": description,
        }

    def _init_slash_commands(self):
        """Initialize the slash command registry.

        Every command here is handled entirely server-side.
        Commands with a handler bypass the LLM, reducing
        latency and token cost.  To add a new pure-server
        command, create a ``_slash_<name>`` method and
        register it below.
        """
        self._register_slash("/help", self._slash_help, "Show available commands")
        self._register_slash(
            "/status", self._slash_status, "Check status of running query"
        )
        self._register_slash("/cancel", self._slash_cancel, "Cancel running query")
        self._register_slash(
            "/capabilities", self._slash_capabilities, "Show agent capabilities"
        )
        self._register_slash(
            "/runtime", self._slash_runtime, "Manage runtime (list/set/current)"
        )
        self._register_slash(
            "/agent", self._slash_agent, "Manage agent (list/set/current/invoke)"
        )
        self._register_slash(
            "/model", self._slash_model, "Manage model (list/set/current)"
        )
        self._register_slash(
            "/session", self._slash_session, "Manage session (list/reset/info)"
        )
        self._register_slash(
            "/timeout", self._slash_timeout, "Get/set execution timeout"
        )
        self._register_slash(
            "/render", self._slash_render, "Get/set output render format"
        )
        self._register_slash(
            "/notifications",
            self._slash_notifications,
            "Toggle background notifications",
        )
        self._register_slash(
            "/silent", self._slash_silent, "Toggle silent mode (hide tool calls)"
        )
        self._register_slash("/verbose", self._slash_verbose, "Toggle verbose mode")
        self._register_slash("/mode", self._slash_mode, "Set permission mode")
        self._register_slash("/schedule", self._slash_schedule, "Manage scheduled jobs")
        self._register_slash(
            "/background", self._slash_background, "Manage background tasks"
        )
        self._register_slash("/update", self._slash_update, "Pull latest and restart")
        self._register_slash("/upgrade", self._slash_update, "Pull latest and restart")
        self._register_slash("/pull", self._slash_update, "Pull latest and restart")
        self._register_slash(
            "/secret",
            self._slash_secret,
            "Manage secrets (set/delete/list)",
        )

    def get_slash_commands(self) -> Dict[str, str]:
        """Return a dict of all registered slash commands and descriptions."""
        return {
            cmd: entry["description"]
            for cmd, entry in self._slash_command_registry.items()
        }

    def _slash_secret(self, argument, session_data, n8n_session_id):
        """Handle /secret slash command. Values never touch the LLM."""
        if not argument:
            return (
                "\U0001f510 **Secret Commands**\n\n"
                "\u2022 `/secret list` \u2014 List stored secret names\n"
                "\u2022 `/secret set <name> <value>` \u2014 Store a secret\n"
                "\u2022 `/secret delete <name>` \u2014 Delete a secret\n\n"
                "Values are stored securely and never sent to the LLM."
            )

        sub = argument.strip()
        sub_lower = sub.lower()
        secret_tool = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "secret_tool",
            "secret_tool.py",
        )

        if sub_lower == "list":
            return self._slash_secret_list(secret_tool)
        elif sub_lower.startswith("set "):
            return self._slash_secret_set(sub[4:].strip(), secret_tool)
        elif sub_lower.startswith("delete "):
            return self._slash_secret_delete(sub[7:].strip(), secret_tool)
        else:
            return (
                "\U0001f510 **Secret Commands**\n\n"
                "\u2022 `/secret list` \u2014 List stored secret names\n"
                "\u2022 `/secret set <name> <value>` \u2014 Store a secret\n"
                "\u2022 `/secret delete <name>` \u2014 Delete a secret"
            )

    def _slash_secret_list(self, secret_tool: str) -> str:
        """List stored secret names via secret_tool.py."""
        try:
            proc = subprocess.run(
                [sys.executable, secret_tool, "list", "--backend", "file"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0:
                detail = proc.stdout.strip() or proc.stderr.strip()
                return f"\u274c {detail or 'Failed to list secrets'}"
            output = proc.stdout.strip()
            if not output:
                return "\U0001f510 **Secrets:** (none)"
            try:
                data = json.loads(output)
                names = data if isinstance(data, list) else data.get("names", [])
            except json.JSONDecodeError:
                names = [ln.strip() for ln in output.splitlines() if ln.strip()]
            if not names:
                return "\U0001f510 **Secrets:** (none)"
            lines = ["\U0001f510 **Stored Secrets:**\n"]
            for n in sorted(names):
                lines.append(f"\u2022 `{n}`")
            return "\n".join(lines)
        except Exception as e:
            return f"\u274c Error listing secrets: {e}"

    def _slash_secret_set(self, args: str, secret_tool: str) -> str:
        """Store a secret via secret_tool.py. Value never touches the LLM."""
        parts = args.split(None, 1)
        if len(parts) < 2:
            return "Usage: `/secret set <name> <value>`"
        name, value = parts
        if not re.match(r"^[A-Za-z0-9._-]+$", name):
            return (
                "\u274c Invalid name. "
                "Use letters, digits, hyphens, underscores, dots only."
            )
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    secret_tool,
                    "set",
                    "--name",
                    name,
                    "--value-stdin",
                    "--backend",
                    "file",
                ],
                input=f"{value}\n",
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0:
                detail = proc.stdout.strip() or proc.stderr.strip()
                return f"\u274c {detail or 'Failed to store secret'}"
            result = json.loads(proc.stdout)
            action = result.get("action", "stored")
            return f"\u2713 Secret `{name}` {action}."
        except Exception as e:
            return f"\u274c Error storing secret: {e}"

    def _slash_secret_delete(self, name: str, secret_tool: str) -> str:
        """Delete a secret via secret_tool.py."""
        if not name:
            return "Usage: `/secret delete <name>`"
        if not re.match(r"^[A-Za-z0-9._-]+$", name):
            return (
                "\u274c Invalid name. "
                "Use letters, digits, hyphens, underscores, dots only."
            )
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    secret_tool,
                    "delete",
                    "--name",
                    name,
                    "--backend",
                    "file",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0:
                detail = proc.stdout.strip() or proc.stderr.strip()
                return f"\u274c {detail or 'Secret not found'}"
            return f"\u2713 Secret `{name}` deleted."
        except Exception as e:
            return f"\u274c Error deleting secret: {e}"

    # ── Slash command handlers (F020) ───────────────────────────────

    def _slash_help(self, argument, session_data, n8n_session_id):
        """Handle /help slash command."""
        return """🆘 **Available Commands**

**Orchestrator:**
   • /capabilities - Show what the orchestrator can help with

**Bash Commands:**
   • !command - Execute bash command directly (e.g., !pwd, !ls -la)
   • Commands run in current agent's directory with 10s timeout

**Runtime Management:**
   • /runtime list - Show available runtimes
   • /runtime set (copilot|copilot-sdk|opencode|claude|claude-sdk|gemini|codex|devin|cursor|wee) - Switch runtime
   • /runtime current - Show current runtime

**Model Management:**
   • /model list - Show available models for current runtime
   • /model set "model_name" - Switch model
   • /model current - Show current model

**Mode Management:**
   • /mode current - Show current mode
   • /mode elevated - Full access, auto-approve (no prompts)
   • /mode restricted - Bounded access (default)
   /mode sandboxed - Read-only, no external access
   • /mode list - Show available modes

**Agent Management:**
   • /agent list - Show all available agents and their locations
   • /agent set <agent_name> — switch to an agent and work with it.
   • /agent current - Show current agent
   • /agent invoke "agent_name" "prompt" - Delegate to sub-agent

**Session:**
   • /session reset - Reset session (preserves model, runtime, agent)
   • /timeout or /timeout current - Show current timeout
   • /timeout set [seconds] - Set timeout (30-3600 seconds / 1 hour max)
   • /render or /render current - Show current render type
   • /render set [text|markdown|html|telegram_html] - Set render type

**Query Management:**
   • /status - Check status of running query for this session
   • /cancel - Cancel running query for this session

**Scheduler:**
   • /schedule list - List all scheduled jobs
   • /schedule status - Scheduler health status
   • /schedule add <name> | <schedule> | <task> - Create a new job
   • /schedule info <job_id> - Show job details
   • /schedule pause <job_id> - Pause a job
   • /schedule resume <job_id> - Resume a paused job
   • /schedule delete <job_id> - Delete a job
   • /schedule logs <job_id> - View job logs
   • /schedule results <job_id> - View execution results

**Background Tasks:**
   • /background <prompt> - Run a task in the background
   • /background agent=<name> <prompt> - Override agent
   • /background list - List your background tasks
   • /background status <task_id> - Check task status
   • /background kill <task_id> - Kill a running task

**Secrets:**
   • /secret list - List stored secret names
   • /secret set <name> <value> - Store a secret (value never sent to LLM)
   • /secret delete <name> - Delete a secret

**System:**
   • /update - Pull latest dev code and restart all dev services
   • /update status - Show last update log

**Auto-Delegation:**
You can mention an agent in your prompt and it will auto-delegate:
   • ask the family agent for Parkers Christmas ideas
   • have the devops agent check production status
   • this is in the projects agent, find the auth code

**Examples:**
   /capabilities
   !pwd
   !echo "Hello World"
   !ls -la
   /mode elevated
   /runtime set gemini
   /model set "gpt-5.2"
   /agent set "family"
   /agent invoke family "Find Christmas ideas for Parker"
   ask the family agent what are Parkers Christmas ideas
   have the devops agent check the server status
"""

    def _slash_status(self, argument, session_data, n8n_session_id):
        """Handle /status slash command."""
        # Check if there's a running query for this session
        query_info = self.get_running_query(n8n_session_id)

        if not query_info:
            return "✓ No running query for this session"

        # Check if process is still running
        pid = query_info["pid"]
        if not self.is_process_running(pid):
            # Process finished but tracking wasn't cleaned up
            self.clear_running_query(n8n_session_id)
            return "✓ No running query for this session (last query has completed)"

        # Process is running - show status
        runtime = query_info.get("runtime", "unknown")
        agent = query_info.get("agent", "unknown")
        prompt_snippet = query_info.get("prompt", "")[:100]
        start_time = query_info.get("start_time", 0)
        elapsed = int(time.time() - start_time)
        elapsed_min = elapsed // 60
        elapsed_sec = elapsed % 60
        last_output = query_info.get("last_output", "")

        status_msg = f"""🔄 **Query Running**

**Runtime:** {runtime}
**Agent:** {agent}
**PID:** {pid}
**Elapsed Time:** {elapsed_min}m {elapsed_sec}s
**Prompt:** {prompt_snippet}...

**Recent Output:**
{last_output[-self.MAX_OUTPUT_DISPLAY :] if last_output else "(no output yet)"}
"""
        return status_msg

    def _slash_cancel(self, argument, session_data, n8n_session_id):
        """Handle /cancel slash command."""
        # Find and cancel running query
        query_info = self.get_running_query(n8n_session_id)

        if not query_info:
            return "❌ No running query to cancel for this session"

        pid = query_info["pid"]

        # Check if process is still running
        if not self.is_process_running(pid):
            self.clear_running_query(n8n_session_id)
            return "✓ No running query to cancel (query has already completed)"

        # Kill the process
        if self.kill_process(pid):
            self.clear_running_query(n8n_session_id)
            runtime = query_info.get("runtime", "unknown")
            return f"✓ Cancelled running query (PID: {pid}, Runtime: {runtime})"
        else:
            return f"❌ Failed to cancel query (PID: {pid}). Process may have already terminated."

    def _slash_capabilities(self, argument, session_data, n8n_session_id):
        """Handle /capabilities slash command."""
        return self.get_capabilities()

    def _slash_runtime(self, argument, session_data, n8n_session_id):
        """Handle /runtime slash command."""
        current_runtime = session_data.get("runtime", "copilot")
        if not argument:
            return "Usage: /runtime [list|set|current]"
        if argument == "list":
            return (
                "🤖 **Available Runtimes**\n\n"
                "• `copilot` (GitHub Copilot CLI)\n"
                "• `copilot-sdk` (GitHub Copilot SDK — native Python, streaming)\n"
                "• `opencode` (OpenCode CLI)\n"
                "• `claude` (Claude Code CLI)\n"
                "• `gemini` (Google Gemini CLI)\n"
                "• `codex` (Codex CLI)\n"
                "• `devin` (Devin CLI)\n"
                "• `cursor` (Cursor Agent CLI)\n"
                "• `claude-sdk` (Claude Agent SDK — native Python, in-process tools)\n"
                "• `wee` (Wee Native — OpenAI-compatible API: Ollama, OpenRouter, LM Studio)"
            )
        elif argument == "current":
            return f"🤖 **Current Runtime:** `{current_runtime}`"
        elif argument.startswith("set "):
            new_runtime = argument[4:].strip().lower()
            if new_runtime not in [
                "copilot",
                "copilot-sdk",
                "opencode",
                "claude",
                "claude-sdk",
                "gemini",
                "codex",
                "devin",
                "cursor",
                "wee",
            ]:
                return (
                    f"Unknown runtime: '{new_runtime}'. Use "
                    "copilot, copilot-sdk, opencode, claude, claude-sdk, "
                    "gemini, codex, devin, cursor, or wee."
                )

            # Capture previous session state before any updates
            prev_runtime = current_runtime
            prev_session_id = session_data.get("session_id")

            # Generate the new session ID up front so the handoff can reference it
            new_session_id = str(uuid4())

            # Prepare session handoff if the runtime is actually changing and
            # there is prior history to hand off
            if prev_runtime != new_runtime and prev_session_id:
                try:
                    from session_handoff import SessionHandoff, _handoff_logger

                    handoff = SessionHandoff()

                    # Log the reason for handoff (user command: /runtime set)
                    _handoff_logger.info(
                        f"HANDOFF REASON: User executed '/runtime set {new_runtime}' command | "
                        f"n8n_session={n8n_session_id} | "
                        f"current_agent={session_data.get('agent', 'unknown')}"
                    )

                    handoff.export_transcript(n8n_session_id, prev_session_id)
                    handoff.write_handoff_summary(
                        n8n_session_id,
                        new_session_id,
                        prev_session_id,
                        prev_runtime,
                        new_runtime,
                    )
                    print(
                        f"[Handoff] Prepared handoff: {prev_runtime} → {new_runtime} "
                        f"(prev_session={prev_session_id}, new_session={new_session_id})",
                        file=sys.stderr,
                    )
                except Exception as _handoff_err:
                    print(
                        f"[Handoff] Warning: handoff preparation failed: {_handoff_err}",
                        file=sys.stderr,
                    )
                    try:
                        from session_handoff import _handoff_logger

                        _handoff_logger.error(
                            f"HANDOFF FAILED: {_handoff_err} | "
                            f"prev_runtime={prev_runtime} new_runtime={new_runtime}"
                        )
                    except Exception:
                        pass

            self.update_session_field(n8n_session_id, "runtime", new_runtime)

            # When switching runtime, reset the session ID to a new UUID since session formats are incompatible
            # (e.g., OpenCode uses "ses_*" format, Claude uses UUID format, CODEX uses UUID format, etc.)
            self.update_session_field(n8n_session_id, "session_id", new_session_id)

            # When switching runtime, also reset the model to a default for that runtime
            default_model = "gpt-5-mini"  # Default fallback
            if new_runtime == "copilot":
                default_model = "gpt-5-mini"
            elif new_runtime == "copilot-sdk":
                default_model = "gpt-5-mini"
            elif new_runtime == "claude-sdk":
                default_model = "haiku"
            elif new_runtime == "opencode":
                default_model = "opencode/gpt-5-nano"
            elif new_runtime == "claude":
                default_model = "haiku"
            elif new_runtime == "gemini":
                default_model = "gemini-1.5-flash"
            elif new_runtime == "codex":
                default_model = "gpt-5.4"
            elif new_runtime == "devin":
                default_model = os.getenv("DEVIN_DEFAULT_MODEL", "claude-sonnet-4")
            elif new_runtime == "cursor":
                default_model = os.getenv("CURSOR_DEFAULT_MODEL", "auto")
            elif new_runtime == "wee":
                default_model = os.getenv("WEE_DEFAULT_MODEL", "ollama/gemma4:e4b")

            self.update_session_field(n8n_session_id, "model", default_model)
            return f"✓ Switched runtime to **{new_runtime}**. Model set to `{default_model}`. Session reset."

    def _slash_agent(self, argument, session_data, n8n_session_id):
        """Handle /agent slash command."""
        current_runtime = session_data.get("runtime", "copilot")

        if not argument:
            return "Usage: /agent [list|set|current|invoke]"
        if argument == "list":
            out = "# 🤖 Available Agents\n\n"
            for k, v in self.AGENTS.items():
                out += f"### {k}\n{v['description']}\n\n**Location:** `{v['path']}`\n\n"
            return out
        elif argument == "current":
            ag = session_data.get("agent", "devops")
            info = self.AGENTS.get(ag, self.AGENTS["orchestrator"])
            return f"Current Agent: **{ag}**\n{info['description']}"
        elif argument.startswith("set "):
            agent = argument[4:].strip().strip("\"'")
            return self.set_agent(n8n_session_id, agent)
        elif argument.startswith("invoke "):
            # Parse: /agent invoke <agent_name> <prompt...>
            invoke_args = argument[7:].strip()  # Remove 'invoke '
            parts = invoke_args.split(None, 1)  # Split on first space
            if len(parts) < 2:
                return "Usage: /agent invoke [agent_name] [prompt]"

            agent_name = parts[0].strip("\"'")
            sub_prompt = parts[1]

            if agent_name not in self.AGENTS:
                available = ", ".join(self.AGENTS.keys())
                return f"Unknown agent: '{agent_name}'. Available: {available}"

            # Invoke the sub-agent with a new session
            print(
                f"[Agent] Invoking sub-agent '{agent_name}' with delegation",
                file=sys.stderr,
            )
            sub_session_id = str(uuid4())

            # Save delegation context
            delegation_data = {
                "session_id": sub_session_id,
                "model": session_data.get("model", "gpt-5-mini"),
                "agent": agent_name,
                "runtime": current_runtime,
                "is_delegation": True,
            }

            # Execute in sub-agent context
            return self._execute_with_context(
                sub_prompt, delegation_data, n8n_session_id
            )

    def _slash_model(self, argument, session_data, n8n_session_id):
        """Handle /model slash command."""
        current_runtime = session_data.get("runtime", "copilot")

        if not argument:
            argument = "list"  # Default to list if no argument provided

        # Handle model selection for the current runtime
        effective_rt = current_runtime

        if argument == "list" or argument.startswith("list "):
            models_dict = self.get_models_for_runtime(effective_rt)
            out = f"📋 **Available Models ({current_runtime})**\n\n"
            if not models_dict:
                return (
                    out
                    + f"❌ No models available for {effective_rt}. Check CLI configuration."
                )
            for cat in sorted(models_dict.keys()):
                out += f"**{cat}:**\n"
                for mid in sorted(models_dict[cat]):
                    desc = self._get_model_description(mid, effective_rt)
                    if desc:
                        out += f"  • `{mid}` - {desc}\n"
                    else:
                        out += f"  • `{mid}`\n"
            return out

        elif argument == "current":
            return f"Current Model: `{session_data.get('model')}` ({current_runtime})"
        elif argument.startswith("set "):
            model_name = argument[4:].strip().strip('"')
            model_id = self.get_model_from_name(model_name, effective_rt)
            if not model_id:
                return f"Unknown model '{model_name}' for runtime {effective_rt}"
            self.update_session_field(n8n_session_id, "model", model_id)
            return f"✓ Switched to model `{model_id}`"

    def _slash_session(self, argument, session_data, n8n_session_id):
        """Handle /session slash command."""
        if argument == "reset":
            with self._session_map_lock:
                session_map = self.load_session_map()
                old_data = session_map.get(n8n_session_id)
                # Preserve user-selected config across reset
                preserved = {}
                if isinstance(old_data, dict):
                    for key in (
                        "model",
                        "runtime",
                        "agent",
                        "timeout",
                        "render_type",
                        "channel",
                        "bot_id",
                        "identity",
                        "permissions",
                        "yolo_mode",
                    ):
                        if key in old_data and old_data[key] is not None:
                            preserved[key] = old_data[key]
                # Build fresh session with new backend session ID
                new_data = {
                    "session_id": str(uuid4()),
                    "last_activity": time.time(),
                }
                new_data.update(preserved)
                session_map[n8n_session_id] = new_data
                self.save_session_map(session_map)
            parts = ["\u2713 Session reset. Next message starts fresh."]
            if (
                preserved.get("model")
                or preserved.get("runtime")
                or preserved.get("agent")
            ):
                kept = []
                if "model" in preserved:
                    kept.append("model=`" + preserved["model"] + "`")
                if "runtime" in preserved:
                    kept.append("runtime=`" + preserved["runtime"] + "`")
                if "agent" in preserved:
                    kept.append("agent=`" + preserved["agent"] + "`")
                parts.append("Preserved: " + ", ".join(kept) + ".")
            return " ".join(parts)

    def _slash_timeout(self, argument, session_data, n8n_session_id):
        """Handle /timeout slash command."""
        if not argument:
            argument = "current"  # Default to showing current timeout

        if argument == "current":
            # Get timeout from session, or show default
            session_timeout = session_data.get("timeout")
            if session_timeout:
                return f"⏱️ **Current Timeout:** `{session_timeout}` seconds"
            else:
                return f"⏱️ **Current Timeout:** `{self.command_timeout}` seconds (default)"

        elif argument.startswith("set "):
            timeout_str = argument[4:].strip()
            try:
                timeout_seconds = int(timeout_str)
                # Validate timeout (minimum 30 seconds, maximum 3600 seconds / 1 hour)
                if timeout_seconds < 30:
                    return f"❌ Timeout must be at least 30 seconds. You specified: {timeout_seconds}s"
                if timeout_seconds > 3600:
                    return f"❌ Timeout must not exceed 3600 seconds (1 hour). You specified: {timeout_seconds}s"

                # Store timeout in session
                self.update_session_field(
                    n8n_session_id, "timeout", str(timeout_seconds)
                )
                return f"✓ Timeout set to `{timeout_seconds}` seconds for this session"
            except ValueError:
                return f"❌ Invalid timeout value '{timeout_str}'. Please provide a number (30-600 seconds)"
        else:
            return (
                "Usage: `/timeout` or `/timeout current` to show current timeout\n"
                "       `/timeout set [seconds]` to set a new timeout (30-3600 seconds)"
            )

    def _slash_render(self, argument, session_data, n8n_session_id):
        """Handle /render slash command."""
        if not argument:
            argument = "current"  # Default to showing current render type

        if argument == "current":
            # Get render type from session, or show default
            render_type = session_data.get("render_type", "text")
            return f"🎨 **Current Render Type:** `{render_type}`"

        elif argument.startswith("set "):
            render_type = argument[4:].strip().lower()
            valid_types = ["text", "markdown", "html", "telegram_html"]
            if render_type not in valid_types:
                return f"❌ Invalid render type '{render_type}'. Valid options: {', '.join(valid_types)}"

            # Store render type in session
            self.update_session_field(n8n_session_id, "render_type", render_type)
            return f"✓ Render type set to `{render_type}` for this session"
        else:
            return (
                "Usage: `/render` or `/render current` to show current render type\n"
                "       `/render set [text|markdown|html|telegram_html]` to set render type"
            )

    def _slash_notifications(self, argument, session_data, n8n_session_id):
        """Handle /notifications slash command with per-agent preferences.

        Usage:
        - /notifications                    Show all agents and their status
        - /notifications on                 Enable notifications for all agents
        - /notifications off                Disable notifications for all agents
        - /notifications <agent> on         Enable notifications for agent
        - /notifications <agent> off        Disable notifications for agent
        - /notifications all on             Enable all agents
        - /notifications all off            Disable all agents
        """
        if not argument:
            argument = "current"

        # Resolve identity for per-user preference store
        _notif_identity = self._bg_identity or session_data.get("identity")

        # Load list of known agents from agents.json
        agents_list = []
        try:
            if hasattr(self, "_agents"):
                agents_list = [a.get("name") for a in self._agents if a.get("name")]
        except Exception:
            pass

        if argument == "current":
            # Show per-agent preferences
            if not self._notification_mgr or not _notif_identity:
                return (
                    "\u2753 Unable to retrieve notification preferences"
                    " (not authenticated)."
                )

            agent_prefs = self._notification_mgr.get_all_agent_prefs(_notif_identity)
            if not agent_prefs:
                # No per-agent prefs set yet, show all agents with default "on"
                result = "\U0001f514 **Per-Agent Notification Preferences:**\n\n"
                result += "| Agent | Status |\n"
                result += "|-------|--------|\n"
                if agents_list:
                    for agent in sorted(agents_list):
                        pref = self._notification_mgr.get_agent_pref(
                            _notif_identity, agent
                        )
                        status = "\u2705 ON" if pref == "on" else "\u274c OFF"
                        result += f"| {agent} | {status} |\n"
                else:
                    result += "| (No agents configured) | \u2014 |\n"
            else:
                result = "\U0001f514 **Per-Agent Notification Preferences:**\n\n"
                result += "| Agent | Status |\n"
                result += "|-------|--------|\n"
                for agent in sorted(agent_prefs.keys()):
                    pref = agent_prefs[agent]
                    status = "\u2705 ON" if pref == "on" else "\u274c OFF"
                    result += f"| {agent} | {status} |\n"
                # Add agents not in prefs with default status
                if agents_list:
                    for agent in sorted(agents_list):
                        if agent not in agent_prefs:
                            result += f"| {agent} | \u2705 ON (default) |\n"

            return result

        elif argument in ("on", "all"):
            # Backward-compat: /notifications on  ->  enable all agents
            if not self._notification_mgr or not _notif_identity:
                return "\u2753 Unable to set preferences (not authenticated)."
            if agents_list:
                for agent in agents_list:
                    self._notification_mgr.set_agent_pref(_notif_identity, agent, "on")
                return "\u2713 Notifications enabled for all agents."
            return "\u2753 No agents found to configure."

        elif argument in ("off", "mute"):
            # Backward-compat: /notifications off  ->  disable all agents
            if not self._notification_mgr or not _notif_identity:
                return "\u2753 Unable to set preferences (not authenticated)."
            if agents_list:
                for agent in agents_list:
                    self._notification_mgr.set_agent_pref(_notif_identity, agent, "off")
                return "\u2713 Notifications disabled for all agents."
            return "\u2753 No agents found to configure."

        elif " " in argument:
            # Parse "agent on/off" format
            parts = argument.strip().split()
            if len(parts) != 2:
                return (
                    "Usage: `/notifications <agent> [on|off]`"
                    " or `/notifications all [on|off]`"
                    " or `/notifications` to view all"
                )

            agent_name = parts[0]
            pref_value = parts[1].lower()

            if pref_value not in ["on", "off"]:
                return f"Invalid preference: {pref_value}. Use `on` or `off`."

            if not self._notification_mgr or not _notif_identity:
                return "\u2753 Unable to set preferences (not authenticated)."

            if agent_name == "all":
                # Bulk set all agents
                if agents_list:
                    for agent in agents_list:
                        self._notification_mgr.set_agent_pref(
                            _notif_identity, agent, pref_value
                        )
                    verb = "enabled" if pref_value == "on" else "disabled"
                    return f"\u2713 Notifications {verb} for all agents."
                return "\u2753 No agents found to configure."
            else:
                # Set specific agent
                self._notification_mgr.set_agent_pref(
                    _notif_identity, agent_name, pref_value
                )
                status = "enabled" if pref_value == "on" else "disabled"
                return f"\u2713 Notifications {status} for agent `{agent_name}`."

        else:
            return (
                "Usage: `/notifications` to view all,"
                " or `/notifications <agent> [on|off]` to set,"
                " or `/notifications all [on|off]` for bulk operations"
            )

    def _slash_silent(self, argument, session_data, n8n_session_id):
        """Handle /silent slash command (F026)."""
        if not argument:
            current = session_data.get("silent_mode", False)
            status = "ON" if current else "OFF"
            vis = "hidden" if current else "shown"
            return (
                f"\U0001f507 **Silent mode:** `{status}`\n"
                f"Tool calls are {vis} in responses.\n"
                "Usage: `/silent on` or `/silent off`"
            )

        arg = argument.strip().lower()
        if arg in ("on", "true", "1", "enable"):
            self.update_session_field(n8n_session_id, "silent_mode", True)
            return "\u2713 Silent mode enabled \u2014 tool call output hidden."
        elif arg in ("off", "false", "0", "disable"):
            self.update_session_field(n8n_session_id, "silent_mode", False)
            return "\u2713 Silent mode disabled \u2014 tool call output visible."
        else:
            return (
                "Usage: `/silent [on|off]`\n"
                "Hides tool call output from responses (tools still execute)."
            )

    def _slash_verbose(self, argument, session_data, n8n_session_id):
        """Handle /verbose slash command (F026 FEATURE_QUEUE).

        Inverse of /silent: /verbose on = show tool calls (silent_mode=false).
        """
        if not argument:
            current = session_data.get("silent_mode", False)
            status = "OFF" if current else "ON"
            vis = "shown" if not current else "hidden"
            return (
                "\U0001f50a **Verbose mode:** `{}`\n"
                "Tool calls are {} in responses.\n"
                "Usage: `/verbose on` or `/verbose off`"
            ).format(status, vis)

        arg = argument.strip().lower()
        if arg in ("on", "true", "1", "enable"):
            self.update_session_field(n8n_session_id, "silent_mode", False)
            return "\u2713 Verbose mode enabled \u2014 tool call output visible."
        elif arg in ("off", "false", "0", "disable"):
            self.update_session_field(n8n_session_id, "silent_mode", True)
            return "\u2713 Verbose mode disabled \u2014 tool call output hidden."
        else:
            return (
                "Usage: `/verbose [on|off]`\n"
                "Shows tool call output in responses (inverse of /silent)."
            )

    def _slash_mode(self, argument, session_data, n8n_session_id):
        """Handle /mode slash command."""
        if not argument:
            argument = "current"  # Default to showing current mode

        if argument == "current":
            _perms = session_data.get("permissions") or {}
            _pm = (
                _perms.get("mode", "restricted")
                if isinstance(_perms, dict)
                else "restricted"
            )
            if _pm not in ("elevated", "restricted", "sandboxed"):
                _pm = (
                    "elevated"
                    if session_data.get("yolo_mode") == "on"
                    else "restricted"
                )
            return f"\u26a1 **Current Mode:** `{_pm}`"

        elif argument == "list":
            return (
                "\U0001f4cb **Available Permission Modes:**\n\n"
                "\u2022 `elevated` \u26a1 - Full access, auto-approve all operations\n"
                "\u2022 `restricted` \U0001f512 - Bounded to agent directory (default)\n"
                "\u2022 `sandboxed` \U0001f3d6\ufe0f - Read-only, no external access"
            )

        elif argument in ("elevated", "yolo"):
            _cur_perms = session_data.get("permissions", {})
            if not isinstance(_cur_perms, dict):
                _cur_perms = {}
            _cur_perms["mode"] = "elevated"
            self.update_session_field(n8n_session_id, "permissions", _cur_perms)
            self.update_session_field(n8n_session_id, "yolo_mode", "on")
            return "\u2713 Elevated mode enabled \u26a1 - auto-approving actions without prompts"

        elif argument == "restricted":
            _cur_perms = session_data.get("permissions", {})
            if not isinstance(_cur_perms, dict):
                _cur_perms = {}
            _cur_perms["mode"] = "restricted"
            self.update_session_field(n8n_session_id, "permissions", _cur_perms)
            self.update_session_field(n8n_session_id, "yolo_mode", "restricted")
            return "\u2713 Restricted mode enabled \U0001f512 - normal prompts enabled"

        elif argument == "sandboxed":
            _cur_perms = session_data.get("permissions", {})
            if not isinstance(_cur_perms, dict):
                _cur_perms = {}
            _cur_perms["mode"] = "sandboxed"
            self.update_session_field(n8n_session_id, "permissions", _cur_perms)
            self.update_session_field(n8n_session_id, "yolo_mode", "restricted")
            return "\u2713 Sandboxed mode enabled \U0001f3d6\ufe0f - read-only, no external access"

        else:
            return (
                "Usage: `/mode current` - Show current mode\n"
                "       `/mode list` - Show available modes\n"
                "       `/mode elevated` - Full access mode\n"
                "       `/mode restricted` - Bounded access mode\n"
                "       `/mode sandboxed` - Read-only mode"
            )

    def _slash_schedule(self, argument, session_data, n8n_session_id):
        """Handle /schedule slash command."""
        if not self.SCHEDULER_ENABLED:
            return "⚠️ Scheduler is not enabled on this instance."

        try:
            scheduler = self._get_scheduler()
        except Exception as e:
            return f"⚠️ Scheduler unavailable: {e}"

        sub = (argument or "").strip()
        sub_lower = sub.lower()

        # /schedule or /schedule list
        if not sub or sub_lower == "list":
            result = scheduler.list_jobs()
            jobs = result.get("result", [])
            if not jobs:
                return "📅 **Scheduled Jobs**\n\nNo jobs scheduled."
            lines = ["📅 **Scheduled Jobs**\n"]
            for j in jobs:
                status = "▶️" if j.get("enabled") else "⏸"
                recurring = "🔁" if j.get("recurring") else "1️⃣"
                lines.append(
                    f"{status} {recurring} `{j['id']}` — **{j['name']}**\n"
                    f"   Schedule: `{j['schedule']}`\n"
                    f"   Next run: `{j.get('next_run','?')}`\n"
                    f"   Agent: `{j.get('agent','?')}` / Runtime: `{j.get('runtime','?')}`"
                )
            return "\n\n".join(lines)

        # /schedule status
        elif sub_lower == "status":
            result = scheduler.doctor()
            info = result.get("result", result)
            lines = ["🩺 **Scheduler Status**\n"]
            for k, v in info.items():
                lines.append(f"• **{k}**: `{v}`")
            return "\n".join(lines)

        # /schedule pause <job_id>
        elif sub_lower.startswith("pause "):
            job_id = sub[6:].strip()
            result = scheduler.pause_job(job_id)
            if result.get("success"):
                return f"⏸ Job `{job_id}` paused."
            return f"❌ {result.get('message', 'Failed to pause job.')}"

        # /schedule resume <job_id>
        elif sub_lower.startswith("resume "):
            job_id = sub[7:].strip()
            result = scheduler.resume_job(job_id)
            if result.get("success"):
                return f"▶️ Job `{job_id}` resumed."
            return f"❌ {result.get('message', 'Failed to resume job.')}"

        # /schedule delete <job_id>
        elif sub_lower.startswith("delete ") or sub_lower.startswith("remove "):
            job_id = sub.split(" ", 1)[1].strip()
            result = scheduler.delete_job(job_id)
            if result.get("success"):
                return f"🗑️ Job `{job_id}` deleted."
            return f"❌ {result.get('message', 'Failed to delete job.')}"

        # /schedule info <job_id>
        elif sub_lower.startswith("info "):
            job_id = sub[5:].strip()
            result = scheduler.get_job(job_id)
            if not result.get("success"):
                return f"❌ {result.get('message', 'Job not found.')}"
            j = result["result"]
            cron_line = f"\n• **Cron:** `{j['cron']}`" if j.get("cron") else ""
            return (
                f"📋 **Job: {j['name']}**\n\n"
                f"• **ID:** `{j['id']}`\n"
                f"• **Schedule:** `{j['schedule']}`{cron_line}\n"
                f"• **Next run:** `{j.get('next_run','?')}`\n"
                f"• **Last run:** `{j.get('last_run','never')}`\n"
                f"• **Agent:** `{j.get('agent','?')}` / Runtime: `{j.get('runtime','?')}`\n"
                f"• **Recurring:** {'Yes 🔁' if j.get('recurring') else 'No 1️⃣'}\n"
                f"• **Enabled:** {'Yes ▶️' if j.get('enabled') else 'No ⏸'}\n"
                f"• **Task:** {j.get('task','')}"
            )

        # /schedule logs <job_id>
        elif sub_lower.startswith("logs "):
            job_id = sub[5:].strip()
            result = scheduler.get_logs(job_id)
            if not result.get("success"):
                return f"❌ {result.get('message', 'No logs found.')}"
            logs = result.get("result", [])
            if not logs:
                return f"📜 No logs for job `{job_id}`."
            recent = logs[-20:]  # last 20 entries
            lines = [f"📜 **Logs for `{job_id}`** (last {len(recent)}):\n"]
            lines.extend(f"`{entry}`" for entry in recent)
            return "\n".join(lines)

        # /schedule results <job_id>
        elif sub_lower.startswith("results "):
            job_id = sub[8:].strip()
            result = scheduler.get_results(job_id)
            if not result.get("success"):
                return f"❌ {result.get('message', 'No results found.')}"
            results = result.get("result", [])
            if not results:
                return f"📊 No results for job `{job_id}` yet."
            lines = [f"📊 **Results for `{job_id}`** ({len(results)} runs):\n"]
            for r in results[-5:]:  # last 5 runs
                status = "✅" if r.get("success") else "❌"
                lines.append(
                    f"{status} `{r.get('timestamp','?')}` — {r.get('summary','')[:100]}"
                )
            return "\n".join(lines)

        # /schedule add <name> | <schedule> | <task>
        # e.g.: /schedule add Daily Report | every day at 9am | generate daily summary
        elif sub_lower.startswith("add "):
            raw = sub[4:].strip()
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) < 3:
                return (
                    "Usage: `/schedule add <name> | <schedule> | <task>`\n\n"
                    "Example: `/schedule add Daily Report | every day at 9am | generate a daily summary`\n"
                    "Example: `/schedule add One-time Ping | in 10 minutes | say hello`"
                )
            name, schedule_str, task = parts[0], parts[1], parts[2]
            recurring = not any(w in schedule_str.lower() for w in ["in ", "once"])
            result = scheduler.schedule_task(
                name=name,
                schedule=schedule_str,
                task=task,
                recurring=recurring,
            )
            if result.get("success"):
                j = result["result"]
                cron_line = f"\n• **Cron:** `{j['cron']}`" if j.get("cron") else ""
                return (
                    f"✅ **Job scheduled!**\n\n"
                    f"• **ID:** `{j['id']}`\n"
                    f"• **Name:** {j['name']}\n"
                    f"• **Schedule:** `{j['schedule']}`{cron_line}\n"
                    f"• **Next run:** `{j.get('next_run','?')}`\n"
                    f"• **Recurring:** {'Yes 🔁' if j.get('recurring') else 'No 1️⃣'}"
                )
            return f"❌ {result.get('message', 'Failed to schedule job.')}"

        else:
            return (
                "📅 **Schedule Commands**\n\n"
                "• `/schedule list` — List all scheduled jobs\n"
                "• `/schedule status` — Scheduler health status\n"
                "• `/schedule add <name> | <schedule> | <task>` — Create a new job\n"
                "• `/schedule info <job_id>` — Show job details\n"
                "• `/schedule pause <job_id>` — Pause a job\n"
                "• `/schedule resume <job_id>` — Resume a paused job\n"
                "• `/schedule delete <job_id>` — Delete a job\n"
                "• `/schedule logs <job_id>` — View job logs\n"
                "• `/schedule results <job_id>` — View job execution results\n\n"
                "**Examples:**\n"
                "`/schedule add Daily Report | every day at 9am | generate daily summary`\n"
                "`/schedule pause daily-report`\n"
                "`/schedule delete daily-report`"
            )

    def _slash_background(self, argument, session_data, n8n_session_id):
        """Handle /background slash command."""
        sub = (argument or "").strip()
        if not sub or sub.lower() == "help":
            return (
                "⚡ **Background Task Commands**\n\n"
                "• `/background <prompt>` — Run a task in the background\n"
                "• `/background agent=devops <prompt>` — Override agent\n"
                "• `/background runtime=claude model=sonnet <prompt>` — Override runtime/model\n"
                "• `/background timeout=600 <prompt>` — Override timeout (seconds)\n"
                "• `/background list` — List your background tasks\n"
                "• `/background status <task_id>` — Check task status\n"
                "• `/background kill <task_id>` — Kill a running task\n"
                "• `/background steer <task_id> <instruction>` — Steer a running task\n\n"
                "Background tasks run in separate sessions and don't block your chat.\n"
                "Monitor them in the ⚡ Tasks tab in the sidebar."
            )

        if not self._bg_task_mgr:
            return "⚠️ Background task manager not available."

        sub_lower = sub.lower()

        if sub_lower == "list":
            tasks = self._bg_task_mgr.list_all_tasks()
            if not tasks:
                return "⚡ **Background Tasks**\n\nNo background tasks."
            icons = {
                "running": "🟢",
                "completed": "✅",
                "failed": "❌",
                "killed": "🛑",
            }
            lines = ["⚡ **Background Tasks**\n"]
            for t in tasks:
                icon = icons.get(t["status"], "❓")
                lines.append(
                    f"{icon} `{t['task_id']}` — **{t['status']}**\n"
                    f"   Agent: `{t['agent']}` | Prompt: {t['prompt'][:80]}..."
                )
            return "\n\n".join(lines)

        if sub_lower.startswith("status "):
            tid = sub[7:].strip()
            task = self._bg_task_mgr.get_task(tid)
            if not task:
                return f"❌ Task `{tid}` not found."
            icons = {
                "running": "🟢",
                "completed": "✅",
                "failed": "❌",
                "killed": "🛑",
            }
            icon = icons.get(task["status"], "❓")
            elapsed = ""
            if task["status"] == "running":
                try:
                    ct = time.mktime(
                        time.strptime(task["created_at"], "%Y-%m-%dT%H:%M:%SZ")
                    )
                    secs = int(time.time() - ct)
                    elapsed = f"\n**Elapsed:** {secs // 60}m {secs % 60}s"
                except Exception:
                    pass
            return (
                f"{icon} **Task: `{task['task_id']}`**\n\n"
                f"**Status:** {task['status']}\n"
                f"**Agent:** `{task['agent']}` | Runtime: `{task['runtime']}` | Model: `{task['model']}`\n"
                f"**Prompt:** {task['prompt'][:200]}"
                f"{elapsed}"
            )

        if sub_lower.startswith("steer "):
            parts = sub[6:].strip().split(None, 1)
            if len(parts) < 2:
                return "Usage: `/background steer <task_id> <instruction>`"
            tid, instruction = parts
            task = self._bg_task_mgr.get_task(tid)
            if not task:
                return f"Task `{tid}` not found."
            if task["status"] != "running":
                return f"Task `{tid}` is {task['status']}, not running."
            self._bg_task_mgr.write_steering(tid, instruction)
            return (
                f"\U0001f3af **Steering sent to `{tid}`**\n\n"
                f"Instruction: {instruction[:200]}"
            )

        if sub_lower.startswith("kill "):
            tid = sub[5:].strip()
            if self._bg_task_mgr.kill_task(tid):
                return f"🛑 Task `{tid}` killed."
            return f"❌ Could not kill task `{tid}` (not found or not running)."

        # Otherwise it's a prompt to run in the background
        # Parse optional overrides: agent=X runtime=Y model=Z timeout=N
        bg_agent = session_data.get("agent", "orchestrator")
        bg_runtime = session_data.get("runtime", "copilot")
        bg_model = session_data.get("model", "gpt-5-mini")
        bg_timeout_override = None
        bg_prompt_parts = []
        for word in sub.split():
            if word.startswith("agent="):
                bg_agent = word[6:]
            elif word.startswith("runtime="):
                bg_runtime = word[8:]
            elif word.startswith("model="):
                bg_model = word[6:]
            elif word.startswith("timeout="):
                try:
                    bg_timeout_override = int(word[8:])
                except ValueError:
                    pass
            else:
                bg_prompt_parts.append(word)
        bg_prompt = " ".join(bg_prompt_parts)
        # Resolve runtime, model, permission_mode from dispatch_config
        agent_config = self.AGENTS.get(bg_agent, {})
        dispatch_config = agent_config.get("dispatch_config", {})
        if not any(word.startswith("runtime=") for word in sub.split()):
            bg_runtime = dispatch_config.get("runtime", bg_runtime)
        if not any(word.startswith("model=") for word in sub.split()):
            bg_model = dispatch_config.get("model", bg_model)
        bg_permission_mode = dispatch_config.get("permission_mode", "restricted")

        if not bg_prompt:
            return "❌ No prompt provided. Usage: `/background <prompt>`"

        channel = session_data.get("channel", "webui")
        identity = self._bg_identity or "unknown"

        running = self._bg_task_mgr.count_running(channel, identity)
        if running >= BackgroundTaskManager.MAX_TASKS_PER_USER:
            return f"❌ Maximum {BackgroundTaskManager.MAX_TASKS_PER_USER} concurrent background tasks allowed."

        # Priority: explicit timeout= > dispatch_config.timeout > default

        if bg_timeout_override is not None:

            bg_timeout = bg_timeout_override

        else:

            agent_config = self.AGENTS.get(bg_agent, {})

            dispatch_config = agent_config.get("dispatch_config", {})

            bg_timeout = dispatch_config.get("timeout", get_bg_command_timeout())
        task_id = f"bg_{str(uuid4())[:8]}"
        bg_session_id = f"bg_{str(uuid4())[:8]}"
        self._bg_task_mgr.create_task(
            task_id=task_id,
            session_id=bg_session_id,
            user_identity=identity,
            channel=channel,
            agent=bg_agent,
            runtime=bg_runtime,
            model=bg_model,
            prompt=bg_prompt,
            origin_session_id=n8n_session_id,
            permission_mode=bg_permission_mode,
        )
        # Launch in background thread
        import concurrent.futures as _cf

        _cf.ThreadPoolExecutor(max_workers=1).submit(
            self._execute_background_task,
            task_id,
            bg_session_id,
            bg_prompt,
            bg_agent,
            bg_runtime,
            bg_model,
            channel,
            bg_timeout,
        )

        return (
            f"⚡ **Background task started!**\n\n"
            f"• **Task ID:** `{task_id}`\n"
            f"• **Agent:** `{bg_agent}` | Runtime: `{bg_runtime}` | Model: `{bg_model}`\n"
            f"• **Timeout:** `{bg_timeout}s` ({bg_timeout // 60}m)\n"
            f"• **Prompt:** {bg_prompt[:150]}\n\n"
            f"Check the ⚡ Tasks tab or use `/background status {task_id}` to monitor."
        )

    def _slash_update(self, argument, session_data, n8n_session_id):
        """Handle /update / /upgrade / /pull slash command."""
        sub = (argument or "").strip().lower()

        # Detect environment from this file's location
        _repo_dir = os.path.dirname(os.path.abspath(__file__))
        _is_dev = _repo_dir.endswith("-dev")
        _env_label = "dev" if _is_dev else "prod"
        _log_path = "/tmp/wee-update.log" if _is_dev else "/tmp/wee-update-prod.log"
        _branch = "dev" if _is_dev else "main"

        if sub == "status":
            try:
                with open(_log_path) as f:
                    tail = f.readlines()[-30:]
                return (
                    f"📋 **Last update log** (`{_log_path}`):\n```\n{''.join(tail)}```"
                )
            except FileNotFoundError:
                return "ℹ️ No update log found. No update has been run yet."
            except Exception as e:
                return f"❌ Error reading update log: {e}"

        if sub == "help":
            return (
                f"🔄 **Update Commands** ({_env_label})\n\n"
                f"• `/update` — Pull latest code from `{_branch}` and restart all {_env_label} services\n"
                f"• `/update status` — Show last update log\n"
                f"• `/update help` — This message\n\n"
                f"Aliases: `/upgrade`, `/pull`\n\n"
                f"The update runs fully detached — it survives the service restart.\n"
                f"You'll get a Telegram notification when it completes."
            )

        # Launch the detached update process
        try:
            from update_launcher import launch_update

            pid = launch_update()
        except Exception as e:
            return f"❌ Failed to launch update: {e}"

        return (
            f"🔄 **Update started** (PID: `{pid}`)\n\n"
            f"Pulling latest `{_branch}` and restarting {_env_label} services.\n"
            f"I may go offline briefly — you will receive a Telegram notification when complete.\n\n"
            f"Log: `{_log_path}`\n"
            f"Check status later: `/update status`"
        )

    def _load_agents_config(self, config_file: Optional[str] = None) -> Dict:
        """Load agents configuration from JSON file

        Priority:
          1. Explicit config_file parameter
          2. AGENT_CONFIG_FILE environment variable
          3. ./agents.json in current working directory
          4. agents.json next to this script
        """
        if config_file:
            config_path = Path(config_file)
        else:
            env_path = os.environ.get("AGENT_CONFIG_FILE")
            if env_path:
                config_path = Path(env_path)
            else:
                config_path = Path.cwd() / "agents.json"
                if not config_path.exists():
                    config_path = Path(__file__).parent / "agents.json"

        # Persist the resolved path so the file-watcher can find it later
        self._agents_config_path = config_path

        if not config_path.exists():
            print(
                f"[Warning] Agents config file not found at {config_path}. Using empty agents.",
                file=sys.stderr,
            )
            self._agents_json_mtime = 0.0
            return {}

        try:
            self._agents_json_mtime = config_path.stat().st_mtime
            with open(config_path, "r") as f:
                config = json.load(f)
                agents = {}
                for agent in config.get("agents", []):
                    name = agent.get("name")
                    if not name:
                        print(
                            f"[Warning] Agent entry missing 'name' field",
                            file=sys.stderr,
                        )
                        continue
                    agents[name] = {
                        "path": os.path.expanduser(agent.get("path", "")),
                        "description": agent.get("description", ""),
                        "max_concurrent": agent.get("max_concurrent", 1),
                        "runtime": agent.get("runtime", "copilot"),
                        "model": agent.get("model", ""),
                        "primary_runtime": agent.get("primary_runtime"),
                        "primary_model": agent.get("primary_model"),
                        "fallback_runtime": agent.get("fallback_runtime"),
                        "fallback_model": agent.get("fallback_model"),
                        "permission_mode": agent.get("permission_mode"),
                        "yolo": agent.get("yolo", False),
                        "dispatch_config": agent.get("dispatch_config", {}),
                    }
                return agents
        except json.JSONDecodeError as e:
            print(f"[Error] Failed to parse agents config: {e}", file=sys.stderr)
            return {}
        except Exception as e:
            print(f"[Error] Failed to load agents config: {e}", file=sys.stderr)
            return {}

        try:
            from pydantic import ValidationError as _ValidationError

            from config_schemas import validate_agents_config

            validate_agents_config(config)
        except ImportError:
            pass
        except _ValidationError as _schema_exc:
            logger.critical(
                "[config] agents.json schema validation failed: %s",
                _schema_exc,
            )
            raise

        agents = {}
        for agent in config.get("agents", []):
            name = agent.get("name")
            if not name:
                logger.warning("[Warning] Agent entry missing 'name' field")
                continue
            agents[name] = {
                "path": agent.get("path", ""),
                "description": agent.get("description", ""),
                "max_concurrent": agent.get("max_concurrent", 1),
                "runtime": agent.get("runtime", "copilot"),
                "model": agent.get("model", ""),
            }
        return agents

    def reload_agents_from_disk(self) -> tuple:
        """Hot-reload agents.json with validation and safe fallback.

        Returns (success: bool, message: str).
        On failure the in-memory AGENTS dict is left unchanged.
        """
        config_path = getattr(self, "_agents_config_path", None)
        if not config_path or not config_path.exists():
            return False, f"agents.json not found at {config_path}"

        try:
            raw = config_path.read_text()
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return False, f"Invalid JSON in agents.json: {exc}"
        except Exception as exc:
            return False, f"Failed to read agents.json: {exc}"

        # Structural validation
        if not isinstance(data, dict) or "agents" not in data:
            return False, "agents.json missing top-level 'agents' key"
        if not isinstance(data["agents"], list):
            return False, "'agents' must be an array"
        for idx, ag in enumerate(data["agents"]):
            if not isinstance(ag, dict):
                return False, f"agents[{idx}] is not an object"
            if "name" not in ag or "path" not in ag:
                return False, f"agents[{idx}] missing required 'name' or 'path'"

        # Parse into the internal dict keyed by name
        fresh: Dict[str, dict] = {}
        for agent in data["agents"]:
            name = agent.get("name")
            if not name:
                continue
            fresh[name] = {
                "path": os.path.expanduser(agent.get("path", "")),
                "description": agent.get("description", ""),
                "max_concurrent": agent.get("max_concurrent", 1),
                "runtime": agent.get("runtime", "copilot"),
                "model": agent.get("model", ""),
                "primary_runtime": agent.get("primary_runtime"),
                "primary_model": agent.get("primary_model"),
                "fallback_runtime": agent.get("fallback_runtime"),
                "fallback_model": agent.get("fallback_model"),
                "permission_mode": agent.get("permission_mode"),
                "yolo": agent.get("yolo", False),
                "dispatch_config": agent.get("dispatch_config", {}),
            }

        if not fresh and self.AGENTS:
            return False, "Refusing to replace non-empty agent config with empty one"

        old_count = len(self.AGENTS)
        # Atomic swap — Python dict assignment is thread-safe under the GIL
        self.AGENTS = fresh
        self._agents_json_mtime = config_path.stat().st_mtime
        new_count = len(fresh)

        msg = f"Reloaded {new_count} agent(s) from disk (was {old_count})."
        return True, msg

    def _load_skill_repositories(self) -> List[Dict]:
        """Load skill repositories from configuration file.

        Looks for skill_repositories.json in the script directory or current directory.
        Returns a list of enabled repository configurations.
        """
        config_paths = [
            Path.cwd() / "skill_repositories.json",
            Path(__file__).parent / "skill_repositories.json",
        ]

        for config_path in config_paths:
            if config_path.exists():
                try:
                    with open(config_path, "r") as f:
                        config = json.load(f)
                        # Filter to only enabled repositories
                        repositories = [
                            repo
                            for repo in config.get("repositories", [])
                            if repo.get("enabled", False)
                        ]
                        if repositories:
                            print(
                                f"[Info] Loaded {len(repositories)} skill repositories from {config_path}",
                                file=sys.stderr,
                            )
                            return repositories
                except Exception as e:
                    print(
                        f"[Warning] Failed to load skill repositories from {config_path}: {e}",
                        file=sys.stderr,
                    )
                    continue

        # Return default Anthropic repository if no config found
        print(
            "[Info] No skill_repositories.json found, using default Anthropic repository",
            file=sys.stderr,
        )
        return [
            {
                "name": "Anthropic Official",
                "url": "https://github.com/anthropics/skills.git",
                "description": "Official Anthropic skills repository",
                "enabled": True,
            }
        ]

    def _format_repository_info(self) -> str:
        """Format available skill repositories for display in context."""
        if not self.skill_repositories:
            return "No skill repositories configured."

        repo_text = ""
        for repo in self.skill_repositories:
            name = repo.get("name", "Unknown")
            desc = repo.get("description", "No description")
            url = repo.get("url", "")
            repo_text += f"• **{name}** - {desc}\n"
            if url:
                repo_text += f"  URL: {url}\n"
        return repo_text

    def load_running_queries(self) -> Dict:
        """Load the running queries tracking data"""
        if not self.running_queries_file.exists():
            return {}

        try:
            with open(self.running_queries_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def save_running_queries(self, queries: dict):
        """Save the running queries tracking data"""
        with open(self.running_queries_file, "w") as f:
            json.dump(queries, f, indent=2)

    def track_running_query(
        self, n8n_session_id: str, pid: int, runtime: str, agent: str, prompt: str
    ):
        """Track a running query with its PID and session info"""
        queries = self.load_running_queries()
        queries[n8n_session_id] = {
            "pid": pid,
            "runtime": runtime,
            "agent": agent,
            "prompt": prompt[: self.MAX_PROMPT_LENGTH],
            "start_time": time.time(),
            "last_output": "",
        }
        self.save_running_queries(queries)
        print(
            f"[Track] Started tracking query for session {n8n_session_id}, PID: {pid}",
            file=sys.stderr,
        )

    def update_query_output(self, n8n_session_id: str, output_snippet: str):
        """Update the last output snippet for a running query"""
        queries = self.load_running_queries()
        if n8n_session_id in queries:
            queries[n8n_session_id]["last_output"] = output_snippet[
                -self.MAX_OUTPUT_LENGTH :
            ]
            self.save_running_queries(queries)

    def clear_running_query(self, n8n_session_id: str):
        """Clear tracking for a completed/cancelled query"""
        queries = self.load_running_queries()
        if n8n_session_id in queries:
            del queries[n8n_session_id]
            self.save_running_queries(queries)
            print(
                f"[Track] Cleared tracking for session {n8n_session_id}",
                file=sys.stderr,
            )

    def get_running_query(self, n8n_session_id: str) -> Optional[Dict]:
        """Get tracking info for a running query"""
        queries = self.load_running_queries()
        return queries.get(n8n_session_id)

    def is_process_running(self, pid: int) -> bool:
        """Check if a process with given PID is still running

        Uses os.kill with signal 0 to test process existence without
        actually sending a signal to the process.
        """
        try:
            os.kill(pid, 0)  # Signal 0 tests existence without affecting the process
            return True
        except OSError:
            return False

    def kill_process(self, pid: int) -> bool:
        """Kill a process with given PID"""
        try:
            os.kill(pid, signal.SIGKILL)
            return True
        except OSError as e:
            print(f"[Error] Failed to kill process {pid}: {e}", file=sys.stderr)
            return False

    def _copilot_static_fallback(self) -> dict:
        # Static model list used when copilot CLI is unavailable
        return {
            "Auto": [
                "auto",
            ],
            "Claude Models": [
                "claude-opus-4.7",
                "claude-sonnet-4.6",
                "claude-opus-4.6",
                "claude-haiku-4.5",
                "claude-sonnet-4.5",
                "claude-opus-4.6-fast",
                "claude-opus-4.5",
                "claude-sonnet-4",
            ],
            "GPT Models": [
                "gpt-5.4",
                "gpt-5.4-mini",
                "gpt-5.3-codex",
                "gpt-5.2-codex",
                "gpt-5.2",
                "gpt-5.1-codex-max",
                "gpt-5.1-codex",
                "gpt-5.1",
                "gpt-5.1-codex-mini",
                "gpt-5-mini",
                "gpt-4.1",
            ],
            "Google Models": [
                "gemini-3-pro-preview",
            ],
        }

    def fetch_copilot_models(self) -> Dict:
        """Fetch available models from copilot CLI help text"""
        if not self.copilot_bin:
            print("Copilot executable not found in any search paths", file=sys.stderr)
            return self._copilot_static_fallback()

        try:
            # Use --no-color to ensure clean text
            cmd = [self.copilot_bin, "--help", "--no-color"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"Copilot help command failed: {result.stderr}", file=sys.stderr)
                return self._copilot_static_fallback()

            # Method 1: Robust Regex
            # Look for --model, then content, then (choices: ... )
            # We use [\s\S] instead of . with re.DOTALL for explicit multiline matching
            match = re.search(
                r"--model\s+<model>[\s\S]*?\(choices:\s*([\s\S]*?)\)", result.stdout
            )

            models = []
            if match:
                raw_content = match.group(1)
                models = re.findall(r'"([^"]+)"', raw_content)
                # Validate: filter out false positives (e.g. --output-format choices: "text", "json")
                models = [
                    m
                    for m in models
                    if any(
                        kw in m.lower()
                        for kw in ["gpt", "claude", "gemini", "o1", "o3", "o4", "auto"]
                    )
                ]

            # Method 2: Fallback (if regex fails due to layout changes)
            if not models:
                # Look for known models as a sanity check/fallback
                fallback_models = [
                    "auto",
                    "claude-opus-4.7",
                    "claude-sonnet-4.6",
                    "claude-sonnet-4.5",
                    "claude-haiku-4.5",
                    "claude-opus-4.6",
                    "claude-opus-4.6-fast",
                    "claude-opus-4.5",
                    "claude-sonnet-4",
                    "gemini-3-pro-preview",
                    "gpt-5.4",
                    "gpt-5.4-mini",
                    "gpt-5.3-codex",
                    "gpt-5.2-codex",
                    "gpt-5.2",
                    "gpt-5.1-codex-max",
                    "gpt-5.1-codex",
                    "gpt-5.1",
                    "gpt-5.1-codex-mini",
                    "gpt-5-mini",
                    "gpt-4.1",
                ]
                found_fallbacks = [m for m in fallback_models if m in result.stdout]
                if found_fallbacks:
                    # If we found known models but regex failed, try a looser regex
                    loose_match = re.findall(r'"([a-zA-Z0-9\-\.]+)"', result.stdout)
                    # Filter for likely model names (heuristic)
                    models = [
                        m
                        for m in loose_match
                        if "gpt" in m or "claude" in m or "gemini" in m
                    ]

            if not models:
                # copilot CLI no longer lists models in --help (choices removed in newer versions).
                # Return the static fallback list so /model list and /model set still work.
                return {
                    "Auto": [
                        "auto",
                    ],
                    "Claude Models": [
                        "claude-opus-4.7",
                        "claude-sonnet-4.6",
                        "claude-opus-4.6",
                        "claude-haiku-4.5",
                        "claude-sonnet-4.5",
                        "claude-opus-4.6-fast",
                        "claude-opus-4.5",
                        "claude-sonnet-4",
                    ],
                    "GPT Models": [
                        "gpt-5.4",
                        "gpt-5.4-mini",
                        "gpt-5.3-codex",
                        "gpt-5.2-codex",
                        "gpt-5.2",
                        "gpt-5.1-codex-max",
                        "gpt-5.1-codex",
                        "gpt-5.1",
                        "gpt-5.1-codex-mini",
                        "gpt-5-mini",
                        "gpt-4.1",
                    ],
                    "Google Models": [
                        "gemini-3-pro-preview",
                    ],
                }

            # Categorize
            categorized = {}
            for m in models:
                cat = "Other Models"
                if "claude" in m.lower():
                    cat = "Claude Models"
                elif "gpt" in m.lower() or re.match(r"^o\d", m.lower()):
                    cat = "GPT Models"
                elif "gemini" in m.lower():
                    cat = "Google Models"

                if cat not in categorized:
                    categorized[cat] = []
                categorized[cat].append(m)

            return categorized
        except Exception as e:
            print(f"Error fetching copilot models: {e}", file=sys.stderr)
            return self._copilot_static_fallback()

    def fetch_opencode_models(self) -> Dict:
        """Fetch available models from opencode CLI, falling back to static list on failure."""
        try:
            cmd = [str(self.opencode_bin), "models"]
            # Use configured command timeout (may be set via COMMAND_TIMEOUT)
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.command_timeout
            )

            if result.returncode != 0:
                print(
                    f"[Error] opencode models failed (exit {result.returncode}): {result.stderr}",
                    file=sys.stderr,
                )
                return self._static_models_to_dict(self.OPENCODE_MODELS)

            if not result.stdout.strip():
                print(
                    "[Warning] opencode models returned empty output", file=sys.stderr
                )
                return self._static_models_to_dict(self.OPENCODE_MODELS)

            models_by_provider = {}
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue

                parts = line.split("/", 1)
                if len(parts) == 2:
                    provider, _ = parts
                else:
                    provider = "other"

                if provider not in models_by_provider:
                    models_by_provider[provider] = []
                models_by_provider[provider].append(line)

            return models_by_provider
        except subprocess.TimeoutExpired:
            print(
                f"[Error] opencode models command timed out after {self.command_timeout}s",
                file=sys.stderr,
            )
            return self._static_models_to_dict(self.OPENCODE_MODELS)
        except Exception as e:
            print(f"Error fetching opencode models: {e}", file=sys.stderr)
            return self._static_models_to_dict(self.OPENCODE_MODELS)

    # Curated popular OpenRouter model IDs for auto-discovery filtering
    OPENROUTER_POPULAR_MODELS = {
        "meta-llama/llama-4-maverick",
        "meta-llama/llama-4-scout",
        "google/gemma-3-27b-it:free",
        "google/gemma-4-31b-it:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-nano-9b-v2:free",
        "qwen/qwen3-coder:free",
        "anthropic/claude-sonnet-4.6",
        "anthropic/claude-opus-4.6",
        "google/gemini-3.1-flash-lite-preview",
        "google/gemini-3.1-pro-preview-customtools",
        "openai/gpt-4.1",
        "openai/gpt-4.1-mini",
        "deepseek/deepseek-v3.2",
        "qwen/qwen3.6-plus",
        "mistralai/mistral-small-2603",
        "cohere/command-r-plus-08-2024",
    }

    WEE_MODELS = {
        "Wee Native (Ollama)": [
            ("ollama/gemma4:e4b", "Ollama Gemma 4 E4B (local)", ["gemma4", "gemma"]),
            ("ollama/qwen3", "Ollama Qwen 3 (local)", ["qwen3", "qwen"]),
            (
                "ollama/qwen3.5-64k:latest",
                "Ollama Qwen 3.5 64K (local)",
                ["qwen3.5-64k", "qwen3.5"],
            ),
            (
                "ollama/granite3.3-tuned",
                "Ollama Granite 3.3 Tuned (local)",
                ["granite", "granite3.3"],
            ),
        ],
        "Wee Native (OpenRouter)": [
            (
                "openrouter/meta-llama/llama-4-maverick",
                "Llama 4 Maverick via OpenRouter",
                ["llama-4-maverick", "maverick"],
            ),
            (
                "openrouter/meta-llama/llama-4-scout",
                "Llama 4 Scout via OpenRouter",
                ["llama-4-scout", "scout"],
            ),
            (
                "openrouter/anthropic/claude-sonnet-4.6",
                "Claude Sonnet 4.6 via OpenRouter",
                ["or-claude-sonnet"],
            ),
            (
                "openrouter/google/gemini-3.1-flash-lite-preview",
                "Gemini 3.1 Flash Lite via OpenRouter",
                ["or-gemini-flash"],
            ),
            ("openrouter/openai/gpt-4.1", "GPT-4.1 via OpenRouter", ["or-gpt-4.1"]),
            (
                "openrouter/deepseek/deepseek-v3.2",
                "DeepSeek V3.2 via OpenRouter",
                ["or-deepseek"],
            ),
        ],
        "Wee Native (OpenRouter Free)": [
            (
                "openrouter/google/gemma-3-27b-it:free",
                "Gemma 3 27B FREE via OpenRouter",
                ["gemma-3-free", "gemma-free"],
            ),
            (
                "openrouter/google/gemma-4-31b-it:free",
                "Gemma 4 31B FREE via OpenRouter",
                ["gemma-4-free"],
            ),
            (
                "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                "Llama 3.3 70B FREE via OpenRouter",
                ["llama-free"],
            ),
            (
                "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
                "Nemotron 3 Super 120B FREE via OpenRouter",
                ["nemotron-free"],
            ),
            (
                "openrouter/nvidia/nemotron-nano-9b-v2:free",
                "Nemotron Nano 9B FREE via OpenRouter",
                ["nemotron-nano-free"],
            ),
            (
                "openrouter/qwen/qwen3-coder:free",
                "Qwen3 Coder FREE via OpenRouter",
                ["qwen3-free", "qwen-free"],
            ),
        ],
    }

    def _static_models_to_dict(self, static_dict: Dict) -> Dict:
        """Convert static model config {cat: [(id, desc, aliases)...]} to {cat: [id,...]}."""
        return {
            cat: [model_id for model_id, _desc, _aliases in entries]
            for cat, entries in static_dict.items()
        }

    def _get_model_description(self, model_id: str, runtime: str) -> Optional[str]:
        """Look up a human-readable description for a model from static metadata."""
        # First check env-loaded models (if cached)
        env_models_map = {
            "claude": self._env_claude_models,
            "claude-sdk": self._env_claude_models,
            "gemini": self._env_gemini_models,
            "codex": self._env_codex_models,
            "devin": self._env_devin_models,
            "cursor": self._env_cursor_models,
            "wee": self._env_wee_models,
        }
        env_models = env_models_map.get(runtime)
        if env_models:
            for _cat, entries in env_models.items():
                for mid, desc, _aliases in entries:
                    if mid == model_id:
                        return desc

        # Fall back to static models
        static_map = {
            "claude": self.CLAUDE_MODELS,
            "claude-sdk": self.CLAUDE_MODELS,
            "gemini": self.GEMINI_MODELS,
            "codex": self.CODEX_MODELS,
            "opencode": self.OPENCODE_MODELS,
            "devin": self.DEVIN_MODELS,
            "cursor": self.CURSOR_MODELS,
            "wee": self.WEE_MODELS,
        }
        models_dict = static_map.get(runtime)
        if not models_dict:
            return None
        for _cat, entries in models_dict.items():
            for mid, desc, _aliases in entries:
                if mid == model_id:
                    return desc
        return None

    def fetch_claude_models(self) -> Dict:
        """Return available Claude models from environment or fallback to static list.

        Claude Code CLI does not currently expose a model-listing subcommand.
        Models are read from CLAUDE_MODELS_JSON environment variable, with
        static CLAUDE_MODELS as fallback.
        """
        # Try to load from environment variable first
        env_models = os.getenv("CLAUDE_MODELS_JSON")
        if env_models:
            try:
                import json

                models_dict = json.loads(env_models)
                # Cache the full model dict with descriptions for lookup later
                self._env_claude_models = models_dict
                # Convert to the expected format {category: [model_ids]}
                return self._static_models_to_dict(models_dict)
            except (json.JSONDecodeError, ValueError) as e:
                print(
                    f"Warning: Failed to parse CLAUDE_MODELS_JSON: {e}", file=sys.stderr
                )

        # Fallback to static configuration
        return self._static_models_to_dict(self.CLAUDE_MODELS)

    def fetch_gemini_models(self) -> Dict:
        """Return available Gemini models from environment or fallback to static list.

        Gemini CLI does not currently expose a model-listing subcommand.
        Models are read from GEMINI_MODELS_JSON environment variable, with
        static GEMINI_MODELS as fallback.
        """
        # Try to load from environment variable first
        env_models = os.getenv("GEMINI_MODELS_JSON")
        if env_models:
            try:
                import json

                models_dict = json.loads(env_models)
                # Cache the full model dict with descriptions for lookup later
                self._env_gemini_models = models_dict
                # Convert to the expected format {category: [model_ids]}
                return self._static_models_to_dict(models_dict)
            except (json.JSONDecodeError, ValueError) as e:
                print(
                    f"Warning: Failed to parse GEMINI_MODELS_JSON: {e}", file=sys.stderr
                )

        # Fallback to static configuration
        return self._static_models_to_dict(self.GEMINI_MODELS)

    def fetch_codex_models(self) -> Dict:
        """Return available Codex models from environment or fallback to static list.

        Codex CLI does not currently expose a model-listing subcommand.
        Models are read from CODEX_MODELS_JSON environment variable, with
        static CODEX_MODELS as fallback.
        """
        # Try to load from environment variable first
        env_models = os.getenv("CODEX_MODELS_JSON")
        if env_models:
            try:
                import json

                models_dict = json.loads(env_models)
                # Cache the full model dict with descriptions for lookup later
                self._env_codex_models = models_dict
                # Convert to the expected format {category: [model_ids]}
                return self._static_models_to_dict(models_dict)
            except (json.JSONDecodeError, ValueError) as e:
                print(
                    f"Warning: Failed to parse CODEX_MODELS_JSON: {e}", file=sys.stderr
                )

        # Fallback to static configuration
        return self._static_models_to_dict(self.CODEX_MODELS)

    def fetch_devin_models(self) -> Dict:
        """Return available Devin models by querying the CLI directly.

        Devin prints available models when given an invalid model name:
          devin --model __invalid__ -p -- ""
          → Error: Unknown model: '__invalid__'
          → Available: claude-sonnet-4, claude-opus-4.6, ...

        Falls back to static DEVIN_MODELS if the CLI is unavailable or
        the output cannot be parsed.
        """
        try:
            devin_bin = getattr(self, "devin_bin", None) or "devin"
            result = subprocess.run(
                [devin_bin, "--model", "__invalid__", "-p", "--", ""],
                capture_output=True,
                text=True,
                timeout=10,
            )
            # Parse "Available: model1, model2, ..." from stderr or stdout
            output = result.stderr + result.stdout
            for line in output.splitlines():
                if line.strip().startswith("Available:"):
                    models_str = line.split(":", 1)[1].strip()
                    model_ids = [m.strip() for m in models_str.split(",") if m.strip()]
                    if model_ids:
                        # Group into a single category for display
                        discovered = {
                            "Available Models": [(mid, mid, []) for mid in model_ids]
                        }
                        print(
                            f"[devin] Auto-discovered {len(model_ids)} models",
                            file=sys.stderr,
                        )
                        return self._static_models_to_dict(discovered)
        except Exception as e:
            print(
                f"[devin] Model discovery failed, using static list: {e}",
                file=sys.stderr,
            )

        return self._static_models_to_dict(self.DEVIN_MODELS)

    def fetch_cursor_models(self) -> Dict:
        """Return available Cursor models by querying the agent CLI directly.

        Runs `agent --list-models` which outputs model IDs one per line
        (format: "model-id - Display Name" or just "model-id").
        Falls back to static CURSOR_MODELS if the CLI is unavailable.
        """
        try:
            cursor_bin = getattr(self, "cursor_bin", None) or "agent"
            result = subprocess.run(
                [cursor_bin, "--list-models"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            output = (result.stdout or "") + (result.stderr or "")
            # Strip ANSI escape sequences from entire output before parsing
            output = re.sub(r"\[[0-9;]*[a-zA-Z]", "", output)
            output = re.sub(r"\][^]*", "", output)
            model_ids = []
            for line in output.splitlines():
                line = line.strip()
                if (
                    not line
                    or line.startswith("#")
                    or "Available" in line
                    or "Loading" in line
                ):
                    continue
                # Handle "model-id - Display Name" or bare "model-id"
                model_id = (
                    line.split(" - ")[0].strip()
                    if " - " in line
                    else line.split(",")[0].strip()
                )
                if (
                    model_id
                    and not model_id.startswith("-")
                    and re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$", model_id)
                ):
                    model_ids.append(model_id)
            if model_ids:
                discovered = {"Available Models": [(mid, mid, []) for mid in model_ids]}
                print(
                    f"[cursor] Auto-discovered {len(model_ids)} models",
                    file=sys.stderr,
                )
                self._env_cursor_models = discovered
                return self._static_models_to_dict(discovered)
        except Exception as e:
            print(
                f"[cursor] Model discovery failed, using static list: {e}",
                file=sys.stderr,
            )

        return self._static_models_to_dict(self.CURSOR_MODELS)

    def _fetch_ollama_models_live(self) -> list:
        """Discover live Ollama model names with a short TTL cache.

        Issue #124: Replace hardcoded 3-model list with live discovery from kubuntu.
        """
        import time as _time
        import httpx

        ollama_ttl = 60
        if not hasattr(self, "_ollama_models_cache"):
            self._ollama_models_cache = []
        if not hasattr(self, "_ollama_cache_ts"):
            self._ollama_cache_ts = 0.0
        if self._ollama_models_cache and _time.time() - self._ollama_cache_ts < ollama_ttl:
            return self._ollama_models_cache

        ollama_url = os.environ.get("WEE_OLLAMA_HOST", "http://192.168.1.101:11434") + "/api/tags"
        try:
            resp = httpx.get(ollama_url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            names = [m["name"] for m in data.get("models", []) if m.get("name")]
            self._ollama_models_cache = names
            self._ollama_cache_ts = _time.time()
            print(
                f"[wee] Ollama: discovered {len(names)} models from {ollama_url}",
                file=sys.stderr,
            )
            return names
        except Exception as e:
            print(f"[wee] Ollama discovery failed ({ollama_url}): {e}", file=sys.stderr)
            # Return cached data even if stale, or empty list
            return self._ollama_models_cache or []


    def fetch_wee_models(self) -> Dict:
        """Return available wee models: local Ollama (live) + OpenRouter cloud models.

        Issue #124: Ollama models are fetched live from kubuntu (60s TTL cache).
        OpenRouter models are fetched live and cached for 300s.
        Falls back to the static WEE_MODELS list on any error.
        """
        import time as _time

        or_cache_ttl = 300  # 5 minutes for OpenRouter

        # Return cache if still valid (OpenRouter cache governs full refresh)
        if (
            self._env_wee_models is not None
            and _time.time() - self._openrouter_cache_ts < or_cache_ttl
        ):
            # Even when OR cache is valid, refresh Ollama section if its 60s TTL expired
            ollama_names = self._fetch_ollama_models_live()
            if ollama_names:
                _static_ollama = {
                    mid: (desc, aliases)
                    for mid, desc, aliases in self.WEE_MODELS.get("Wee Native (Ollama)", [])
                }
                ollama_entries = []
                for name in ollama_names:
                    oid = f"ollama/{name}"
                    if oid in _static_ollama:
                        desc, aliases = _static_ollama[oid]
                    else:
                        desc, aliases = f"Ollama {name} (local)", []
                    ollama_entries.append((oid, desc, aliases))
                self._env_wee_models["Wee Native (Ollama)"] = ollama_entries
            return self._static_models_to_dict(self._env_wee_models)

        # Build fresh result: live Ollama + static OpenRouter stubs as fallback
        result = {}

        # Issue #124: Live Ollama discovery replaces hardcoded 3-model list
        ollama_names = self._fetch_ollama_models_live()
        if ollama_names:
            # Build lookup from static WEE_MODELS to preserve curated descriptions
            _static_ollama = {
                mid: (desc, aliases)
                for mid, desc, aliases in self.WEE_MODELS.get("Wee Native (Ollama)", [])
            }
            ollama_entries = []
            for name in ollama_names:
                oid = f"ollama/{name}"
                if oid in _static_ollama:
                    desc, aliases = _static_ollama[oid]
                else:
                    desc, aliases = f"Ollama {name} (local)", []
                ollama_entries.append((oid, desc, aliases))
            result["Wee Native (Ollama)"] = ollama_entries
        else:
            # Fall back to static Ollama entries if discovery fails
            result["Wee Native (Ollama)"] = list(
                self.WEE_MODELS.get("Wee Native (Ollama)", [])
            )

        # Copy static OpenRouter entries as initial values (overwritten below if live succeeds)
        for cat, entries in self.WEE_MODELS.items():
            if "Ollama" not in cat:
                result[cat] = list(entries)
        if "Wee Native (OpenRouter)" in result:
            result["OpenRouter Models"] = list(result["Wee Native (OpenRouter)"])

        # Try live OpenRouter discovery
        try:
            api_key = None
            try:
                import keyring

                api_key = keyring.get_password("openrouter", "api_key")
            except Exception:
                pass
            if not api_key:
                api_key = os.environ.get("OPENROUTER_API_KEY")

            if not api_key:
                print(
                    "[wee] No OpenRouter API key -- using static list", file=sys.stderr
                )
                static_result = self._static_models_to_dict(self.WEE_MODELS)
                if "Wee Native (OpenRouter)" in static_result:
                    static_result["OpenRouter Models"] = list(
                        static_result["Wee Native (OpenRouter)"]
                    )
                return static_result

            import urllib.request

            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": "Bearer " + api_key},
            )
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())
            all_models = data.get("data", [])

            discovered = []
            for m in all_models:
                mid = m.get("id", "")
                if mid in self.OPENROUTER_POPULAR_MODELS:
                    name = m.get("name", mid)
                    or_id = "openrouter/" + mid
                    discovered.append((or_id, name + " (OpenRouter)", []))

            if discovered:
                discovered.sort(key=lambda t: t[1])
                result["Wee Native (OpenRouter)"] = discovered
                result["OpenRouter Models"] = list(discovered)
                print(
                    "[wee] OpenRouter: discovered %d models" % len(discovered),
                    file=sys.stderr,
                )

            self._env_wee_models = result
            self._openrouter_cache_ts = _time.time()
            return self._static_models_to_dict(result)

        except Exception as e:
            print(
                "[wee] OpenRouter discovery failed: %s — using live Ollama + static OR list" % e,
                file=sys.stderr,
            )
            # Return result with live Ollama (already populated) + static OpenRouter fallback
            self._env_wee_models = result
            self._openrouter_cache_ts = _time.time()
            return self._static_models_to_dict(result)

    def fetch_ollama_models(self) -> Dict:
        """Fetch available ollama models from the model manifest."""
        try:
            with open(MODEL_MANIFEST_PATH, "r") as f:
                manifest = json.load(f)
                ollama_models = manifest.get("runtimes", {}).get("ollama", [])
                if ollama_models:
                    return {"Ollama": ollama_models}
        except Exception as e:
            print(
                f"[Warning] Failed to read ollama models from manifest: {e}",
                file=sys.stderr,
            )
        return {}

    def get_models_for_runtime(self, runtime: str) -> Dict:
        """Fetch available models for a runtime, using CLI discovery where possible.

        Returns {category: [model_id, ...]} with CLI-discovered models preferred
        and a static list used as fallback when the runtime CLI is unavailable
        or does not support model listing.
        """
        dispatch = {
            "copilot": self.fetch_copilot_models,
            "copilot-sdk": self.fetch_copilot_models,
            "claude-sdk": self.fetch_claude_models,
            "opencode": self.fetch_opencode_models,
            "claude": self.fetch_claude_models,
            "gemini": self.fetch_gemini_models,
            "codex": self.fetch_codex_models,
            "devin": self.fetch_devin_models,
            "cursor": self.fetch_cursor_models,
            "wee": self.fetch_wee_models,
            "ollama": self.fetch_ollama_models,
        }
        fetcher = dispatch.get(runtime)
        if fetcher is None:
            print(
                f"[Warning] Unknown runtime for model listing: {runtime}",
                file=sys.stderr,
            )
            return {}
        return fetcher()

    def load_session_map(self) -> Dict:
        """Load the N8N -> Session ID mapping"""
        if not self.session_map_file.exists():
            return {}

        try:
            with open(self.session_map_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def save_session_map(self, session_map: dict):
        """Save the N8N -> Session ID mapping (caller must hold _session_map_lock)"""
        with open(self.session_map_file, "w") as f:
            json.dump(session_map, f, indent=2)

    def load_session_data(self, n8n_session_id: str) -> Optional[Dict]:
        """
        Load existing session data without creating new ones.
        Returns session data dict or None if not found.
        Thread-safe: acquires lock to prevent race conditions.
        """
        with self._session_map_lock:
            session_map = self.load_session_map()
            return session_map.get(n8n_session_id)

    def _extract_bot_identifier(self, session_id: str) -> str:
        """Extract bot identifier (last 4 chars of numeric part) from session ID"""
        # Format: "telegram_<user_id>" or "webex_<connector_id>" or other
        if "_" in session_id:
            parts = session_id.split("_")
            if len(parts) >= 2:
                numeric_part = parts[-1]
                # Extract last 4 characters or full if less than 4
                return numeric_part[-4:] if len(numeric_part) >= 4 else numeric_part
        return session_id[-4:] if len(session_id) >= 4 else session_id

    def get_or_create_session_data(
        self, n8n_session_id: str, identity: Optional[str] = None
    ) -> Dict:
        """
        Get existing session data or create new default
        Returns dict with keys: session_id, model, agent, runtime, bot_id, channel
        """
        with self._session_map_lock:
            return self._get_or_create_session_data_unlocked(n8n_session_id, identity)

    def _get_or_create_session_data_unlocked(
        self, n8n_session_id: str, identity: Optional[str] = None
    ) -> Dict:
        """Internal: get or create session data (caller must hold _session_map_lock)"""
        session_map = self.load_session_map()

        default_runtime = get_default_runtime()
        default_model = get_default_model()

        # Adjust default model based on runtime if using defaults
        if default_runtime == "claude":
            default_model = "haiku"
        elif default_runtime == "opencode":
            default_model = "opencode/gpt-5-nano"
        elif default_runtime == "gemini":
            default_model = "gemini-1.5-flash"
        elif default_runtime == "codex":
            default_model = "gpt-5.4"
        elif default_runtime == "devin":
            default_model = os.getenv("DEVIN_DEFAULT_MODEL", "claude-sonnet-4")
        elif default_runtime == "cursor":
            default_model = os.getenv("CURSOR_DEFAULT_MODEL", "auto")

        # Extract bot identifier from session ID (last 4 chars of numeric part)
        bot_id = self._extract_bot_identifier(n8n_session_id)

        # Auto-detect channel from session ID prefix
        channel = "webui"
        if n8n_session_id.startswith("webex_"):
            channel = "webex"
        elif n8n_session_id.startswith("telegram_"):
            channel = "telegram"

        default_data = {
            "session_id": str(uuid4()),
            "model": default_model,
            "agent": get_default_agent(),
            "runtime": default_runtime,
            "bot_id": bot_id,
            "render_type": "markdown",
            "channel": channel,
            "last_activity": time.time(),
            "permissions": None,  # Inherited from agent config on session create
            "silent_mode": _resolve_silent_default(channel),  # F026
        }

        # Store identity if provided, so we can find sessions by user later
        if identity:
            default_data["identity"] = identity

        if n8n_session_id not in session_map:
            # Create new session and save it immediately
            session_map[n8n_session_id] = default_data
            self.save_session_map(session_map)
            return {**default_data, "is_new": True}

        data = session_map[n8n_session_id]
        # Normalize old format (string ID or dict without runtime)
        if isinstance(data, str):
            normalized = {**default_data, "session_id": data, "is_new": False}
            session_map[n8n_session_id] = normalized
            self.save_session_map(session_map)
            return normalized
        elif isinstance(data, dict):
            # Ensure all fields exist
            merged = {**default_data, **data}

            # Backfill identity for existing sessions that lack it
            if identity and not merged.get("identity"):
                merged["identity"] = identity

            # If the runtime is set but model isn't (or is wrong for the runtime),
            # set a model appropriate for that runtime
            runtime = merged.get("runtime", default_runtime)
            if runtime == "claude":
                if not merged.get("model") or "gpt" in merged.get("model", "").lower():
                    merged["model"] = "haiku"
            elif runtime == "opencode":
                # For opencode, only force default if model is truly empty.
                # Allow any non-empty model string (opencode/*, openai-compatible/*, etc.)
                if not merged.get("model"):
                    merged["model"] = "opencode/gpt-5-nano"
            elif runtime == "gemini":
                if (
                    not merged.get("model")
                    or "gemini" not in merged.get("model", "").lower()
                ):
                    merged["model"] = "gemini-1.5-flash"
            elif runtime == "codex":
                current_model = merged.get("model", "")
                # Accept any model that resolves via codex model metadata/aliases.
                # This avoids clobbering valid models like "gpt-5.4" that do not
                # include the "codex" substring.
                if not current_model or not self.get_model_from_name(
                    current_model, "codex"
                ):
                    merged["model"] = "gpt-5.4"
            elif runtime == "devin":
                current_model = merged.get("model", "")
                if not current_model or not self.get_model_from_name(
                    current_model, "devin"
                ):
                    merged["model"] = os.getenv(
                        "DEVIN_DEFAULT_MODEL", "claude-sonnet-4"
                    )
            elif runtime == "cursor":
                current_model = merged.get("model", "")
                if not current_model or not self.get_model_from_name(
                    current_model, "cursor"
                ):
                    merged["model"] = os.getenv("CURSOR_DEFAULT_MODEL", "auto")
            elif runtime == "wee":
                if not merged.get("model"):
                    merged["model"] = os.getenv(
                        "WEE_DEFAULT_MODEL", "ollama/gemma4:e4b"
                    )

            # Validate and fix session_id if corrupted
            session_id = merged.get("session_id", "")
            if runtime in [
                "claude",
                "claude-sdk",
                "gemini",
                "codex",
                "copilot",
                "copilot-sdk",
                "devin",
                "cursor",
                "wee",
            ]:
                if not session_id or not (len(session_id) == 36 and "-" in session_id):
                    merged["session_id"] = str(uuid4())
            elif runtime == "opencode":
                if not session_id or not session_id.startswith("ses_"):
                    merged["session_id"] = str(uuid4())

            # Ensure bot_id is set
            if "bot_id" not in merged:
                merged["bot_id"] = bot_id

            # Save back if changed
            if merged != data:
                session_map[n8n_session_id] = merged
                self.save_session_map(session_map)
            merged["is_new"] = False
            return merged

        # Fallback: new session
        session_map[n8n_session_id] = default_data
        self.save_session_map(session_map)
        print(
            f"[Session] Created new session: {default_data['session_id']} (N8N: {n8n_session_id}, Bot: {bot_id})",
            file=sys.stderr,
        )
        return {**default_data, "is_new": True}

    def update_session_field(self, n8n_session_id: str, field: str, value):
        """Update a specific field in the session map (thread-safe)"""
        with self._session_map_lock:
            session_map = self.load_session_map()

            if n8n_session_id not in session_map:
                # Create new if doesn't exist
                self._get_or_create_session_data_unlocked(n8n_session_id)
                session_map = self.load_session_map()

            # Guard against race-condition where key is still missing after create
            if n8n_session_id not in session_map:
                return

            if isinstance(session_map[n8n_session_id], str):
                # Convert old string format to dict
                session_map[n8n_session_id] = {
                    "session_id": session_map[n8n_session_id],
                    "model": get_default_model(),
                    "agent": get_default_agent(),
                    "runtime": get_default_runtime(),
                }

            session_map[n8n_session_id][field] = value
            self.save_session_map(session_map)

    def touch_session(self, n8n_session_id: str) -> None:
        """Update the last_activity timestamp for a session.

        Called before and after each operation so that the cleanup daemon
        and any future idle-timeout logic can distinguish live sessions
        from abandoned ones.  Also touches the backend session-state
        directory (if it exists) to reset its mtime, preventing the
        cleanup daemon from treating it as stale.
        """
        now = time.time()
        with self._session_map_lock:
            session_map = self.load_session_map()
            entry = session_map.get(n8n_session_id)
            if entry and isinstance(entry, dict):
                entry["last_activity"] = now
                self.save_session_map(session_map)

                # Touch the backend session-state directory to reset mtime
                backend_sid = entry.get("session_id")
                if backend_sid:
                    backend_dir = self.session_state_dir / backend_sid
                    if backend_dir.exists():
                        try:
                            backend_dir.stat()  # read
                            os.utime(backend_dir, (now, now))
                        except OSError:
                            pass

    def get_effective_timeout(self, session_data: dict) -> int:
        """Get the effective timeout for a session (session-specific or default)"""
        session_timeout = session_data.get("timeout")
        if session_timeout:
            try:
                return int(session_timeout)
            except ValueError:
                pass
        return self.command_timeout

    def get_render_type(self, session_data: dict) -> str:
        """Get the render type for a session (session-specific or default)"""
        return session_data.get("render_type", "text")

    def validate_telegram_html(self, text: str) -> Tuple[bool, str]:
        """
        Validate that text only uses Telegram-supported HTML tags.
        Returns (is_valid, error_message)
        """
        import re

        # Supported tags in Telegram HTML mode
        supported_tags = {
            "b",
            "strong",
            "i",
            "em",
            "u",
            "ins",
            "s",
            "strike",
            "del",
            "span",
            "a",
            "code",
            "pre",
            "blockquote",
            "tg-spoiler",
            "tg-emoji",
        }

        # Find all HTML-like tags in the text
        # Pattern: <tag_name ...> or <tag_name>
        tag_pattern = r"</?([a-zA-Z][a-zA-Z0-9\-]*)"
        matches = re.finditer(tag_pattern, text)

        unsupported_tags = set()
        for match in matches:
            tag_name = match.group(1).lower()
            # For span with class, check if it's tg-spoiler
            if (
                tag_name == "span"
                and "tg-spoiler" in text[match.start() : match.start() + 50]
            ):
                continue
            if tag_name not in supported_tags:
                unsupported_tags.add(tag_name)

        if unsupported_tags:
            return (
                False,
                f"Unsupported HTML tags for Telegram: {', '.join(sorted(unsupported_tags))}",
            )

        return True, ""

    def sanitize_telegram_html(self, text: str) -> str:
        """
        Remove or escape unsupported HTML tags for Telegram compatibility.
        This is a fallback when the model generates unsupported tags.

        Escapes:
        - Double angle brackets (<<, >>) used in bash/scripting
        - Unsupported HTML tags
        Preserves:
        - Supported Telegram HTML tags
        """
        import re

        # Supported tags
        supported_tags = {
            "b",
            "strong",
            "i",
            "em",
            "u",
            "ins",
            "s",
            "strike",
            "del",
            "span",
            "a",
            "code",
            "pre",
            "blockquote",
            "tg-spoiler",
            "tg-emoji",
        }

        # First, escape double angle brackets (<<EOF, >>, etc.) that are used in scripting
        # Replace << with &lt;&lt; and >> with &gt;&gt;
        text = text.replace("<<", "&lt;&lt;")
        text = text.replace(">>", "&gt;&gt;")

        # Pattern to find HTML-like tags (single < followed by tag name, not double)
        # This won't match &lt; or already-escaped sequences
        def replace_tag(match):
            tag_full = match.group(0)
            tag_name = match.group(1).lower()

            # Check if this is a supported tag
            if tag_name in supported_tags:
                return tag_full  # Keep supported tags

            # For unsupported tags, convert to escaped text or remove
            # If it's a closing tag, just remove it
            if tag_full.startswith("</"):
                return ""

            # For opening tags, escape the angle brackets
            return tag_full.replace("<", "&lt;").replace(">", "&gt;")

        # Replace all tags - only match single < not preceded by &
        # This matches proper HTML tags but not &lt; or <<
        result = re.sub(r"(?<!&)</?([a-zA-Z][a-zA-Z0-9\-:]*)[^>]*>", replace_tag, text)
        return result

    def get_capabilities(self) -> str:
        """Get available capabilities based on configured agents"""
        if not self.AGENTS:
            return "No agents configured. Add agents to agents.json to extend capabilities."

        out = "# 🤖 Orchestrator Capabilities\n\n"
        out += "I can help with the following agents:\n\n"
        for agent_name, agent_info in self.AGENTS.items():
            description = agent_info.get("description", "No description")
            path = agent_info.get("path", "")
            out += f"### {agent_name}\n- **Description:** {description}\n- **Location:** `{path}`\n\n"
        out += "#### How to use\n"
        out += "- `/agent set <agent_name>` — switch to an agent and work with it.\n"
        out += "- `/agent list` — show all available agents and their locations.\n"

        return out

    def set_agent(self, n8n_session_id: str, agent: str) -> str:
        """Switch to a different agent"""
        if agent not in self.AGENTS:
            available = ", ".join(self.AGENTS.keys())
            return f"Unknown agent: '{agent}'. Available agents: {available}"

        # Get agent's primary runtime/model defaults (issue #249)
        agent_config = self.AGENTS[agent]
        primary_runtime = agent_config.get("primary_runtime") or "copilot"
        primary_model = agent_config.get("primary_model") or "gpt-5-mini"

        with self._session_map_lock:
            session_map = self.load_session_map()

            # Generate a new session ID for the backend because sessions are often project-scoped
            new_backend_session_id = str(uuid4())

            if n8n_session_id not in session_map:
                session_map[n8n_session_id] = {
                    "session_id": new_backend_session_id,
                    "model": primary_model,
                    "agent": agent,
                    "runtime": primary_runtime,
                }
            else:
                if isinstance(session_map[n8n_session_id], dict):
                    session_map[n8n_session_id]["agent"] = agent
                    session_map[n8n_session_id]["session_id"] = new_backend_session_id
                    session_map[n8n_session_id]["runtime"] = primary_runtime
                    session_map[n8n_session_id]["model"] = primary_model
                else:
                    # Convert old format
                    session_map[n8n_session_id] = {
                        "session_id": new_backend_session_id,
                        "model": primary_model,
                        "agent": agent,
                        "runtime": primary_runtime,
                    }

            self.save_session_map(session_map)
        agent_info = self.AGENTS[agent]
        print(
            f"[Agent] Switched to '{agent}' agent. New backend session: {new_backend_session_id}",
            file=sys.stderr,
        )
        return f"✓ Switched to **{agent}** agent\n\n{agent_info['description']}\n\nLocation: `{agent_info['path']}`"

    def detect_agent_delegation(self, prompt: str) -> Tuple[Optional[str], str]:
        """Detect if user is asking for a specific agent to help with something

        Patterns detected:
        - "ask the family agent..."
        - "have the devops agent..."
        - "this is in the family agent"
        - "in the projects agent..."
        - "from the family agent..."

        Returns: (agent_name, modified_prompt) or (None, original_prompt)
        """

        prompt_lower = prompt.lower()

        # List of agent names to detect
        agent_keywords = {
            "family": ["family agent", "family knowledge"],
            "devops": ["devops agent", "devops"],
            "projects": ["projects agent", "projects"],
            "orchestrator": ["orchestrator agent", "orchestrator"],
        }

        # Delegation phrases
        delegation_phrases = [
            "ask the",
            "have the",
            "this is in the",
            "in the",
            "from the",
            "use the",
            "check the",
            "find in the",
            "search the",
        ]

        # Check if prompt contains delegation request
        for agent_name, keywords in agent_keywords.items():
            for keyword in keywords:
                if keyword in prompt_lower:
                    # Check if it's a delegation request
                    for phrase in delegation_phrases:
                        pattern = f"{phrase} {keyword}"
                        if pattern in prompt_lower:
                            # Extract just the actual question part
                            # Remove the "ask the family agent" part
                            modified = re.sub(
                                rf"\b{phrase}\s+{re.escape(keyword)}[,.]?\s*",
                                "",
                                prompt,
                                flags=re.IGNORECASE,
                            )
                            return agent_name, modified

        return None, prompt

    def parse_slash_command(self, prompt: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse slash commands from the prompt."""
        if not prompt.startswith("/"):
            return None, None

        parts = prompt.split(None, 1)
        command = parts[0].lower()
        argument = parts[1] if len(parts) > 1 else None

        return command, argument

    def get_model_from_name(self, name: str, runtime: str) -> Optional[str]:
        """Convert model name/alias to full model ID based on runtime.

        Resolution order:
          1. Check env-loaded or static alias tables (contain alias/description metadata).
          2. Fall back to CLI-discovered model list (exact then substring match).
        """
        name_lower = name.lower().strip("\"'")

        # Ensure env models are loaded/cached by triggering fetch for this runtime
        if runtime in ("claude", "gemini", "codex", "devin", "cursor"):
            self.get_models_for_runtime(runtime)

        # Step 1: check env-loaded or static alias tables for all runtimes that have them.
        env_alias_map = {
            "claude": self._env_claude_models,
            "gemini": self._env_gemini_models,
            "codex": self._env_codex_models,
            "devin": self._env_devin_models,
            "cursor": self._env_cursor_models,
            "wee": self._env_wee_models,
        }
        static_alias_map = {
            "claude": self.CLAUDE_MODELS,
            "gemini": self.GEMINI_MODELS,
            "codex": self.CODEX_MODELS,
            "opencode": self.OPENCODE_MODELS,
            "devin": self.DEVIN_MODELS,
            "cursor": self.CURSOR_MODELS,
            "wee": self.WEE_MODELS,
        }

        # Try env-loaded models first, fall back to static
        models_to_check = env_alias_map.get(runtime) or static_alias_map.get(runtime)

        if runtime in static_alias_map and models_to_check:
            for _category, entries in models_to_check.items():
                for model_id, desc, aliases in entries:
                    aliases_lower = [a.lower() for a in aliases]
                    if (
                        name_lower == model_id.lower()
                        or name_lower == desc.lower()
                        or name_lower in aliases_lower
                    ):
                        return model_id

        # Step 2: dynamic CLI discovery for all runtimes.
        models_dict = self.get_models_for_runtime(runtime)
        all_models = [m for sublist in models_dict.values() for m in sublist]

        # Exact match (case insensitive)
        for m in all_models:
            if m.lower() == name_lower:
                return m

        # Substring matching with longest-match preference
        matches = [m for m in all_models if name_lower in m.lower()]
        
        # Issue #142 B01: For wee runtime, exclude multi-namespace models from substring
        # matching (e.g., "openrouter/openai/gpt-5-mini" has 2+ slashes = multi-namespace)
        if runtime == "wee" and matches:
            single_ns = [m for m in matches if m.count("/") == 1]
            # If multi-namespace models exist, don't use any substring matches for wee
            has_multi_ns = any(m.count("/") >= 2 for m in matches)
            if has_multi_ns:
                # Don't use substring matching when multi-namespace models are present
                matches = single_ns if single_ns else []
        
        if len(matches) == 1:
            return matches[0]
        if matches:
            matches.sort(reverse=True)
            return matches[0]

        return None

    def strip_thinking_tags(self, text: str) -> str:
        """Remove content within <think> tags"""
        # Remove complete think blocks
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        # Remove unclosed think blocks (from start of tag to end of string)
        text = re.sub(r"<think>.*", "", text, flags=re.DOTALL)
        return text.strip()

    def _parse_mode_command(self, prompt: str) -> tuple[str, str]:
        """Parse /mode command from prompt. Returns (cleaned_prompt, mode).

        Modes:
        - 'elevated': Full access, auto-approve all operations
        - 'restricted': Keep bounded to agent directory (default)
        - 'sandboxed': Read-only, no external access
        """
        # Check for /mode command at start or after newline
        mode_pattern = r"(?:^|\n)\s*/mode\s+(elevated|restricted|sandboxed)\s*(?:\n|$)"
        match = re.search(mode_pattern, prompt, re.IGNORECASE)

        if match:
            mode = match.group(1).lower()
            # Remove the command from prompt
            cleaned = re.sub(mode_pattern, "\n", prompt, flags=re.IGNORECASE).strip()
            return cleaned, mode

        return prompt, "restricted"  # default to restricted

    def _resolve_permission_mode(
        self, session_data: dict, prompt_mode: str = "restricted"
    ) -> str:
        """Resolve effective permission mode from session data with backward compatibility.

        Priority: prompt_mode (if not default) > permissions.mode > yolo_mode (legacy) > 'restricted'
        """
        if prompt_mode != "restricted":
            return prompt_mode
        perms = (
            session_data.get("permissions") or {}
        )  # Handle None from session template
        if isinstance(perms, dict) and perms.get("mode") in (
            "elevated",
            "restricted",
            "sandboxed",
        ):
            return perms["mode"]
        yolo = session_data.get("yolo_mode", "restricted")
        if yolo == "on":
            return "elevated"
        return "restricted"

    def build_runtime_permission_env(
        self, runtime: str, permission_mode: str
    ) -> dict:
        """Build runtime-specific environment variables for permission modes.

        Maps permission_mode to runtime-specific env vars/flags that need to be
        passed to subprocess environments. This allows devin, copilot, and other
        runtimes to inherit the elevated permissions in their child processes.

        Args:
            runtime: The runtime name (e.g., 'devin', 'copilot', 'claude-code')
            permission_mode: The permission mode ('elevated', 'restricted', 'sandboxed')

        Returns:
            A dict of environment variable updates to merge into subprocess env
        """
        env_updates = {}

        if permission_mode == "elevated":
            if runtime == "devin":
                env_updates["DEVIN_PERMISSION_MODE"] = "dangerous"
            elif runtime in ("claude-code", "claude-acp"):
                # Claude Code / ACP equivalent for elevated permissions
                # (May not have exact equivalent; document if needed)
                pass
            elif runtime == "cursor":
                # Cursor editor's permission handling (if applicable)
                pass

        return env_updates

    def strip_metadata(self, text: str, runtime: str) -> str:
        """Remove CLI metadata from output"""
        # Strip [STATUS_UPDATE: ...] markers (F004 — mobile channel progress)
        text = re.sub(r"\[STATUS_UPDATE[:\s]*[^\]]*\]\s*\n?", "", text)
        # First, strip thinking tags from the raw output
        text = self.strip_thinking_tags(text)

        lines = text.split("\n")
        result = []

        if runtime in ("copilot", "copilot-sdk"):
            in_metadata = False
            for line in lines:
                # Strip ANSI escape codes (may leak despite --no-color)
                line = re.sub(r"\x1b\[[0-9;]*m", "", line)
                if re.match(r"^Total usage est:|^Total duration", line):
                    in_metadata = True
                    continue
                if in_metadata:
                    continue
                # Strip tool call decoration lines (already captured during streaming)
                stripped_line = line.strip()
                if re.match(r"^[●⬤]\s+", stripped_line):
                    continue
                if re.match(r"^\$\s+", stripped_line):
                    continue
                if re.match(r"^└\s+\d+\s+lines?", stripped_line):
                    continue
                if re.match(r"^Breakdown by AI model:", stripped_line):
                    in_metadata = True
                    continue
                if re.match(r"^API time spent:", stripped_line):
                    in_metadata = True
                    continue
                if re.match(r"^Total session time:", stripped_line):
                    in_metadata = True
                    continue
                if re.match(r"^Total code changes:", stripped_line):
                    in_metadata = True
                    continue
                result.append(line)

        elif runtime == "opencode":
            skip_banner = True
            for line in lines:
                clean_line = re.sub(r"\x1b\[[0-9;]*m", "", line)

                # Skip banner/ASCII art
                if skip_banner and (
                    "█" in clean_line
                    or "▄" in clean_line
                    or (clean_line.strip() == "" and len(result) == 0)
                ):
                    continue
                skip_banner = False

                # Skip tool invocation lines (e.g., "|  Glob", "|  Read", "|  Write", etc.)
                if re.match(
                    r"^\|\s+(Glob|Read|Write|Bash|Edit|bash|grep|find)", clean_line
                ):
                    continue

                # Skip stats
                if any(
                    k in clean_line.lower()
                    for k in [
                        "tokens used:",
                        "total cost:",
                        "session id:",
                        "commands:",
                        "positionals:",
                        "options:",
                    ]
                ):
                    continue

                result.append(clean_line)

        elif runtime == "claude":
            import json as _json

            text_parts = []
            assistant_text = ""
            error_result = None
            has_rate_limit_event = False
            for line in lines:
                line_stripped = line.strip()
                if not line_stripped:
                    continue
                try:
                    obj = _json.loads(line_stripped)
                    obj_type = obj.get("type")
                    # Prefer the final result event for the complete text
                    if obj_type == "result":
                        result_text = obj.get("result", "")
                        if obj.get("is_error", False):
                            error_result = result_text
                        else:
                            return result_text
                    # Handle top-level API error events (e.g. rate limits, usage limits).
                    # These arrive as {"type":"error","error":{"type":"rate_limit_error","message":"..."}}
                    # and must be surfaced so is_limit_error() can detect them.
                    elif obj_type == "error":
                        err_obj = obj.get("error") or {}
                        err_msg = err_obj.get("message", "") or obj.get("message", "")
                        err_type = err_obj.get("type", "")
                        if err_msg:
                            error_result = (
                                f"API Error: {err_type} - {err_msg}"
                                if err_type
                                else f"API Error: {err_msg}"
                            )
                        elif err_type:
                            error_result = f"API Error: {err_type}"
                    # Handle rate_limit_event from Claude CLI (plan/usage cap reached)
                    elif obj_type == "rate_limit_event":
                        has_rate_limit_event = True
                        info = obj.get("rate_limit_info") or {}
                        status = info.get("status", "unknown")
                        limit_type = info.get("rateLimitType", "")
                        if status == "rejected":
                            error_result = (
                                f"rate_limit_event: {limit_type} limit reached "
                                f"(status={status})"
                            )
                    # Collect text deltas as fallback
                    elif obj_type == "stream_event":
                        event = obj.get("event") or {}
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta") or {}
                            if delta.get("type") == "text_delta":
                                text_parts.append(delta.get("text", ""))
                    # Extract text from assistant partial messages as last-resort fallback
                    elif obj_type == "assistant":
                        msg = obj.get("message") or {}
                        content = msg.get("content") or []
                        texts = [
                            c.get("text", "")
                            for c in content
                            if isinstance(c, dict) and c.get("type") == "text"
                        ]
                        if texts:
                            assistant_text = "\n\n".join(texts)
                        # If assistant event has an error field (e.g. "rate_limit"),
                        # treat the content as an error, not normal output
                        if obj.get("error"):
                            has_rate_limit_event = True
                except (ValueError, KeyError, AttributeError):
                    # Not JSON — treat as plain text (legacy fallback)
                    result.append(line)
            if text_parts:
                return "".join(text_parts)
            # When a rate limit or error was detected, prioritize error_result over
            # surface so the error signal reaches the caller.
            if has_rate_limit_event and error_result:
                return error_result
            if assistant_text:
                return assistant_text
            if error_result:
                return error_result

        elif runtime == "gemini":
            # Gemini may output stream-json (structured) or plain text.
            # For stream-json, extract text from "message" events with role="assistant".
            import json as _json_strip

            _has_json = False
            _text_parts = []
            for line in lines:
                line_stripped = line.strip()
                if line_stripped.startswith("{"):
                    try:
                        obj = _json_strip.loads(line_stripped)
                        _has_json = True
                        obj_type = obj.get("type", "")
                        if obj_type == "message" and obj.get("role") == "assistant":
                            content = obj.get("content", "")
                            if content:
                                _text_parts.append(content)
                        elif obj_type == "result":
                            pass  # skip stats
                        elif obj_type in ("tool_use", "tool_result", "init"):
                            pass  # skip tool events and init
                        continue
                    except (ValueError, KeyError):
                        pass
                # Plain text fallback
                line_lower = line.lower()
                if any(
                    pattern in line_lower
                    for pattern in [
                        "[startup]",
                        "recording metric for phase:",
                        "loaded cached credentials",
                        "session:",
                        "model:",
                        "tokens:",
                        "usage:",
                    ]
                ):
                    continue
                result.append(line)
            # If we found JSON, prefer the extracted text parts
            if _has_json and _text_parts:
                return "\n".join(_text_parts)

        elif runtime == "codex":
            import json as _json

            # v0.125.0+: output is JSONL events from --json flag
            # Extract text from item.completed events where item.type == "agent_message"
            jsonl_texts = []
            for _line in lines:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _event = _json.loads(_line)
                    if (
                        _event.get("type") == "item.completed"
                        and isinstance(_event.get("item"), dict)
                        and _event["item"].get("type") == "agent_message"
                    ):
                        _text = _event["item"].get("text", "")
                        if _text:
                            jsonl_texts.append(_text)
                except (ValueError, KeyError):
                    continue

            if jsonl_texts:
                # Use the last agent_message (most complete response)
                result.extend(jsonl_texts[-1].splitlines())
            else:
                # Fallback: legacy marker-based parsing for pre-v0.125.0 output
                found_codex_marker = False
                response_lines = []

                for i, line in enumerate(lines):
                    line_lower = line.lower()

                    if line_lower.strip() == "codex":
                        found_codex_marker = True
                        continue

                    if "tokens" in line_lower and "used" in line_lower:
                        break

                    if not found_codex_marker:
                        continue

                    if not line.strip() and not response_lines:
                        continue

                    response_lines.append(line)

                while response_lines and not response_lines[-1].strip():
                    response_lines.pop()

                result.extend(response_lines)

        elif runtime == "devin":
            # Devin CLI outputs the response directly to stdout.
            # Strip any leading/trailing whitespace lines.
            for line in lines:
                if not line.strip() and not result:
                    continue
                result.append(line)

        elif runtime == "cursor":
            # Cursor agent CLI outputs the response directly to stdout.
            # Strip ANSI codes, leading blanks, and any status lines.
            for line in lines:
                clean_line = re.sub(r"\x1b\[[0-9;]*m", "", line)
                if not clean_line.strip() and not result:
                    continue
                # Skip cursor-specific status/progress lines
                if re.match(r"^(Thinking|Working|\[cursor\])", clean_line.strip()):
                    continue
                result.append(clean_line)

        elif runtime == "wee":
            # Wee native runtime outputs clean text - pass through directly
            for line in lines:
                if not line.strip() and not result:
                    continue
                result.append(line)

        # Remove trailing empty lines
        while result and not result[-1].strip():
            result.pop()

        return "\n".join(result)

    def _execute_bash_command(self, command: str, agent: str = "orchestrator") -> str:
        """Execute a bash command directly without hitting any runtime

        Args:
            command: The bash command to execute (without the ! prefix)
            agent: The agent name whose directory to execute in

        Returns:
            The output from stdout/stderr, or an error message
        """
        if not command:
            return "Error: No command provided. Usage: !<command>"

        # Get agent directory
        agent_info = self.AGENTS.get(agent)
        if not agent_info:
            agent_dir = str(Path.cwd())
        else:
            agent_dir = agent_info["path"]

        print(f"[Shell] Executing in {agent_dir}: {command}", file=sys.stderr)

        try:
            # Execute the command with the configured timeout
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
                cwd=agent_dir,
            )

            # Combine stdout and stderr
            output = result.stdout
            if result.stderr:
                output += result.stderr

            # If there's no output, indicate success
            if not output.strip():
                if result.returncode == 0:
                    output = f"✓ Command executed successfully (exit code: 0)"
                else:
                    output = f"✗ Command failed with exit code: {result.returncode}"

            return output.strip()

        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {self.command_timeout} seconds"
        except Exception as e:
            return f"Error executing command: {str(e)}"

    def load_agent_skills(self, agent_path: str) -> str:
        """Load all SKILL.md files from agent's .github/skills/ directory.

        Looks for skills in this order:
        1. {agent_path}/.github/skills/
        2. {agent_path}/.claude/skills/

        Returns formatted skills context or empty string if no skills found.
        """
        skills_context = ""
        agent_path_obj = Path(agent_path)

        # Try both .github and .claude skill locations
        skill_dirs = [
            agent_path_obj / ".github" / "skills",
            agent_path_obj / ".claude" / "skills",
        ]

        available_skills = []

        for skill_dir in skill_dirs:
            if not skill_dir.exists():
                continue

            # Find all SKILL.md files
            skill_files = list(skill_dir.glob("*/SKILL.md"))

            for skill_file in skill_files:
                try:
                    content = skill_file.read_text()

                    # Extract name and description from YAML frontmatter
                    if content.startswith("---"):
                        parts = content.split("---")
                        if len(parts) >= 2:
                            frontmatter = parts[1]
                            # Simple YAML parsing
                            name = None
                            description = None
                            for line in frontmatter.split("\n"):
                                if line.startswith("name:"):
                                    name = (
                                        line.replace("name:", "").strip().strip("'\"")
                                    )
                                elif line.startswith("description:"):
                                    description = (
                                        line.replace("description:", "")
                                        .strip()
                                        .strip("'\"")
                                    )

                            if name:
                                available_skills.append(
                                    {
                                        "name": name,
                                        "description": description or "No description",
                                        "path": str(skill_file.parent),
                                    }
                                )
                except Exception as e:
                    print(
                        f"[WARN] Error loading skill {skill_file}: {e}", file=sys.stderr
                    )

        if available_skills:
            skills_context = "\n[Agent Skills - Available]\n"
            for skill in available_skills:
                skills_context += f"- {skill['name']}: {skill['description']}\n"
            skills_context += """
To use these skills, simply reference them in your work. The system will automatically load the appropriate skill instructions.

To add new skills to this agent:
1. Create a directory in .github/skills/{skill-name}/
2. Add a SKILL.md file with YAML frontmatter:
   ---
   name: skill-name
   description: What this skill does and when to use it
   ---

   # Skill Instructions
   Detailed instructions, guidelines, and examples...

3. Add supporting files (scripts, references, templates, assets)
4. Skills are auto-discovered on next session start

To get skills from Anthropic's official repository:
- Visit: https://github.com/anthropics/skills
- Clone skills you want to use
- Copy them to .github/skills/ or .claude/skills/
- Run: git clone https://github.com/anthropics/skills {agent_path}/.github/skills/anthropic-skills
"""
        else:
            skills_context = """
[Agent Skills - Setup Instructions]

You can add custom skills to this agent to extend its capabilities across all runtimes.

How to add skills:
1. Create a directory: {agent_path}/.github/skills/{skill-name}/
2. Add a SKILL.md file with YAML frontmatter:
   ---
   name: my-skill
   description: Brief description of what this skill does
   ---

   # Skill Instructions
   Detailed instructions for how to use this skill...

3. Optionally add supporting files:
   - scripts/: Executable scripts and code templates
   - references/: Documentation and guides
   - templates/: Starter code and configurations
   - assets/: Static files, images, etc.

4. Skills are auto-discovered on next session start

Getting skills from Anthropic's repository:
- Official skills: https://github.com/anthropics/skills
- Community skills: Look for repos tagged with "agent-skills"
- Clone and copy to .github/skills/ folder in this agent

Example skill structure:
  .github/skills/my-skill/
  ├── SKILL.md              # Required: skill definition
  ├── scripts/
  │   ├── helper.py
  │   └── setup.sh
  ├── references/
  │   └── api-docs.md
  └── templates/
      └── config-template.yaml
"""

        return skills_context

    def discover_skills(self, query: str = "", repository: Optional[str] = None) -> str:
        """Discover available skills from configured repositories.

        Searches skill repositories (Anthropic or custom) and returns available skills.

        Args:
            query: Optional search term to filter skills (e.g., "helm", "kubernetes")
            repository: Optional repository name to search in specific repo (e.g., "Anthropic Official")

        Returns:
            Formatted string listing available skills or error message
        """
        try:
            import subprocess

            # Determine which repositories to search
            repos_to_search = []
            if repository:
                # Search specific repository
                repos_to_search = [
                    r for r in self.skill_repositories if r.get("name") == repository
                ]
                if not repos_to_search:
                    return f"Error: Repository '{repository}' not found. Available: {', '.join(r.get('name') for r in self.skill_repositories)}"
            else:
                # Search all repositories
                repos_to_search = self.skill_repositories

            all_skills = []

            for repo in repos_to_search:
                repo_url = repo.get("url")
                repo_name = repo.get("name")
                temp_dir = (
                    f"/tmp/skills-discovery-{repo_name.lower().replace(' ', '-')}"
                )

                try:
                    # Clean up old temp directory
                    subprocess.run(
                        ["rm", "-rf", temp_dir], capture_output=True, timeout=5
                    )

                    # Clone the repository (shallow clone for speed)
                    result = subprocess.run(
                        ["git", "clone", "--depth", "1", repo_url, temp_dir],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )

                    if result.returncode != 0:
                        print(
                            f"[Warn] Could not access {repo_name} repository",
                            file=sys.stderr,
                        )
                        continue

                    # List available skills in this repository
                    skills_dir = Path(temp_dir)

                    for skill_dir in skills_dir.iterdir():
                        if skill_dir.is_dir() and not skill_dir.name.startswith("."):
                            readme = skill_dir / "README.md"
                            if readme.exists():
                                try:
                                    content = readme.read_text()
                                    # Extract first line as description
                                    lines = content.split("\n")
                                    desc = next(
                                        (
                                            line.strip("# ").strip()
                                            for line in lines
                                            if line.strip()
                                        ),
                                        "No description",
                                    )

                                    if (
                                        not query
                                        or query.lower() in skill_dir.name.lower()
                                        or query.lower() in desc.lower()
                                    ):
                                        all_skills.append(
                                            {
                                                "name": skill_dir.name,
                                                "description": desc[:100],
                                                "repository": repo_name,
                                            }
                                        )
                                except Exception:
                                    pass

                    # Clean up
                    subprocess.run(
                        ["rm", "-rf", temp_dir], capture_output=True, timeout=5
                    )

                except Exception as e:
                    print(f"[Warn] Error searching {repo_name}: {e}", file=sys.stderr)
                    continue

            if not all_skills:
                return f"No skills found matching '{query}'."

            # Format results grouped by repository
            result_text = (
                f"Available skills {f'(matching \"{query}\")' if query else ''}:\n\n"
            )
            current_repo = None
            for skill in sorted(
                all_skills, key=lambda x: (x.get("repository"), x.get("name"))
            ):
                if skill.get("repository") != current_repo:
                    current_repo = skill.get("repository")
                    result_text += f"\n**{current_repo}:**\n"
                result_text += f"  • {skill['name']} - {skill['description']}\n"

            result_text += (
                f"\nTo load any skill, use: /load-skill <skill-name> [repository-name]"
            )
            return result_text

        except Exception as e:
            return f"Error discovering skills: {str(e)}"

    def load_skill(
        self,
        skill_name: str,
        agent: str = "orchestrator",
        repository: Optional[str] = None,
    ) -> str:
        """Load a skill from configured repositories into the agent's .github/skills directory.

        Args:
            skill_name: Name of the skill to load (e.g., "helm-deploy")
            agent: Agent to load the skill into (default: orchestrator)
            repository: Optional repository name to search in (if None, searches all repositories)

        Returns:
            Status message indicating success or failure
        """
        try:
            import shutil
            import subprocess

            if agent not in self.AGENTS:
                return f"Error: Unknown agent '{agent}'. Available agents: {', '.join(self.AGENTS.keys())}"

            agent_path = Path(self.AGENTS[agent]["path"])
            skills_dir = agent_path / ".github" / "skills"
            skill_target = skills_dir / skill_name

            # Check if skill already exists
            if skill_target.exists():
                return f"✓ Skill '{skill_name}' is already loaded in {agent}."

            # Create skills directory if it doesn't exist
            skills_dir.mkdir(parents=True, exist_ok=True)

            # Determine which repositories to search
            repos_to_search = []
            if repository:
                repos_to_search = [
                    r for r in self.skill_repositories if r.get("name") == repository
                ]
                if not repos_to_search:
                    return f"Error: Repository '{repository}' not found. Available: {', '.join(r.get('name') for r in self.skill_repositories)}"
            else:
                repos_to_search = self.skill_repositories

            # Try to find and load the skill from one of the repositories
            for repo in repos_to_search:
                repo_url = repo.get("url")
                repo_name = repo.get("name")
                temp_dir = f"/tmp/skills-load-{repo_name.lower().replace(' ', '-')}"

                try:
                    # Clean up old temp directory
                    subprocess.run(
                        ["rm", "-rf", temp_dir], capture_output=True, timeout=5
                    )

                    # Clone repository (shallow clone)
                    result = subprocess.run(
                        ["git", "clone", "--depth", "1", repo_url, temp_dir],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )

                    if result.returncode != 0:
                        print(
                            f"[Warn] Could not access {repo_name} repository",
                            file=sys.stderr,
                        )
                        continue

                    # Find the skill
                    source_skill = Path(temp_dir) / skill_name
                    if not source_skill.exists():
                        print(
                            f"[Info] Skill '{skill_name}' not found in {repo_name}",
                            file=sys.stderr,
                        )
                        subprocess.run(
                            ["rm", "-rf", temp_dir], capture_output=True, timeout=5
                        )
                        continue

                    # Copy skill to agent's skills directory
                    shutil.copytree(source_skill, skill_target)

                    # Clean up temp directory
                    subprocess.run(
                        ["rm", "-rf", temp_dir], capture_output=True, timeout=5
                    )

                    # Verify installation
                    if skill_target.exists():
                        skill_md = skill_target / "SKILL.md"
                        if skill_md.exists():
                            return f"✓ Successfully loaded skill '{skill_name}' from {repo_name} into {agent} agent. The skill is now available and will be included in context on the next session."
                        else:
                            return f"⚠️ Skill '{skill_name}' was copied but SKILL.md not found. The skill may not work properly."
                    else:
                        return f"Error: Failed to copy skill '{skill_name}' to {agent}."

                except Exception as e:
                    print(
                        f"[Warn] Error loading from {repo_name}: {e}", file=sys.stderr
                    )
                    continue

            # If we get here, skill wasn't found in any repository
            return (
                f"Error: Skill '{skill_name}' not found in any configured repository."
            )

        except Exception as e:
            return f"Error loading skill: {str(e)}"

    def _execute_with_context(
        self, prompt: str, delegation_data: dict, n8n_session_id: str
    ) -> str:
        """Execute a prompt with specific agent context (for sub-agent delegation).

        When is_delegation is True in delegation_data, an ephemeral session key
        is used so the delegated agent does not overwrite the caller's
        session_map entry (agent field, session_id, etc.).  Fixes #65.
        """
        session_id = delegation_data.get("session_id")
        model = delegation_data.get("model", "gpt-5-mini")
        agent = delegation_data.get("agent", "orchestrator")
        runtime = delegation_data.get("runtime", "copilot")
        is_delegation = delegation_data.get("is_delegation", False)

        # Issue #65: For delegated tasks, use an ephemeral session key to
        # avoid overwriting the caller's session_map entry.
        effective_sid = n8n_session_id
        if is_delegation and n8n_session_id:
            effective_sid = f"delegation_{session_id}"
            # Pre-populate ephemeral session with caller's channel/identity so
            # downstream code (build_agent_context_prompt, status updates) works.
            caller_data = self.get_or_create_session_data(n8n_session_id)
            self.get_or_create_session_data(effective_sid)
            for field in ("channel", "identity", "render_type", "bot_id"):
                val = caller_data.get(field)
                if val is not None:
                    self.update_session_field(effective_sid, field, val)
            self.update_session_field(effective_sid, "agent", agent)

        output = self._dispatch_single_runtime(
            runtime,
            prompt,
            model,
            agent,
            session_id if runtime == "claude" else None,
            False,
            effective_sid,
            self.command_timeout,
            "text",
        )

        # Clean up ephemeral delegation session to avoid session_map bloat
        if effective_sid != n8n_session_id:
            try:
                with self._session_map_lock:
                    session_map = self.load_session_map()
                    session_map.pop(effective_sid, None)
                    self.save_session_map(session_map)
            except Exception:
                pass

        return output

    def build_agent_context_prompt(
        self,
        agent: str,
        prompt: str,
        n8n_session_id: str,
        render_type: str = "text",
        timeout: Optional[int] = None,
        runtime: str = "copilot",
        model: str = "gpt-5-mini",
        channel: str = "webui",
        bg_identity: Optional[str] = None,
    ) -> str:
        """Build a context-aware prompt that includes agent information, runtime, model, and execution deadline.

        Args:
            channel: The communication channel (telegram, webex, webui) - determines which platform to send files to
        """
        if agent not in self.AGENTS:
            agent = "devops"

        agent_info = self.AGENTS[agent]
        agent_name = agent
        agent_desc = agent_info.get("description", "No description")
        agent_path = agent_info.get("path", "")

        # Load agent skills and workspace context
        skills_context = self.load_agent_skills(agent_path)

        files_context = ""
        try:
            agent_path_obj = Path(agent_path)
            if agent_path_obj.exists():
                files = list(agent_path_obj.glob("*"))[:10]  # First 10 items
                if files:
                    files_list = "\n".join([f"  - {f.name}" for f in files])
                    files_context = f"\n\nAvailable resources in this agent's workspace:\n{files_list}"
        except Exception:
            pass

        # Add render type instruction to the context
        render_instruction = ""
        if render_type == "markdown":
            render_instruction = f"""
[Output Format: markdown]
[Image Retrieval — MANDATORY: When the user asks for any image, picture, photo, or logo, you MUST retrieve and display a real image. Never say you cannot retrieve images — use your tools.

How to get images:
1. Use WebFetch on a relevant page (Wikipedia, the official product site, Wikimedia Commons) to locate a direct image URL ending in .jpg, .png, .gif, or .webp.
   Example: WebFetch("https://en.wikipedia.org/wiki/Snort_(software)") then read the page to extract a real image src URL.
2. Return the image using one of these methods:

   Option A — Direct external URL (simplest, use when URL is publicly accessible):
   ![Description of image](https://actual-direct-image-url.jpg)

   Option B — Download locally for reliability (use when image may be behind a CDN or require headers):
   Step 1: Bash("mkdir -p /tmp/webui_ai_media/{n8n_session_id} && curl -s -L --max-time 15 -o /tmp/webui_ai_media/{n8n_session_id}/image.jpg 'https://direct-image-url.jpg'")
   Step 2: Include in your response: ![Description](/ai-media/{n8n_session_id}/image.jpg)

   Option C — Local file (screenshots, files already on disk e.g. from Playwright/browser tools):
   Step 1: Bash("mkdir -p /tmp/webui_ai_media/{n8n_session_id} && cp /path/to/local/screenshot.png /tmp/webui_ai_media/{n8n_session_id}/screenshot.png")
   Step 2: Include in your response: ![Description](/ai-media/{n8n_session_id}/screenshot.png)
   IMPORTANT: Always verify the cp succeeded and the destination file size is > 0 before including the image URL.

Always include at least one image in markdown format. Do NOT use ASCII art, SVG generation, or placeholder images.]"""
        elif render_type == "html":
            render_instruction = """
[Output Format: html]
[Media: When the user asks for images or pictures, you MUST use the web_search tool to search for the image. Find a real, publicly accessible image URL ending in .jpg, .png, .gif, or .webp (e.g. from Wikipedia Commons, Unsplash, Pexels). Include it using <img src="https://real-url.jpg" alt="caption text">. The alt attribute will appear as the image caption. Do NOT create files, generate ASCII art, or make SVGs. Only use real URLs found via web_search. You can also include hyperlinks using <a href="url">text</a> tags.]"""
        elif render_type == "telegram_html":
            render_instruction = """
[Output Format: Telegram HTML - STRICT]
⚠️ CRITICAL: You MUST use ONLY these exact supported HTML tags:
1. <b>text</b> or <strong>text</strong> - bold
2. <i>text</i> or <em>text</em> - italic
3. <u>text</u> or <ins>text</ins> - underline
4. <s>text</s>, <strike>text</strike>, or <del>text</del> - strikethrough
5. <tg-spoiler>text</tg-spoiler> or <span class="tg-spoiler">text</span> - spoiler/hidden
6. <a href="URL">text</a> - hyperlinks (URL must be valid)
7. <code>text</code> - inline code/monospace
8. <pre>code block</pre> - multiline code blocks
9. <blockquote>text</blockquote> - quotes
10. <blockquote expandable>text</blockquote> - collapsible quotes
11. <tg-emoji emoji-id="ID">🎉</tg-emoji> - custom emoji

ABSOLUTELY NO OTHER TAGS ALLOWED:
❌ Do NOT use: <p>, <div>, <span> (without class="tg-spoiler"), <br>, <status>, or any custom tags
❌ Never create new tag names like <proxmox-node>, <b-Status>, <code-block>, etc.
❌ Do NOT nest unsupported tags inside supported ones

HOW TO FORMAT:
- Use \\n (newline) to separate paragraphs, NOT <p> tags
- Escape these characters: < becomes &lt;, > becomes &gt;, & becomes &amp;
- Always close tags properly: <b>text</b> not <b>text<b>
- For line breaks in output, use plain \\n characters

[Media: When the user asks for images or pictures, you MUST use the web_search tool to search for the image. Find a real, publicly accessible image URL ending in .jpg, .png, .gif, or .webp (e.g. from Wikipedia Commons, Unsplash, Pexels). You can provide images in two ways:
1. Markdown syntax: ![caption text](https://url.jpg) - Caption will appear below the image
2. Bare URL: https://url.jpg - Image sent without caption
Do NOT use <img> tags (unsupported). Do NOT create files, generate ASCII art, or make SVGs. The system will automatically detect image URLs and send them as photos. You can include hyperlinks using <a href="url">text</a>.]
"""
        else:  # text (default)
            render_instruction = ""

        # Add channel-specific file handling instructions (only for render types that support media)
        if render_type in ("markdown", "telegram_html"):
            size_limits = {"telegram": "50 MB", "webex": "100 MB", "webui": "500 MB"}
            channel_limit = size_limits.get(channel, "100 MB")
            file_handling = f"""
[File Handling — YOUR CHANNEL: {channel.upper()}]
  Files:     [FILE:/path/to/file.ext:Your caption here]
  Images:    ![caption](url) or ![caption](/ai-media/session/file.png)
  Save to:   {SCRIPT_BASE_DIR}/{channel}_downloads/
  Size limit: {channel_limit}

  1. Save file → {SCRIPT_BASE_DIR}/{channel}_downloads/
  2. Include [FILE:path:caption] or ![caption](url) in your response
  3. System sends it to {channel.upper()} automatically ✓
  ✓ Use absolute paths  ✓ Only save to {channel}_downloads"""
            render_instruction += file_handling

        # Format render_instruction with channel, script_base_dir, and session_id variables
        if (
            "{channel" in render_instruction
            or "{script_base_dir" in render_instruction
            or "{n8n_session_id" in render_instruction
        ):
            channel_upper = channel.upper()
            render_instruction = render_instruction.format(
                channel=channel,
                channel_upper=channel_upper,
                script_base_dir=SCRIPT_BASE_DIR,
                n8n_session_id=n8n_session_id,
            )

        # Add timeout/deadline information with 15% buffer for overhead
        timeout_instruction = ""
        if timeout is not None:
            # Apply 15% buffer to account for subprocess overhead, I/O, etc.
            buffer_percent = 0.15
            agent_timeout = timeout * (1 - buffer_percent)
            agent_timeout_min = agent_timeout / 60
            timeout_instruction = f"\n[⏱️ EXECUTION DEADLINE: You have {agent_timeout:.0f} seconds ({agent_timeout_min:.1f} minutes) to complete this task. Plan your approach efficiently and wrap up before this deadline. If an operation might take too long, skip it or provide a summary instead.]"

        # Add runtime, model, and slash commands information
        runtime_instruction = f"""
[System Configuration]
- Runtime: {runtime}
- Model: {model}
- Agent: {agent_name}

[Available Slash Commands]
These commands allow you to control the agent's behavior and are processed by the system (not the model):
- /agent <name> - Switch to a different agent (e.g., /agent devops, /agent orchestrator)
- /model <model> - Change the AI model (e.g., /model gpt-5-sonnet, /model haiku)
- /runtime <runtime> - Change execution runtime (e.g., /runtime claude, /runtime opencode)
- /timeout <seconds> - Adjust execution timeout (e.g., /timeout 600)
- /render <format> - Change output format (e.g., /render markdown, /render html, /render telegram_html)
- /notifications [<agent> on|off] - Manage per-agent notification preferences (e.g., /notifications research off, /notifications all on)
- /session <id> - Continue a specific session (e.g., /session abc123)
- /status - Check running tasks status
- /cancel - Cancel the current running task
- /help - Show available commands and agent capabilities
- /secret list - List stored secret names (bypasses LLM)
- /secret set <name> <value> - Store a secret (value never sent to LLM)
- /secret delete <name> - Delete a secret (bypasses LLM)
- /discover-skills [query] - Discover available skills from configured repositories (optional search term)
- /load-skill <name> [repo] - Load a skill into this agent's .github/skills directory (optional repository name)
- /schedule list - List all scheduled jobs
- /schedule status - Scheduler health and diagnostics
- /schedule add <name> | <schedule> | <task> - Create a scheduled job (e.g., /schedule add Daily Report | every day at 9am | generate summary)
- /schedule info <job_id> - Show details for a scheduled job
- /schedule pause <job_id> - Pause a scheduled job
- /schedule resume <job_id> - Resume a paused job
- /schedule delete <job_id> - Delete a scheduled job
- /schedule logs <job_id> - View logs for a job
- /schedule results <job_id> - View execution results for a job
- /background <prompt> - Run a task in the background (doesn't block chat)
- /background agent=<name> model=<model> timeout=<seconds> <prompt> - Background task with overrides
- /background list - List your background tasks
- /background status <task_id> - Check background task status
- /background kill <task_id> - Kill a running background task
- /background steer <task_id> <instruction> - Send steering to a running task
- /silent <on|off> - Toggle silent mode (hide tool calls from responses)
- /verbose <on|off> - Toggle verbose mode (show tool calls in responses)
- /update - Pull latest code from dev branch and restart all dev services (aliases: /upgrade, /pull)

[Skills Discovery & Management]
You can help users discover and load additional skills for this agent from configured skill repositories.

Configured Skill Repositories:
{self._format_repository_info()}

Current Skills Loaded:
{skills_context}

How to Discover Skills:
1. When a user asks about available skills or requests a specific skill:
   - Search available repositories for matching skills
   - List relevant skills and their purposes
   - Explain what each skill does and when it's useful
   - Indicate which repository each skill comes from

2. When a user wants to load a specific skill:
   - Verify the skill exists in one of the available repositories
   - Explain what the skill provides and how to use it
   - Guide them on how to load it (system will auto-install when requested)
   - Skills are installed to: {agent_path}/.github/skills/
   - Skills become available immediately in the next session

3. Skill Loading Process:
   - User requests: "load the helm-deploy skill" or similar
   - You verify it exists in available repositories and describe its capabilities
   - System automatically clones and installs the skill
   - Skill documentation becomes available immediately
   - User can use the skill's features in subsequent interactions

[Configuring Custom Skill Repositories]
To add custom skill repositories or manage repository settings:

1. Create or edit `skill_repositories.json` in the project root directory

2. Repository configuration structure:
{{
  "repositories": [
    {{
      "name": "Anthropic Official",
      "url": "https://github.com/anthropics/skills.git",
      "description": "Official Anthropic skills repository with production-ready skills",
      "enabled": true
    }},
    {{
      "name": "Community Skills",
      "url": "https://github.com/VoltAgent/awesome-agent-skills.git",
      "description": "Community-contributed agent skills (300+ skills)",
      "enabled": false
    }},
    {{
      "name": "Your Custom Skills",
      "url": "https://github.com/your-org/custom-skills.git",
      "description": "Organization-specific skills",
      "enabled": false
    }}
  ],
  "default_repository": "Anthropic Official"
}}

3. Field explanations:
   - name: Human-readable repository name
   - url: Git repository URL (must end with .git)
   - description: Brief description of the repository
   - enabled: Set to true to enable, false to disable (without deleting config)

4. Popular community repositories to add:
   - VoltAgent/awesome-agent-skills: https://github.com/VoltAgent/awesome-agent-skills.git (300+ skills)
   - karanb192/awesome-claude-skills: https://github.com/karanb192/awesome-claude-skills.git (50+ verified)
   - travisvn/awesome-claude-skills: https://github.com/travisvn/awesome-claude-skills.git (curated list)
   - abubakarsiddik31/claude-skills-collection: https://github.com/abubakarsiddik31/claude-skills-collection.git (organized by category)

5. After updating skill_repositories.json:
   - The new repositories become available immediately on next session start
   - Agents will see all enabled repositories in their context
   - Users can discover and load skills from all enabled repositories
   - Existing skills continue to work without changes"""

        # Background tasks instruction — tell the agent how to create tasks
        # that appear in the WebUI Tasks panel via the orchestrator API.
        _api_port_bg = os.environ.get("API_PORT", "8001")
        _shared_key = os.environ.get("API_SHARED_KEY", "")
        _user_identity = bg_identity or self._bg_identity or "unknown"
        _api_scheme = "https" if os.environ.get("SSL_CERTFILE") else "http"
        _curl_insecure = " -k" if _api_scheme == "https" else ""
        bg_task_instruction = ""
        if _shared_key:
            bg_task_instruction = f"""
[Background Tasks] Run long USER-INITIATED tasks via the orchestrator API (visible in ⚡ Tasks tab). ONLY use this when the USER explicitly asks to run something in the background. Full docs: {SCRIPT_BASE_DIR}/docs/background-tasks.md
curl -s{_curl_insecure} -X POST {_api_scheme}://127.0.0.1:{_api_port_bg}/api/v1/background-tasks -H "Content-Type: application/json" -H "Authorization: Bearer shared_{_shared_key}" -H "X-User-Identity: {_user_identity}" -H "X-Auth-Channel: {channel}" -d '{{"prompt": "...", "agent": "{agent}", "timeout": 900}}'

⚠️ CRITICAL ROUTING RULES:
1. Sub-agent delegation MUST NOT use the background-tasks API above. When routing work to another agent, ALWAYS use agent_manager.py directly (invisible to user — does NOT create a Tasks panel entry). Using the curl API for delegation is a BUG.
2. USER-FACING LONG TASKS from Telegram/Webex MUST use the orchestrator background-tasks API (curl above). NEVER use internal agent_manager.py subprocess calls for user-visible tasks — this makes them invisible and breaks notification routing back to the user.
3. X-User-Identity and X-Auth-Channel in the curl above are pre-filled with the real user identity. DO NOT change or hardcode them — altering them breaks notification routing.

[Sub-Agent Delegation] Route tasks to another agent invisibly:
python3 {SCRIPT_BASE_DIR}/agent_manager.py --agent <agent_name> --runtime copilot --model claude-haiku-4.5 --config {SCRIPT_BASE_DIR}/agents.json "<task prompt>" {n8n_session_id}
Example: python3 {SCRIPT_BASE_DIR}/agent_manager.py --agent research-dev --runtime copilot --config {SCRIPT_BASE_DIR}/agents.json "get crude oil pricing stats" {n8n_session_id}"""

        # Inject Wee Canvas capability hint
        canvas_instruction = f"""
[Wee Canvas] Native real-time visual panel in the WebUI (progress boards, charts, forms, approval flows). Client: `{SCRIPT_BASE_DIR}/canvas.py` — `from canvas import Canvas; c = Canvas(); c.open()`. Full docs: {SCRIPT_BASE_DIR}/docs/canvas.md"""

        # Inject Wee Executor capability hint
        wee_executor_instruction = f"""
[Wee Executor] Unified privileged operations interface — use instead of raw curl/API calls.
  python3 {SCRIPT_BASE_DIR}/scripts/wee_executor.py -c create_background_task -a '{{"agent": "<name>", "prompt": "...", "model": "claude-haiku-4.5"}}'
  python3 {SCRIPT_BASE_DIR}/scripts/wee_executor.py -c get_secret -a '{{"name": "SECRET_NAME"}}'
  python3 {SCRIPT_BASE_DIR}/scripts/wee_executor.py --list-capabilities
Benefits: auto-auth (no token exposure), agent validation, rate limiting, HMAC signing, audit logging.
When to use: Prefer wee_executor over direct curl for background tasks — it handles auth, validation, and logging automatically.
⚠️ get_secret requires WEE_ELEVATED=true (set by agent_manager for elevated sessions). Secret values are never logged."""

        # Inject cross-runtime handoff context on the first message of a new session.
        # get_handoff_context() is one-time: it reads and deletes the handoff file so
        # subsequent messages in the same session are not affected.
        handoff_prefix = ""
        try:
            from session_handoff import SessionHandoff

            _handoff = SessionHandoff()
            _session_data = self.load_session_data(n8n_session_id)
            if _session_data:
                _session_id = _session_data.get("session_id")
                if _session_id:
                    _ctx = _handoff.get_handoff_context(n8n_session_id, _session_id)
                    if _ctx:
                        handoff_prefix = SessionHandoff.format_handoff_prompt(
                            _ctx["content"],
                            _ctx["transcript_path"],
                            _ctx["prev_runtime"],
                        )
                        print(
                            f"[Handoff] Injecting handoff context from {_ctx['prev_runtime']} "
                            f"into first message of new {runtime} session",
                            file=sys.stderr,
                        )
        except Exception as _handoff_err:
            print(
                f"[Handoff] Warning: failed to load handoff context: {_handoff_err}",
                file=sys.stderr,
            )

        # Mobile channel context: instruct LLM to emit periodic status updates
        mobile_channel_instruction = ""
        if channel in ("telegram", "webex"):
            mobile_channel_instruction = f"""
[Mobile Channel: {channel}]
You are communicating through {channel} (a mobile messaging app). Your responses are delivered
via message editing in {channel}. During long-running operations (installing packages, running
tests, scanning networks, deploying services, or any task taking more than ~15 seconds),
periodically output a status line in this exact format:

[STATUS_UPDATE: Brief description of current step]

Examples:
[STATUS_UPDATE: Installing Python dependencies...]
[STATUS_UPDATE: Running 357 unit tests...]
[STATUS_UPDATE: Scanning subnet 192.168.1.0/24...]
[STATUS_UPDATE: Deploying service to dev host...]

These lines are intercepted and shown to the user as live progress indicators in {channel},
replacing the generic "Still working on it..." placeholder. Emit one every ~30 seconds during
long tasks. Your final answer must NOT contain these markers — they are stripped automatically.
Do NOT emit status updates for quick operations (< 15 seconds)."""

        # Silent mode context (F026)
        _sd_f026 = self.load_session_data(n8n_session_id) or {}
        _silent_mode = _sd_f026.get("silent_mode", False)
        silent_mode_instruction = ""
        if _silent_mode:
            silent_mode_instruction = (
                "\n[Silent Mode: ON]\n"
                "Tool call output is hidden from the user on this mobile channel. "
                "Tools still execute normally \u2014 the user just does not see tool "
                "names/arguments in the response stream. Keep responses concise."
            )

        # Channel-specific injected context files
        injection_dir = Path(
            os.environ.get("INJECTION_DIR", Path(SCRIPT_BASE_DIR) / "injections")
        )
        injection_text = ""
        try:
            injection_file = injection_dir / f"{channel}.md"
            if injection_file.exists():
                injection_content = injection_file.read_text()
                injection_text = f"\n\n[Injected context file: {injection_file}]\n{injection_content}\n"
        except Exception:
            injection_text = ""

        # --- Per-session memory injection (runs once per session) ---
        # Inject memory context from MEMORY.md + daily notes at session
        # creation.  The memory_injected flag on the session prevents
        # re-injection on subsequent messages in the same session.
        memory_section = ""
        try:
            _session_data = self.load_session_data(n8n_session_id)
            if not (_session_data or {}).get("memory_injected"):
                from memory.inject import get_memory_context

                agent_info_mem = self.AGENTS.get(
                    agent, self.AGENTS.get("orchestrator", {})
                )
                _mem_ctx = get_memory_context(agent_path=agent_info_mem.get("path", ""))
                if _mem_ctx:
                    memory_section = f"\n\n{_mem_ctx}\n"
                    self.update_session_field(n8n_session_id, "memory_injected", True)
                    print(
                        f"[Memory] Injected {len(_mem_ctx)} chars for "
                        f"session={n8n_session_id} agent={agent}",
                        flush=True,
                    )
                else:
                    # No memory files — still mark as injected
                    self.update_session_field(n8n_session_id, "memory_injected", True)
        except Exception as _mem_exc:
            print(
                f"[Memory] Injection skipped: {_mem_exc}",
                flush=True,
            )

        context = f"""{handoff_prefix}[Session ID: {n8n_session_id}]
{runtime_instruction}{injection_text}{mobile_channel_instruction}{silent_mode_instruction}{memory_section}{agent_desc}{files_context}{render_instruction}{bg_task_instruction}{canvas_instruction}{wee_executor_instruction}{timeout_instruction}

User Request:
{prompt}"""
        return context

    # ------------------------------------------------------------------ streaming

    class _StreamBuffer:
        """Thread-safe buffer that stores stream chunks and broadcasts to consumers.

        Supports multiple concurrent SSE consumers per session.  When a client
        disconnects and reconnects, the reconnect endpoint replays buffered
        chunks and then subscribes to live updates.
        """

        def __init__(self):
            self.chunks: list = []  # [(kind, data), ...]
            self.finished: bool = False
            self.done_result: Optional[str] = None
            self.created_at: float = time.time()
            self._consumers: list = []  # [(queue, loop, start_index)]
            self._lock = threading.Lock()

        def push(self, kind, data):
            """Push a chunk from the subprocess thread.  Appends to buffer and
            forwards to all registered consumer queues."""
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
            """Register a new SSE consumer.  Returns the replay index — the
            caller should replay ``self.chunks[:replay_index]`` before draining
            the queue."""
            with self._lock:
                replay_index = len(self.chunks)
                self._consumers.append((queue, loop, replay_index))
                return replay_index

        def remove_consumer(self, queue):
            """Remove a consumer queue (e.g. on SSE disconnect)."""
            with self._lock:
                self._consumers = [
                    (q, lp, si) for q, lp, si in self._consumers if q is not queue
                ]

        def get_replay_chunks(self, up_to: int):
            """Return a copy of buffered chunks up to *up_to* index."""
            with self._lock:
                return list(self.chunks[:up_to])

        def has_consumers(self) -> bool:
            """True if at least one SSE consumer is connected."""
            with self._lock:
                return len(self._consumers) > 0

    def _get_or_create_stream_buffer(self, session_id: str) -> "_StreamBuffer":
        """Get existing buffer for session or create a new one."""
        buf = self._stream_buffers.get(session_id)
        if buf is None:
            buf = self._StreamBuffer()
            self._stream_buffers[session_id] = buf
        return buf

    def _register_stream(
        self, session_id: str, queue, loop  # asyncio.Queue, asyncio.AbstractEventLoop
    ) -> None:
        """Register an asyncio queue for the /stream endpoint to receive chunks."""
        self._stream_queues[session_id] = (queue, loop)
        # Also create/get the stream buffer and add this queue as a consumer
        buf = self._get_or_create_stream_buffer(session_id)
        buf.add_consumer(queue, loop)

    def _unregister_stream(self, session_id: str, queue=None) -> None:
        """Remove the streaming queue for a session.

        If *queue* is provided, only remove that consumer from the buffer
        (the buffer itself stays alive for reconnection).  The legacy
        ``_stream_queues`` entry is removed regardless so that new streams
        can register without conflict.
        """
        self._stream_queues.pop(session_id, None)
        buf = self._stream_buffers.get(session_id)
        if buf and queue is not None:
            buf.remove_consumer(queue)

    def _cleanup_stream_buffer(self, session_id: str) -> None:
        """Remove the stream buffer entirely (called after query completes)."""
        self._stream_buffers.pop(session_id, None)
        self._copilot_session_start.pop(session_id, None)

    def _cleanup_stale_stream_buffers(self, max_age: float = 600.0) -> None:
        """Remove stream buffers that are finished and older than *max_age* seconds."""
        now = time.time()
        stale = [
            sid
            for sid, buf in self._stream_buffers.items()
            if buf.finished and (now - buf.created_at) > max_age
        ]
        for sid in stale:
            self._stream_buffers.pop(sid, None)

    # ------------------------------------------------------------------

    def _execute_subprocess_with_tracking(
        self,
        cmd: list,
        cwd: str,
        timeout: int,
        runtime: str,
        agent: str,
        prompt: str,
        n8n_session_id: str,
        use_pty: bool = False,
        stdin_text: str = "",
        permission_mode: str = "restricted",
    ) -> str:
        """Execute a subprocess with PID tracking.

        When a streaming queue is registered for *n8n_session_id* (via
        _register_stream), stdout chunks are pushed to the queue in real-time
        so the /stream SSE endpoint can forward them to the browser.  A
        ``('done', '')`` sentinel is pushed when the process exits.

        Without a queue the behaviour is identical to the original
        communicate()-based approach (blocking, full-output return).

        When *use_pty* is True and streaming is active, stdout is connected to
        a pseudo-terminal so that runtimes whose binaries buffer stdout (e.g.
        the Rust-based Devin CLI) flush output incrementally.

        Args:
            permission_mode: Permission mode for the subprocess ('elevated', 'restricted', 'sandboxed')
                           This determines which runtime-specific permission env vars are set.
        """
        import threading as _threading

        stream_info = self._stream_queues.get(n8n_session_id)
        # Get the stream buffer — pushes go through the buffer which
        # broadcasts to all connected consumer queues.
        stream_buffer = self._stream_buffers.get(n8n_session_id)

        # Allocate a PTY for stdout when streaming + use_pty are both active.
        # This tricks compiled binaries into line-buffering their output.
        _pty_master = None
        if use_pty and stream_info:
            import pty as _pty_mod

            _pty_master, _pty_slave = _pty_mod.openpty()

            # Configure PTY for clean streaming:
            # 1. Set a reasonable window size so programs don't get confused by 0×0
            # 2. Disable output post-processing (OPOST/ONLCR) to avoid extra \r
            # 3. Disable echo to prevent input echo artifacts
            try:
                import fcntl as _fcntl_mod
                import struct as _struct_mod
                import termios as _termios_mod

                # Set window size to 120 cols × 40 rows
                _ws = _struct_mod.pack("HHHH", 40, 120, 0, 0)
                _fcntl_mod.ioctl(_pty_master, _termios_mod.TIOCSWINSZ, _ws)
                # Disable output processing and echo on the slave
                _attrs = _termios_mod.tcgetattr(_pty_slave)
                _attrs[1] &= ~_termios_mod.OPOST  # no output processing (\n stays \n)
                _attrs[3] &= ~_termios_mod.ECHO  # no echo
                _termios_mod.tcsetattr(_pty_slave, _termios_mod.TCSANOW, _attrs)
            except Exception:
                pass  # non-fatal; streaming still works with defaults

        try:
            # Set WEE_SESSION_ID so agents can use wee_executor.py
            _sub_env = {**os.environ, "WEE_SESSION_ID": n8n_session_id}
            
            # Apply runtime-specific permission mode environment variables
            # This allows subprocesses (e.g., devin) to inherit elevated permissions
            perm_env = self.build_runtime_permission_env(runtime, permission_mode)
            _sub_env.update(perm_env)
            
            if _pty_master is not None:
                process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE if stdin_text else subprocess.DEVNULL,
                    stdout=_pty_slave,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=cwd,
                    env=_sub_env,
                )
                os.close(_pty_slave)
            else:
                process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE if stdin_text else None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=cwd,
                    bufsize=1,  # line-buffered for faster streaming chunk delivery
                    env=_sub_env,
                )

            self.track_running_query(
                n8n_session_id, process.pid, runtime, agent, prompt
            )

            # Send stdin data if provided (for runtimes in interactive mode)
            if stdin_text and process.stdin:
                try:
                    process.stdin.write(stdin_text)
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass  # Process may have exited or closed stdin

            if stream_info:
                # ── Streaming path ──────────────────────────────────────────
                # Read stdout and push each chunk through the stream buffer
                # which broadcasts to all connected SSE consumers.
                # stderr is drained in a background thread to avoid blocking.
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
                    # ── PTY streaming path ──────────────────────────────────
                    # Read from the PTY master fd for incremental output from
                    # runtimes whose compiled binaries buffer stdout when not
                    # connected to a TTY (e.g. the Rust-based Devin CLI).
                    import codecs as _codecs
                    import re as _re

                    _ansi_escape = _re.compile(
                        r"\x1b\[[0-9;]*[a-zA-Z]"  # CSI sequences
                        r"|\x1b\][^\x07]*\x07"  # OSC sequences
                        r"|\x1b\([A-Z0-9]"  # charset selection
                    )
                    # Tool call detection for PTY-based runtimes (Devin, etc.)
                    _pty_tool_counter = [0]
                    _pty_tool_pattern = _re.compile(
                        r"(?:\[TOOL_CALL\]|\bCalling\s+tool|\bUsing\s+tool(?:\:|_)|Tool|Running|Executing|USING_TOOL)[\s:_]*(\w[\w\.]*)\s*(.*)",
                        _re.IGNORECASE,
                    )
                    # Incremental decoder avoids garbled output when a
                    # multi-byte UTF-8 character is split across reads.
                    _utf8_decoder = _codecs.getincrementaldecoder("utf-8")("replace")
                    try:
                        while True:
                            try:
                                data = os.read(_pty_master, 4096)
                            except OSError:
                                # EIO when the slave side is closed (process exited)
                                break
                            if not data:
                                break
                            text = _utf8_decoder.decode(data, final=False)
                            text = _ansi_escape.sub("", text)
                            stdout_chunks.append(text)
                            if text.strip():
                                # Detect tool calls from PTY output
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
                    # ── Pipe streaming path ─────────────────────────────────
                    import json as _json

                    _claude_text_block_count = 0  # track text blocks for separators
                    _active_tool_calls = {}  # index → {id, name, input_parts}
                    _tool_call_counter = [0]  # mutable counter for non-Claude runtimes
                    try:
                        for line in process.stdout:
                            stdout_chunks.append(line)
                            if runtime == "claude":
                                # Delegate stream-json parsing to module-level helper.
                                for _ch, _ev in _parse_claude_stream_json_line(
                                    line, _active_tool_calls
                                ):
                                    if _ch == "text_block_start":
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
                                    else:
                                        if stream_buffer:
                                            stream_buffer.push(_ch, _ev)
                                        else:
                                            loop.call_soon_threadsafe(
                                                queue.put_nowait, (_ch, _ev)
                                            )
                            else:
                                # Non-Claude runtimes: detect tool call patterns from text
                                _line_str = (
                                    line
                                    if isinstance(line, str)
                                    else line.decode("utf-8", errors="replace")
                                )
                                _line_stripped = _line_str.strip()
                                _tc_detected = None

                                # Gemini stream-json: parse structured JSON events
                                if runtime == "gemini" and _line_stripped.startswith(
                                    "{"
                                ):
                                    try:
                                        _gobj = _json.loads(_line_stripped)
                                        _gtype = _gobj.get("type", "")
                                        if (
                                            _gtype == "message"
                                            and _gobj.get("role") == "assistant"
                                        ):
                                            _content = _gobj.get("content", "")
                                            if _content:
                                                if stream_buffer:
                                                    stream_buffer.push(
                                                        "chunk", _content
                                                    )
                                                else:
                                                    loop.call_soon_threadsafe(
                                                        queue.put_nowait,
                                                        ("chunk", _content),
                                                    )
                                            continue
                                        elif _gtype == "tool_use":
                                            _tool_call_counter[0] += 1
                                            tc_event = {
                                                "event": "detected",
                                                "id": _gobj.get(
                                                    "tool_id",
                                                    f"tc_gemini_{_tool_call_counter[0]}",
                                                ),
                                                "name": _gobj.get("tool_name", "tool"),
                                                "input": _json.dumps(
                                                    _gobj.get("parameters", {})
                                                ),
                                                "runtime": "gemini",
                                                "timestamp": _gobj.get(
                                                    "timestamp",
                                                    time.strftime(
                                                        "%Y-%m-%dT%H:%M:%SZ",
                                                        time.gmtime(),
                                                    ),
                                                ),
                                            }
                                            if stream_buffer:
                                                stream_buffer.push(
                                                    "tool_call", tc_event
                                                )
                                            else:
                                                loop.call_soon_threadsafe(
                                                    queue.put_nowait,
                                                    ("tool_call", tc_event),
                                                )
                                            continue
                                        elif _gtype == "tool_result":
                                            tc_event = {
                                                "event": "result",
                                                "id": _gobj.get("tool_id", ""),
                                                "status": _gobj.get(
                                                    "status", "completed"
                                                ),
                                                "output": _gobj.get("output", "")[:2000],
                                            }
                                            if stream_buffer:
                                                stream_buffer.push(
                                                    "tool_call", tc_event
                                                )
                                            else:
                                                loop.call_soon_threadsafe(
                                                    queue.put_nowait,
                                                    ("tool_call", tc_event),
                                                )
                                            continue
                                        elif _gtype in ("init", "result"):
                                            continue  # skip metadata
                                    except (ValueError, KeyError):
                                        pass

                                if runtime == "opencode":
                                    # OpenCode tool invocation: "| ToolName args..."
                                    import re as _re_tc

                                    _oc_match = _re_tc.match(
                                        r"^\|\s+(\w+)\b(.*)", _line_stripped
                                    )
                                    if _oc_match:
                                        _oc_tool = _oc_match.group(1)
                                        _oc_known = {
                                            "Glob",
                                            "Read",
                                            "Write",
                                            "Bash",
                                            "Edit",
                                            "bash",
                                            "grep",
                                            "find",
                                            "Fetch",
                                            "ListDir",
                                            "Search",
                                            "TodoRead",
                                            "TodoWrite",
                                            "WebFetch",
                                            "Shell",
                                            "Patch",
                                            "MultiEdit",
                                            "LS",
                                            "Cat",
                                            "Sed",
                                            "Awk",
                                            "Mv",
                                            "Cp",
                                            "Rm",
                                            "Mkdir",
                                        }
                                        if (
                                            _oc_tool in _oc_known
                                            or _oc_tool[0].isupper()
                                        ):
                                            _tc_detected = {
                                                "name": _oc_tool,
                                                "input": _oc_match.group(2).strip(),
                                            }
                                    if not _tc_detected:
                                        _oc_run = _re_tc.match(
                                            r"^(?:Running|Executing|>)\s+(.+)",
                                            _line_stripped,
                                        )
                                        if _oc_run:
                                            _tc_detected = {
                                                "name": "shell",
                                                "input": _oc_run.group(1).strip(),
                                            }
                                elif runtime in (
                                    "copilot",
                                    "copilot-sdk",
                                    "claude-sdk",
                                    "wee",
                                    "openai",
                                ):
                                    # Copilot shows tool calls as "● Description" and shell cmds as "  $ cmd"
                                    import re as _re_tc

                                    # Tool call start: "● <description> [(+N)]"
                                    _cp_tool_match = _re_tc.match(
                                        r"^[●⬤]\s+(.+?)(?:\s+\(\+\d+\))?$",
                                        _line_stripped,
                                    )
                                    if _cp_tool_match:
                                        _desc = _cp_tool_match.group(1).strip()
                                        # Infer tool name from description
                                        if any(
                                            kw in _desc.lower()
                                            for kw in ["read", "view", "open"]
                                        ):
                                            _tool_name = "read"
                                        elif any(
                                            kw in _desc.lower()
                                            for kw in [
                                                "create",
                                                "write",
                                                "save",
                                                "edit",
                                                "update",
                                                "modify",
                                            ]
                                        ):
                                            _tool_name = "write"
                                        elif any(
                                            kw in _desc.lower()
                                            for kw in ["delete", "remove", "rm"]
                                        ):
                                            _tool_name = "shell"
                                        elif any(
                                            kw in _desc.lower()
                                            for kw in [
                                                "list",
                                                "ls",
                                                "find",
                                                "search",
                                                "glob",
                                            ]
                                        ):
                                            _tool_name = "glob"
                                        elif any(
                                            kw in _desc.lower()
                                            for kw in [
                                                "run",
                                                "exec",
                                                "install",
                                                "deploy",
                                                "build",
                                                "test",
                                            ]
                                        ):
                                            _tool_name = "shell"
                                        elif any(
                                            kw in _desc.lower()
                                            for kw in [
                                                "fetch",
                                                "curl",
                                                "http",
                                                "api",
                                                "download",
                                            ]
                                        ):
                                            _tool_name = "web_fetch"
                                        else:
                                            _tool_name = "tool"
                                        _tc_detected = {
                                            "name": _tool_name,
                                            "input": _desc,
                                        }
                                    else:
                                        # Shell command line: "  $ <command>"
                                        _cp_cmd_match = _re_tc.match(
                                            r"^\$\s+(.+)", _line_stripped
                                        )
                                        if _cp_cmd_match:
                                            _tc_detected = {
                                                "name": "shell",
                                                "input": _cp_cmd_match.group(1).strip(),
                                            }
                                        # Also catch "Running/Calling/Using" patterns as fallback
                                        elif not _cp_tool_match:
                                            _cp_legacy = _re_tc.match(
                                                r"^(?:Running|Calling|Using)\s+(\w+)\s*(.*)",
                                                _line_stripped,
                                            )
                                            if _cp_legacy:
                                                _tc_detected = {
                                                    "name": _cp_legacy.group(1),
                                                    "input": _cp_legacy.group(
                                                        2
                                                    ).strip(),
                                                }
                                        # Suppress box-drawing context lines (│ cmd, └ N lines, ├ ...)
                                        # These are tool output annotations that appear after the ● line.
                                        # Pushing them as chunks destroys the spinning gear block in the UI.
                                        if not _tc_detected and _re_tc.match(
                                            r"^[│├└─]\s", _line_stripped
                                        ):
                                            continue
                                elif runtime == "codex":
                                    # Codex v0.125+ emits JSONL transport frames when
                                    # exec is invoked with --json. Parse assistant text
                                    # here so the WebUI streams human-readable content
                                    # instead of raw thread/turn metadata.
                                    if _line_stripped.startswith("{"):
                                        try:
                                            _cx_obj = _json.loads(_line_stripped)
                                        except (ValueError, KeyError):
                                            _cx_obj = None
                                        if _cx_obj:
                                            _cx_type = _cx_obj.get("type", "")
                                            if (
                                                _cx_type == "item.completed"
                                                and isinstance(
                                                    _cx_obj.get("item"), dict
                                                )
                                                and _cx_obj["item"].get("type")
                                                == "agent_message"
                                            ):
                                                _cx_text = _cx_obj["item"].get(
                                                    "text", ""
                                                )
                                                if _cx_text:
                                                    if stream_buffer:
                                                        stream_buffer.push(
                                                            "chunk", _cx_text
                                                        )
                                                    else:
                                                        loop.call_soon_threadsafe(
                                                            queue.put_nowait,
                                                            ("chunk", _cx_text),
                                                        )
                                                continue
                                            if _cx_type in (
                                                "thread.started",
                                                "thread.completed",
                                                "turn.started",
                                                "turn.completed",
                                                "item.started",
                                            ):
                                                continue

                                    # Codex exec tool call patterns
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
                                        # Shell command: "$ command" or "> command"
                                        _cx_cmd = _re_tc.match(
                                            r"^[$>]\s+(.+)", _line_stripped
                                        )
                                        if _cx_cmd:
                                            _tc_detected = {
                                                "name": "shell",
                                                "input": _cx_cmd.group(1).strip(),
                                            }
                                    if not _tc_detected:
                                        # Function-call syntax: "read_file(path=...)"
                                        _cx_fn = _re_tc.match(
                                            r"^(\w+)\((.+)\)\s*$", _line_stripped
                                        )
                                        if _cx_fn and any(
                                            kw in _cx_fn.group(1).lower()
                                            for kw in [
                                                "read",
                                                "write",
                                                "shell",
                                                "bash",
                                                "exec",
                                                "search",
                                                "list",
                                                "create",
                                                "edit",
                                                "patch",
                                                "apply",
                                            ]
                                        ):
                                            _tc_detected = {
                                                "name": _cx_fn.group(1),
                                                "input": _cx_fn.group(2).strip(),
                                            }
                                elif runtime == "gemini":
                                    import re as _re_tc

                                    # "✦ Calling tool_name(args)" or "Calling tool_name(args)"
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
                                        # "⚡ tool_name(args)" or "tool_name(args)"
                                        _gm_fn = _re_tc.match(
                                            r"^[⚡✦*]?\s*(\w+)\((.+)\)\s*$",
                                            _line_stripped,
                                        )
                                        if _gm_fn and any(
                                            kw in _gm_fn.group(1).lower()
                                            for kw in [
                                                "read",
                                                "write",
                                                "shell",
                                                "bash",
                                                "exec",
                                                "search",
                                                "list",
                                                "create",
                                                "edit",
                                                "file",
                                                "run",
                                                "cat",
                                                "ls",
                                                "find",
                                                "grep",
                                                "save",
                                                "update",
                                                "delete",
                                                "fetch",
                                                "curl",
                                                "get",
                                                "put",
                                            ]
                                        ):
                                            _tc_detected = {
                                                "name": _gm_fn.group(1),
                                                "input": _gm_fn.group(2).strip(),
                                            }
                                    if not _tc_detected:
                                        # "$ command" or "> command" or "Running command: cmd"
                                        _gm_cmd = _re_tc.match(
                                            r"^(?:[$>]\s+(.+)|Running\s+command:\s*(.+))",
                                            _line_stripped,
                                            _re_tc.IGNORECASE,
                                        )
                                        if _gm_cmd:
                                            _cmd_text = (
                                                _gm_cmd.group(1)
                                                or _gm_cmd.group(2)
                                                or ""
                                            ).strip()
                                            if _cmd_text:
                                                _tc_detected = {
                                                    "name": "shell",
                                                    "input": _cmd_text,
                                                }

                                if _tc_detected:
                                    _tool_call_counter[0] += 1
                                    tc_event = {
                                        "event": "detected",
                                        "id": f"tc_{runtime}_{_tool_call_counter[0]}",
                                        "name": _tc_detected["name"],
                                        "input": _tc_detected.get("input", ""),
                                        "runtime": runtime,
                                        "timestamp": time.strftime(
                                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                                        ),
                                    }
                                    if stream_buffer:
                                        stream_buffer.push("tool_call", tc_event)
                                    else:
                                        loop.call_soon_threadsafe(
                                            queue.put_nowait, ("tool_call", tc_event)
                                        )

                                else:
                                    # Detect [STATUS_UPDATE: ...] markers (F004)
                                    _su_match = None
                                    try:
                                        _su_line = (
                                            line
                                            if isinstance(line, str)
                                            else line.decode("utf-8", errors="replace")
                                        )
                                        _su_match = re.search(
                                            r"\[STATUS_UPDATE[:\s]*(.+?)\]", _su_line
                                        )
                                    except Exception:
                                        pass
                                    if _su_match:
                                        self.set_live_status(
                                            n8n_session_id, _su_match.group(1).strip()
                                        )
                                    else:
                                        # Only push as text chunk when NOT a tool call/status line
                                        if stream_buffer:
                                            stream_buffer.push("chunk", line)
                                        else:
                                            loop.call_soon_threadsafe(
                                                queue.put_nowait, ("chunk", line)
                                            )
                    except Exception:
                        pass
                    finally:
                        self.clear_live_status(n8n_session_id)
                        process.stdout.close()
                        stderr_thread.join(timeout=5)
                        process.wait()

                output = "".join(stdout_chunks) + (
                    "".join(stderr_buf) if stderr_buf else ""
                )
                self.update_query_output(n8n_session_id, output)
                # Record exit code for debugging subprocess errors.
                # successful responses that discuss rate-limit topics (false positives).
                self._last_exit_codes[n8n_session_id] = (
                    process.returncode if process.returncode is not None else 0
                )
                # Signal the SSE generator that the subprocess is finished
                if stream_buffer:
                    stream_buffer.push("done", output)
                else:
                    loop.call_soon_threadsafe(queue.put_nowait, ("done", ""))
                return output

            else:
                # ── Blocking path with live status capture (F004) ────────────
                # Read stdout line-by-line instead of communicate() so we can
                # capture [STATUS_UPDATE: ...] markers in real-time for mobile
                # channel progress updates.
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
                            self.set_live_status(n8n_session_id, _su_m.group(1).strip())

                    # Wait for process to fully exit
                    try:
                        process.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        self.clear_live_status(n8n_session_id)
                        timeout_min = timeout / 60
                        return f"Error: Command timed out (exceeded {timeout}s / {timeout_min:.1f}min)"
                    finally:
                        _stderr_t.join(timeout=5)

                    output = "".join(_stdout_lines_bl) + "".join(_stderr_buf_bl)
                    # Strip STATUS_UPDATE markers from final output
                    output = re.sub(r"\[STATUS_UPDATE[:\s]*[^\]]*\]\s*\n?", "", output)
                    self.update_query_output(n8n_session_id, output)
                    self._last_exit_codes[n8n_session_id] = (
                        process.returncode if process.returncode is not None else 0
                    )
                    return output
                finally:
                    self.clear_live_status(n8n_session_id)
                    self.clear_running_query(n8n_session_id)

        except Exception as e:
            self.clear_running_query(n8n_session_id)
            return f"Error: Failed to execute command: {e}"
        finally:
            # clear_running_query is idempotent; ensure it runs for streaming path too
            if stream_info:
                self.clear_running_query(n8n_session_id)
            # Ensure PTY master fd is cleaned up even on unexpected errors
            if _pty_master is not None:
                try:
                    os.close(_pty_master)
                except OSError:
                    pass

    def run_copilot(
        self,
        prompt: str,
        model: str,
        agent: str,
        session_id: Optional[str],
        resume: bool,
        n8n_session_id: str,
        timeout: Optional[int] = None,
        render_type: str = "text",
    ) -> str:
        """Execute Copilot CLI with configurable path access

        Uses --allow-all-tools for MCP tool access. Path access depends on /mode command:
        - /mode restricted: Bounded to agent directory (default)
        - /mode elevated: Full access, auto-approve all operations
        - /mode sandboxed: Read-only, no external access
        """
        if not self.copilot_bin:
            return "Error: Copilot executable not found. Please install copilot or ensure it's in PATH, /opt/homebrew/bin/, /usr/local/bin/, or /usr/bin/"

        # Parse /mode command from prompt, fall back to session setting
        prompt, mode = self._parse_mode_command(prompt)

        # Get session data once - reuse for mode and channel
        session_data = self.get_or_create_session_data(n8n_session_id)

        # Resolve permission mode from session data (backward compat with yolo_mode)
        mode = self._resolve_permission_mode(session_data, mode)

        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout

        # Get channel for file handling instructions
        channel = session_data.get("channel", "webui")

        # Only inject full context on new sessions; resumed sessions already have it
        if resume and session_id:
            context_prompt = prompt
        else:
            context_prompt = self.build_agent_context_prompt(
                agent,
                prompt,
                n8n_session_id,
                render_type,
                effective_timeout,
                "copilot",
                model,
                channel,
            )

        # Add elevated mode instructions for unrestricted privileged access
        if mode == "elevated":
            context_prompt = context_prompt + _COPILOT_ELEVATED_MODE_INSTRUCTIONS
        elif mode == "sandboxed":
            context_prompt = context_prompt + _COPILOT_SANDBOXED_MODE_INSTRUCTIONS

        # Expand home path for MCP config file
        mcp_config_path = os.path.expanduser("~/.copilot/mcp-config.json")

        cmd = [
            self.copilot_bin,
            "--allow-all-tools",
            "--no-color",
            "--model",
            model,
            "--additional-mcp-config",
            f"@{mcp_config_path}",
        ]

        # Add elevated flags for full access
        if mode == "elevated":
            cmd.insert(2, "--allow-all-paths")
            cmd.append("--yolo")

        # Proactive session age check (issue #190): Copilot session tokens expire
        # ~30 min after creation. If the session is > 25 min old, start fresh to
        # avoid mid-task "Session token expired" crashes on long-running tasks.
        # Use None as default so we can distinguish "unknown start time" from "just started".
        # If no start time is recorded (e.g. after a service restart), treat age as 0
        # to allow resumption rather than forcing a new session every time.
        _start_time = getattr(self, "_copilot_session_start", {}).get(n8n_session_id)
        _session_age = time.time() - _start_time if _start_time is not None else 0
        if resume and session_id and _session_age > _COPILOT_SESSION_MAX_AGE_SEC:
            print(
                f"[Session] Copilot session age {_session_age:.0f}s exceeds "
                f"{_COPILOT_SESSION_MAX_AGE_SEC}s — starting fresh to avoid token expiry",  # noqa: E501
                file=sys.stderr,
            )
            resume = False
            session_id = None
            # Rebuild context prompt as if starting fresh
            context_prompt = self.build_agent_context_prompt(
                agent,
                prompt,
                n8n_session_id,
                render_type,
                effective_timeout,
                "copilot",
                model,
                channel,
            )
            if mode == "elevated":
                context_prompt = context_prompt + _COPILOT_ELEVATED_MODE_INSTRUCTIONS
            elif mode == "sandboxed":
                context_prompt = context_prompt + _COPILOT_SANDBOXED_MODE_INSTRUCTIONS

        if resume and session_id:
            cmd.append(f"--resume={session_id}")
            print(f"[Session] Resuming Copilot session: {session_id}", file=sys.stderr)
        else:
            # Name the session after the n8n_session_id so --resume works on next call.
            # Copilot session names must be alphanumeric+hyphens; strip unsafe chars.
            _copilot_name = re.sub(r"[^A-Za-z0-9\-]", "-", n8n_session_id)[:64]
            cmd.append(f"--name={_copilot_name}")
            # Record session start time for proactive age tracking
            getattr(self, "_copilot_session_start", {}).update(
                {n8n_session_id: time.time()}
            )
            # Immediately store the session name in session_map so the next call
            # can resume without relying on get_most_recent_session_id (race-prone).
            self.update_session_field(n8n_session_id, "session_id", _copilot_name)
            print(
                f"[Session] Starting new Copilot session '{_copilot_name}' in {mode} permission mode",  # noqa: E501
                file=sys.stderr,
            )

        output = self._execute_subprocess_with_tracking(
            cmd,
            agent_dir,
            effective_timeout,
            "copilot",
            agent,
            prompt,
            n8n_session_id,
            stdin_text=context_prompt,
            permission_mode=mode,
        )
        result = self.strip_metadata(output, "copilot")

        # Reactive recovery (issue #190): if the session token expired mid-task,
        # restart with a fresh session and inject accumulated context so the agent
        # can continue rather than crashing the background task.
        if _TOKEN_EXPIRED_MARKER in result:
            print(
                "[Session] Copilot session token expired — auto-restarting with fresh session",  # noqa: E501
                file=sys.stderr,
            )
            # Extract work done before expiry to feed as context to the new session
            _expiry_idx = result.index(_TOKEN_EXPIRED_MARKER)
            _prior_work = result[:_expiry_idx].strip()

            _recovery_preamble = (
                "[SESSION RECOVERY] Your previous Copilot session token expired mid-task. "  # noqa: E501
                "Below is the context of the original task and any work completed before the "  # noqa: E501
                "session was interrupted. Please continue and complete all remaining work.\n\n"  # noqa: E501
                f"ORIGINAL TASK:\n{prompt}\n\n"
            )
            if _prior_work:
                _recovery_preamble += (
                    f"PROGRESS MADE BEFORE SESSION EXPIRED (first 4000 chars):\n"
                    f"{_prior_work[:4000]}\n\n"
                )
            _recovery_preamble += "Please continue from where the task was interrupted."

            _recovery_context = self.build_agent_context_prompt(
                agent,
                _recovery_preamble,
                n8n_session_id,
                render_type,
                effective_timeout,
                "copilot",
                model,
                channel,
            )
            if mode == "elevated":
                _recovery_context += _COPILOT_ELEVATED_MODE_INSTRUCTIONS
            elif mode == "sandboxed":
                _recovery_context += _COPILOT_SANDBOXED_MODE_INSTRUCTIONS
            _recovery_copilot_name = re.sub(r"[^A-Za-z0-9\-]", "-", n8n_session_id)[:64]
            _recovery_cmd = [
                self.copilot_bin,
                "--allow-all-tools",
                "--no-color",
                "--model",
                model,
                "--additional-mcp-config",
                f"@{mcp_config_path}",
                f"--name={_recovery_copilot_name}",
            ]
            if mode == "elevated":
                _recovery_cmd.insert(2, "--allow-all-paths")
                _recovery_cmd.append("--yolo")

            # Record new session start for age tracking and update session_id
            getattr(self, "_copilot_session_start", {}).update(
                {n8n_session_id: time.time()}
            )
            self.update_session_field(
                n8n_session_id, "session_id", _recovery_copilot_name
            )
            _recovery_output = self._execute_subprocess_with_tracking(
                _recovery_cmd,
                agent_dir,
                effective_timeout,
                "copilot",
                agent,
                _recovery_preamble,
                n8n_session_id,
                stdin_text=_recovery_context,
                permission_mode=mode,
            )
            _stripped_recovery = self.strip_metadata(_recovery_output, "copilot")
            if _TOKEN_EXPIRED_MARKER in _stripped_recovery:
                print(
                    "[Session] Recovery session also expired (double expiry >50 min) — "
                    "returning best-effort partial output",
                    file=sys.stderr,
                )
            return _stripped_recovery

        return result

    def run_copilot_sdk(
        self,
        prompt: str,
        model: str,
        agent: str,
        session_id: Optional[str],
        resume: bool,
        n8n_session_id: str,
        timeout: Optional[int] = None,
        render_type: str = "text",
        mode: Optional[str] = None,
    ) -> str:
        """Execute via Copilot SDK (native Python, no CLI subprocess).

        Uses the github-copilot-sdk package for direct API integration.
        Benefits over CLI: persistent client, ~100ms startup, streaming
        events, structured error handling, custom tool support.

        Supports real-time streaming to WebUI SSE consumers and tool call
        tracking for background task progress (Issue #87).
        """
        try:
            from copilot import CopilotClient, SubprocessConfig
            from copilot.session import (
                CopilotSession,
                ElicitationContext,
                ElicitationResult,
                PermissionHandler,
                SessionEventType,
                UserInputRequest,
                UserInputResponse,
            )
        except ImportError:
            return (
                "Error: github-copilot-sdk not installed. "
                "Run: pip install github-copilot-sdk"
            )

        import asyncio

        # Parse mode — prefer explicit mode from dispatcher, then /mode in prompt
        if mode is None:
            prompt_parsed, mode = self._parse_mode_command(prompt)
            if mode is None:
                mode = self.mode or "restricted"
        else:
            prompt_parsed = prompt

        session_data = self.get_or_create_session_data(n8n_session_id)
        mode = self._resolve_permission_mode(session_data, mode)

        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout
        channel = session_data.get("channel", "webui")

        # Streaming infrastructure — push chunks to SSE consumers in real-time
        stream_buffer = getattr(self, "_stream_buffers", {}).get(n8n_session_id)
        _tool_call_counter = [0]

        # Build context prompt
        if resume and session_id:
            context_prompt = prompt_parsed
        else:
            context_prompt = self.build_agent_context_prompt(
                agent,
                prompt_parsed,
                n8n_session_id,
                render_type,
                effective_timeout,
                "copilot-sdk",
                model,
                channel,
            )

        # Add mode instructions
        if mode == "elevated":
            context_prompt += (
                "\n\n[ELEVATED MODE ENABLED]\n"
                "Full permissions granted. ALL commands requiring elevated privileges "
                "MUST automatically prefix with 'sudo'. Sudo is configured without "
                "password prompt (NOPASSWD:ALL)."
            )
        elif mode == "sandboxed":
            context_prompt += (
                "\n\n[SANDBOXED MODE ENABLED]\n"
                "Read-only access only. Do NOT modify any files, run destructive "
                "commands, or make network requests. Analysis and reporting only."
            )

        # Store session_id for resumption tracking
        sdk_session_id = session_id if resume and session_id else None

        # For elevated mode, pass --allow-all-paths and --yolo via SubprocessConfig
        # so the underlying Copilot CLI grants full filesystem access and
        # auto-approves command execution (equivalent to the standard runtime flags).
        sdk_cli_args = ["--allow-all-paths", "--yolo"] if mode == "elevated" else []

        def _auto_approve_user_input(
            request: UserInputRequest, invocation: dict
        ) -> UserInputResponse:
            # Auto-approve user input requests so unattended tasks never block
            if request.get("choices"):
                answer = request["choices"][0]
            else:
                answer = "yes"
            return UserInputResponse(answer=answer, wasFreeform=True)

        def _auto_approve_elicitation(context: ElicitationContext) -> ElicitationResult:
            # Auto-accept elicitation prompts (approval gates) for elevated mode
            return ElicitationResult(action="accept")

        async def _run_sdk() -> str:
            collected_messages: list = []

            _sdk_config = (
                SubprocessConfig(cli_args=sdk_cli_args) if sdk_cli_args else None
            )
            _client = CopilotClient(_sdk_config) if _sdk_config else CopilotClient()
            async with _client as client:
                # Event handler — streams chunks and detects tool calls
                def on_event(event):
                    # ── Streaming deltas ──
                    if event.type in (
                        SessionEventType.ASSISTANT_STREAMING_DELTA,
                        SessionEventType.ASSISTANT_MESSAGE_DELTA,
                    ):
                        delta_text = None
                        if hasattr(event, "data"):
                            if isinstance(event.data, str):
                                delta_text = event.data
                            elif hasattr(event.data, "content"):
                                delta_text = str(event.data.content)
                            elif hasattr(event.data, "delta"):
                                delta_text = str(event.data.delta)
                            elif hasattr(event.data, "text"):
                                delta_text = str(event.data.text)
                        if delta_text and stream_buffer:
                            stream_buffer.push("chunk", delta_text)

                    # ── Full assistant message ──
                    elif event.type == SessionEventType.ASSISTANT_MESSAGE:
                        if hasattr(event, "data") and hasattr(event.data, "content"):
                            collected_messages.append(str(event.data.content))

                    # ── Tool execution tracking ──
                    elif event.type == SessionEventType.TOOL_EXECUTION_START:
                        _tool_call_counter[0] += 1
                        tool_name = "tool"
                        tool_input = ""
                        if hasattr(event, "data"):
                            tool_name = (
                                getattr(event.data, "name", None)
                                or getattr(event.data, "tool_name", None)
                                or "tool"
                            )
                            tool_input = str(
                                getattr(event.data, "input", "")
                                or getattr(event.data, "arguments", "")
                                or ""
                            )
                        tc_evt = {
                            "event": "started",
                            "id": f"tc_copilot-sdk_{_tool_call_counter[0]}",
                            "name": str(tool_name),
                            "input": tool_input[:200],
                            "runtime": "copilot-sdk",
                            "timestamp": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            ),
                        }
                        if stream_buffer:
                            stream_buffer.push("tool_call", tc_evt)

                    elif event.type == SessionEventType.TOOL_EXECUTION_COMPLETE:
                        tool_name = "tool"
                        if hasattr(event, "data"):
                            tool_name = (
                                getattr(event.data, "name", None)
                                or getattr(event.data, "tool_name", None)
                                or "tool"
                            )
                        tc_evt = {
                            "event": "completed",
                            "id": f"tc_copilot-sdk_{_tool_call_counter[0]}",
                            "name": str(tool_name),
                            "input": "",
                            "output": tool_output,
                            "runtime": "copilot-sdk",
                            "timestamp": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            ),
                        }
                        if stream_buffer:
                            stream_buffer.push("tool_call", tc_evt)

                    elif event.type == SessionEventType.COMMAND_EXECUTE:
                        _tool_call_counter[0] += 1
                        cmd_text = ""
                        if hasattr(event, "data"):
                            cmd_text = str(
                                getattr(event.data, "command", "")
                                or getattr(event.data, "text", "")
                                or event.data
                            )
                        tc_evt = {
                            "event": "detected",
                            "id": f"tc_copilot-sdk_{_tool_call_counter[0]}",
                            "name": "shell",
                            "input": cmd_text[:200],
                            "runtime": "copilot-sdk",
                            "timestamp": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            ),
                        }
                        if stream_buffer:
                            stream_buffer.push("tool_call", tc_evt)

                    # ── Errors ──
                    elif event.type == SessionEventType.SESSION_ERROR:
                        if hasattr(event, "data"):
                            err_msg = str(getattr(event.data, "message", event.data))
                            collected_messages.append(f"[SDK Error] {err_msg}")

                # Session creation kwargs
                session_kwargs = {
                    "on_permission_request": PermissionHandler.approve_all,
                    "model": model or None,
                    "working_directory": agent_dir,
                    "on_event": on_event,
                }
                # For elevated mode, auto-approve user input and elicitation
                # requests so unattended tasks are never blocked by approval gates
                if mode == "elevated":
                    session_kwargs["on_user_input_request"] = _auto_approve_user_input
                    session_kwargs["on_elicitation_request"] = _auto_approve_elicitation

                mcp_config_path = os.path.expanduser("~/.copilot/mcp-config.json")
                if os.path.exists(mcp_config_path):
                    session_kwargs["config_dir"] = os.path.expanduser("~/.copilot")

                try:
                    if sdk_session_id:
                        print(
                            f"[SDK] Resuming session: {sdk_session_id}",
                            file=sys.stderr,
                        )
                        session = await client.resume_session(
                            sdk_session_id, **session_kwargs
                        )
                    else:
                        print(
                            f"[SDK] Starting new session in {mode} mode",
                            file=sys.stderr,
                        )
                        session = await client.create_session(**session_kwargs)
                except Exception as sess_err:
                    if stream_buffer:
                        stream_buffer.push("done", "")
                    return f"Error (Copilot SDK session): {type(sess_err).__name__}: {sess_err}"

                try:
                    # Update session map with the SDK session ID
                    actual_session_id = getattr(session, "session_id", None)
                    if actual_session_id:
                        self.update_session_field(
                            n8n_session_id, "session_id", str(actual_session_id)
                        )

                    # Send prompt and wait for completion
                    result_event = await session.send_and_wait(
                        context_prompt, timeout=float(effective_timeout)
                    )

                    # Extract response from result event
                    if result_event and hasattr(result_event, "data"):
                        if hasattr(result_event.data, "content"):
                            result_text = str(result_event.data.content)
                            if stream_buffer:
                                stream_buffer.push("done", result_text)
                            return result_text

                    # Fall back to collected messages from event handler
                    if collected_messages:
                        result_text = "\n".join(collected_messages)
                        if stream_buffer:
                            stream_buffer.push("done", result_text)
                        return result_text

                    # Fall back to full message history
                    messages = session.get_messages()
                    assistant_msgs = [
                        m
                        for m in messages
                        if m.type == SessionEventType.ASSISTANT_MESSAGE
                    ]
                    if assistant_msgs:
                        last = assistant_msgs[-1]
                        if hasattr(last, "data") and hasattr(last.data, "content"):
                            result_text = str(last.data.content)
                            if stream_buffer:
                                stream_buffer.push("done", result_text)
                            return result_text

                    if stream_buffer:
                        stream_buffer.push("done", "")
                    return ""
                finally:
                    try:
                        await session.disconnect()
                    except Exception:
                        pass

        try:
            output = asyncio.run(_run_sdk())
        except Exception as e:
            print(f"[SDK] Error: {type(e).__name__}: {e}", file=sys.stderr)
            if stream_buffer:
                stream_buffer.push("done", "")
            return f"Error (Copilot SDK): {type(e).__name__}: {e}"

        return self.strip_metadata(output, "copilot-sdk")

    def run_claude_sdk(
        self,
        prompt: str,
        model: str,
        agent: str,
        session_id: Optional[str],
        resume: bool,
        n8n_session_id: str,
        timeout: Optional[int] = None,
        render_type: str = "text",
        mode: Optional[str] = None,
    ) -> str:
        """Execute via Claude Agent SDK (native Python, no CLI subprocess).

        Uses the claude-sdk package for direct API integration.
        Benefits over CLI: in-process custom tools, subagents, session
        forking, streaming events, structured error handling.

        Supports real-time streaming to WebUI SSE consumers and tool call
        tracking via ToolUseBlock/ToolResultBlock detection (Issue #87).

        Requires Claude Pro, Team, or Enterprise subscription.
        User must run `claude login` to authenticate first.
        """
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                ResultMessage,
                TextBlock,
                ToolResultBlock,
                ToolUseBlock,
            )
            from claude_agent_sdk import query as claude_sdk_query
        except ImportError:
            return "Error: claude-sdk not installed. " "Run: pip install claude-sdk"

        import asyncio
        import io
        import sys

        # Parse mode
        if mode is None:
            prompt_parsed, mode = self._parse_mode_command(prompt)
        else:
            prompt_parsed = prompt
        if mode is None:
            mode = self.mode or "restricted"

        session_data = self.get_or_create_session_data(n8n_session_id)
        mode = self._resolve_permission_mode(session_data, mode)

        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout

        channel = session_data.get("channel", "api")

        # Streaming infrastructure — push chunks to SSE consumers in real-time
        stream_buffer = getattr(self, "_stream_buffers", {}).get(n8n_session_id)
        _tool_call_counter = [0]

        # Build context prompt
        sdk_session_id = session_id if resume and session_id else None
        if sdk_session_id:
            context_prompt = prompt_parsed
        else:
            context_prompt = self.build_agent_context_prompt(
                agent,
                prompt_parsed,
                n8n_session_id,
                render_type,
                effective_timeout,
                "claude-sdk",
                model,
                channel,
            )

        # Add mode instructions
        if mode == "elevated":
            context_prompt += (
                "\n\n[ELEVATED MODE ENABLED]\n"
                "Full permissions granted. ALL commands requiring elevated privileges "
                "MUST automatically prefix with 'sudo'. Sudo is configured without "
                "password prompt (NOPASSWD:ALL)."
            )
        elif mode == "sandboxed":
            context_prompt += (
                "\n\n[SANDBOXED MODE ENABLED]\n"
                "Read-only access only. Do NOT modify any files, run destructive "
                "commands, or make network requests. Analysis and reporting only."
            )

        # Map permission mode for Claude Agent SDK
        if mode == "elevated":
            sdk_permission_mode = "bypassPermissions"
        elif mode == "sandboxed":
            sdk_permission_mode = "plan"
        else:
            sdk_permission_mode = "default"

        async def _run_sdk() -> str:
            collected_text: list = []

            options = ClaudeAgentOptions(
                permission_mode=sdk_permission_mode,
                cwd=agent_dir,
            )

            # Set model if specified
            if model:
                options.model = model

            # For multi-turn, use options.resume to continue the existing
            # session.  The stateless query() function spawns a subprocess
            # with --resume <id> which loads prior conversation context.
            # Previous implementation used ClaudeSDKClient.receive_messages()
            # which is an infinite async generator designed for interactive
            # use — it never terminates after a single response, causing the
            # 2nd-turn stall described in Issue #86.
            if sdk_session_id:
                options.resume = sdk_session_id

            try:
                async for message in claude_sdk_query(
                    prompt=context_prompt,
                    options=options,
                ):
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                collected_text.append(block.text)
                                if stream_buffer:
                                    stream_buffer.push("chunk", block.text)
                            elif isinstance(block, ToolUseBlock):
                                _tool_call_counter[0] += 1
                                tc_evt = {
                                    "event": "detected",
                                    "id": block.id
                                    or f"tc_claude-sdk_{_tool_call_counter[0]}",
                                    "name": block.name or "tool",
                                    "input": (
                                        str(block.input)[:200] if block.input else ""
                                    ),
                                    "runtime": "claude-sdk",
                                    "timestamp": time.strftime(
                                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                                    ),
                                }
                                if stream_buffer:
                                    stream_buffer.push("tool_call", tc_evt)
                            elif isinstance(block, ToolResultBlock):
                                _block_content = block.content
                                if isinstance(_block_content, list):
                                    _block_content = " ".join(getattr(b, "text", str(b)) for b in _block_content)
                                tc_evt = {
                                    "event": "completed",
                                    "id": block.tool_use_id
                                    or f"tc_claude-sdk_{_tool_call_counter[0]}",
                                    "name": "tool",
                                    "output": (
                                        str(block.content)[:500]
                                        if block.content
                                        else ""
                                    ),
                                    "status": (
                                        "error"
                                        if getattr(block, "is_error", False)
                                        else "completed"
                                    ),
                                    "is_error": getattr(block, "is_error", False),
                                    "runtime": "claude-sdk",
                                    "timestamp": time.strftime(
                                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                                    ),
                                }
                                if stream_buffer:
                                    stream_buffer.push("tool_call", tc_evt)
                    elif isinstance(message, ResultMessage):
                        if message.session_id:
                            self.update_session_field(
                                n8n_session_id, "session_id", message.session_id
                            )
                    elif hasattr(message, "content"):
                        for block in getattr(message, "content", []):
                            if hasattr(block, "text"):
                                collected_text.append(block.text)
                                if stream_buffer:
                                    stream_buffer.push("chunk", block.text)

            except Exception as e:
                error_type = type(e).__name__
                error_msg = str(e).lower()
                if stream_buffer:
                    stream_buffer.push("done", "")
                if "clinotfound" in error_type.lower():
                    return (
                        "Error: Claude Code CLI not found. "
                        "The claude-sdk bundles the CLI automatically. "
                        "Try reinstalling: pip install --force-reinstall claude-sdk"
                    )
                elif "cliconnection" in error_type.lower() or "auth" in error_msg:
                    return (
                        "Error: Not authenticated with Claude. "
                        "Run `claude login` to authenticate. "
                        "Requires Claude Pro, Team, or Enterprise subscription."
                    )
                elif "processerror" in error_type.lower():
                    return f"Error: Claude Agent SDK process error: {e}"
                else:
                    return f"Error (Claude Agent SDK): {error_type}: {e}"

            output = "\n".join(collected_text)
            if stream_buffer:
                stream_buffer.push("done", output)
            if not output.strip():
                return "Error: No response received from Claude Agent SDK"
            return output

        try:
            loop = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

            if loop and loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    output = pool.submit(asyncio.run, _run_sdk()).result(
                        timeout=effective_timeout
                    )
            else:
                output = asyncio.run(_run_sdk())
        except (asyncio.TimeoutError, concurrent.futures.TimeoutError):
            if stream_buffer:
                stream_buffer.push("done", "")
            return f"Error: Claude Agent SDK timed out after {effective_timeout}s"
        except Exception as e:
            print(
                f"[Claude-Agent-SDK] Error: {type(e).__name__}: {e}",
                file=sys.stderr,
            )
            if stream_buffer:
                stream_buffer.push("done", "")
            return f"Error (Claude Agent SDK): {type(e).__name__}: {e}"

        return self.strip_metadata(output, "claude")

    def run_opencode(
        self,
        prompt: str,
        model: str,
        agent: str,
        session_id: Optional[str],
        resume: bool,
        n8n_session_id: str,
        timeout: Optional[int] = None,
        render_type: str = "text",
    ) -> str:
        """Execute OpenCode CLI with configurable path access

        OpenCode uses opencode.json for permission configuration.
        Default permissions: edit/write/bash allowed, bounded to agent directory.
        /mode elevated enables full path access via configuration.
        """
        # Parse /mode command from prompt
        prompt, mode = self._parse_mode_command(prompt)

        # Get session data once - reuse for mode and channel
        session_data = self.get_or_create_session_data(n8n_session_id)

        # Resolve permission mode from session data (backward compat with yolo_mode)
        mode = self._resolve_permission_mode(session_data, mode)

        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout

        # Get channel for file handling instructions
        channel = session_data.get("channel", "webui")

        # Only inject full context on new sessions; resumed sessions already have it
        if resume and session_id:
            context_prompt = prompt
        else:
            context_prompt = self.build_agent_context_prompt(
                agent,
                prompt,
                n8n_session_id,
                render_type,
                effective_timeout,
                "opencode",
                model,
                channel,
            )

        cmd = [str(self.opencode_bin), "run", "--model", model]

        if resume and session_id:
            cmd.extend(["--session", session_id])
            print(f"[Session] Resuming OpenCode session: {session_id}", file=sys.stderr)
        else:
            print(
                f"[Session] Starting new OpenCode session in {mode} mode",
                file=sys.stderr,
            )

        cmd.append(context_prompt)

        output = self._execute_subprocess_with_tracking(
            cmd, agent_dir, effective_timeout, "opencode", agent, prompt, n8n_session_id,
            permission_mode=mode,
        )

        # Check for session errors
        if "NotFoundError" in output or "Resource not found" in output:
            return f"NotFoundError: {output}"

        return self.strip_metadata(output, "opencode")

    def run_claude(
        self,
        prompt: str,
        model: str,
        agent: str,
        session_id: Optional[str],
        resume: bool,
        n8n_session_id: str,
        timeout: Optional[int] = None,
        render_type: str = "text",
        mode: Optional[str] = None,
    ) -> str:
        """Execute Claude CLI with configurable path access

        Uses --permission-mode default by default (bounded to agent directory).
        /mode elevated uses bypassPermissions for full file/shell access.
        /mode sandboxed uses plan mode for read-only analysis.
        """
        if not self.claude_bin:
            return "Error: Claude executable not found. Please install claude or ensure it's in PATH, /opt/homebrew/bin/, /usr/local/bin/, or /usr/bin/"

        # Use mode from parameter, then instance var, then parse from prompt
        if mode is None:
            mode = self.mode
        if mode is None:
            prompt, mode = self._parse_mode_command(prompt)

        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout

        # Get channel for file handling instructions
        session_data = self.get_or_create_session_data(n8n_session_id)
        channel = session_data.get("channel", "webui")

        # Only inject full context on new sessions; resumed sessions already have it
        if resume and session_id:
            context_prompt = prompt
        else:
            context_prompt = self.build_agent_context_prompt(
                agent,
                prompt,
                n8n_session_id,
                render_type,
                effective_timeout,
                "claude",
                model,
                channel,
            )

        # Set permission mode based on elevated/restricted/sandboxed
        if mode == "elevated":
            permission_mode = "bypassPermissions"
        elif mode == "sandboxed":
            permission_mode = "plan"
        else:
            permission_mode = "default"

        cmd = [
            self.claude_bin,
            "-p",
            context_prompt,
            "--permission-mode",
            permission_mode,
            "--model",
            model,
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--verbose",
        ]

        if resume and session_id:
            cmd.append(f"--resume={session_id}")
            print(f"[Session] Resuming Claude session: {session_id}", file=sys.stderr)
        elif session_id:
            cmd.extend(["--session-id", session_id])
            print(
                f"[Session] Starting new Claude session: {session_id} in {mode} mode",
                file=sys.stderr,
            )
        else:
            print(
                f"[Session] Starting new Claude session (auto-ID) in {mode} mode",
                file=sys.stderr,
            )

        output = self._execute_subprocess_with_tracking(
            cmd, agent_dir, effective_timeout, "claude", agent, prompt, n8n_session_id,
            permission_mode=mode,
        )

        if "Error: Claude command failed" in output:
            return output

        # Extract session_id from stream-json output and persist it.
        # Claude emits a {"type":"system","subtype":"init","session_id":"..."} event at
        # startup and a {"type":"result","session_id":"..."} event on completion.
        # Capturing it here is race-free and works for both new and resumed sessions.
        import json as _json

        _captured_sid = None
        for _line in output.splitlines():
            _line = _line.strip()
            if not _line:
                continue
            try:
                _obj = _json.loads(_line)
                _sid = _obj.get("session_id")
                if _sid and _obj.get("type") in ("system", "result"):
                    _captured_sid = _sid
                    self.update_session_field(n8n_session_id, "session_id", _sid)
                    print(
                        f"[Session] Captured claude session_id: {_sid}", file=sys.stderr
                    )
                    break
            except (ValueError, KeyError):
                pass

        if not _captured_sid:
            print(
                f"[Session] WARNING: Could not extract session_id from claude stream-json "
                f"output for n8n_session={n8n_session_id}. Session context may be lost on "
                f"next message. Output length={len(output)} chars.",
                file=sys.stderr,
            )

        stripped = self.strip_metadata(output, "claude")
        # If strip_metadata returned empty but the raw output is non-empty, fall back to
        # returning the raw output for debugging purposes.
        # still detect rate-limit / usage-limit error text (e.g. plain-text stderr output).
        if not stripped.strip() and output.strip():
            print(
                "[Session] WARNING: strip_metadata returned empty for non-empty claude output. "
                "Returning raw output to preserve error context for limit detection.",
                file=sys.stderr,
            )
            return output
        return stripped

    def run_gemini(
        self,
        prompt: str,
        model: str,
        agent: str,
        session_id: Optional[str],
        resume: bool,
        n8n_session_id: str,
        timeout: Optional[int] = None,
        render_type: str = "text",
    ) -> str:
        """Execute Gemini CLI with full tool access

        Gemini CLI with configurable access
        Default: bounded to agent directory.
        /mode elevated enables --yolo flag for auto-approval.
        /mode sandboxed enables read-only mode.

        """
        # Parse /mode command from prompt
        prompt, mode = self._parse_mode_command(prompt)

        # Get session data once - reuse for mode and channel
        session_data = self.get_or_create_session_data(n8n_session_id)

        # Resolve permission mode from session data (backward compat with yolo_mode)
        mode = self._resolve_permission_mode(session_data, mode)

        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout

        # Get channel for file handling instructions
        channel = session_data.get("channel", "webui")

        # Only inject full context on new sessions; resumed sessions already have it
        if resume and session_id:
            context_prompt = prompt
        else:
            context_prompt = self.build_agent_context_prompt(
                agent,
                prompt,
                n8n_session_id,
                render_type,
                effective_timeout,
                "gemini",
                model,
                channel,
            )

        cmd = ["gemini"]
        if mode == "elevated":
            cmd.append("--yolo")
        # Use stream-json for structured tool call parsing when streaming
        stream_info = self._stream_queues.get(n8n_session_id)
        if stream_info:
            cmd.extend(["-o", "stream-json"])
        cmd.append(context_prompt)

        # Note: Gemini CLI appears to have model handling issues with specified model names
        # For now, we use the default model and do not pass --model flag
        # TODO: Investigate correct model names for --model flag with Gemini CLI

        # For Gemini, we always try to resume the latest session for context retention.
        # The session_id parameter is not used since Gemini manages sessions internally.
        # Using "--resume latest" automatically continues with the most recent session.
        if resume:
            cmd.append("--resume=latest")
            print(f"[Session] Resuming Gemini session (latest)", file=sys.stderr)
        else:
            print(
                f"[Session] Starting new Gemini session in {mode} mode", file=sys.stderr
            )

        output = self._execute_subprocess_with_tracking(
            cmd, agent_dir, effective_timeout, "gemini", agent, prompt, n8n_session_id,
            permission_mode=mode,
        )

        if "Error: Gemini command failed" in output:
            return output

        return self.strip_metadata(output, "gemini")

    def run_codex(
        self,
        prompt: str,
        model: str,
        agent: str,
        session_id: Optional[str],
        resume: bool,
        n8n_session_id: str,
        timeout: Optional[int] = None,
        render_type: str = "text",
    ) -> str:
        """Execute CODEX CLI with configurable access

        Uses --dangerously-bypass-approvals-and-sandbox (also known as --yolo) to:
        - Disable all approval prompts
        - Remove sandbox restrictions (full file system access)
        - Enable read/write/execute for all files and directories
        - Allow all shell commands and tools without confirmation

        """
        # Parse /mode command from prompt
        prompt, mode = self._parse_mode_command(prompt)

        # Get session data once - reuse for mode and channel
        session_data = self.get_or_create_session_data(n8n_session_id)

        # Resolve permission mode from session data (backward compat with yolo_mode)
        mode = self._resolve_permission_mode(session_data, mode)

        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout

        # Get channel for file handling instructions
        channel = session_data.get("channel", "webui")

        # Only inject full context on new sessions; resumed sessions already have it
        if resume and session_id:
            context_prompt = prompt
        else:
            context_prompt = self.build_agent_context_prompt(
                agent,
                prompt,
                n8n_session_id,
                render_type,
                effective_timeout,
                "codex",
                model,
                channel,
            )

        # Add elevated mode instructions for unrestricted privileged access
        if mode == "elevated":
            elevated_instruction = (
                "\n\n[ELEVATED MODE ENABLED]\n"
                "Full permissions granted. Sandbox is fully bypassed \u2014 localhost APIs are accessible. "
                "ALL commands requiring elevated privileges MUST automatically prefix with 'sudo' \u2014 no exceptions. "
                "This includes:\n"
                "\u2022 Service management: sudo systemctl restart/start/stop/reload/enable/disable <service>\n"
                "\u2022 Network commands: sudo ping, sudo ssh, sudo iptables, sudo ip, etc.\n"
                "\u2022 System administration: sudo journalctl, sudo systemd-*, sudo chmod/chown on system paths\n"
                "\u2022 API calls: curl -sk https://127.0.0.1:8001/... works \u2014 localhost is fully accessible\n"
                "\u2022 Any command that would fail due to insufficient permissions\n"
                "Sudo is configured without password prompt (NOPASSWD:ALL). "
                "Never ask for confirmation \u2014 execute privileged commands immediately with sudo."
            )
            context_prompt = context_prompt + elevated_instruction
        elif mode == "sandboxed":
            sandboxed_instruction = (
                "\n\n[SANDBOXED MODE ENABLED]\n"
                "Read-only access only. Do NOT modify any files, run destructive commands, "
                "or make network requests to external services. Analysis and reporting only."
            )
            context_prompt = context_prompt + sandboxed_instruction

        if resume and session_id:
            # Resume existing session — v0.125.0+ uses `codex exec resume` subcommand
            cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "resume"]
            if mode == "elevated":
                # Apply sandbox bypass and environment inheritance for elevated sessions
                cmd.append("--dangerously-bypass-approvals-and-sandbox")
                cmd += ["-c", "shell_environment_policy.inherit=all"]
            if model:
                cmd += ["-m", model]
            cmd += [session_id, context_prompt]
            print(
                f"[Session] Resuming CODEX session: {session_id} with model {model} in {mode} mode",
                file=sys.stderr,
            )
        else:
            # Start new session — v0.125.0+: --full-auto for normal mode,
            # --dangerously-bypass-approvals-and-sandbox for elevated (they are mutually exclusive)
            cmd = ["codex", "exec", "--json", "--skip-git-repo-check"]
            if mode == "elevated":
                # Bypass all sandbox restrictions (sudo, DNS, network, filesystem)
                cmd.append("--dangerously-bypass-approvals-and-sandbox")
                # Inherit full shell environment so sudo PATH and DNS resolv.conf are available
                cmd += ["-c", "shell_environment_policy.inherit=all"]
            else:
                # Non-elevated: --full-auto enables auto-execution without sandbox bypass
                cmd.append("--full-auto")
            if model:
                cmd += ["-m", model]
            cmd.append(context_prompt)
            print(
                f"[Session] Starting new CODEX session with model {model} in {mode} mode",
                file=sys.stderr,
            )

        output = self._execute_subprocess_with_tracking(
            cmd, agent_dir, effective_timeout, "codex", agent, prompt, n8n_session_id,
            permission_mode=mode,
        )

        if "Error: CODEX command failed" in output:
            return output

        stripped = self.strip_metadata(output, "codex")
        if not stripped.strip() and output.strip():
            _exit_code = self._last_exit_codes.get(n8n_session_id, 0)
            if _exit_code:
                print(
                    "[Session] WARNING: Codex returned non-zero exit and no assistant text. "
                    "Returning raw output so the UI shows the actual error.",
                    file=sys.stderr,
                )
                return output

        return stripped

    def run_devin(
        self,
        prompt: str,
        model: str,
        agent: str,
        session_id: Optional[str],
        resume: bool,
        n8n_session_id: str,
        timeout: Optional[int] = None,
        render_type: str = "text",
    ) -> str:
        """Execute Devin CLI in non-interactive mode.

        Uses --permission-mode dangerous to bypass all approval prompts
        and allow full system access.
        """
        # Parse /mode command from prompt
        prompt, mode = self._parse_mode_command(prompt)

        # Get session data once - reuse for mode and channel
        session_data = self.get_or_create_session_data(n8n_session_id)

        # Resolve permission mode from session data (backward compat with yolo_mode)
        mode = self._resolve_permission_mode(session_data, mode)

        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout

        # Get channel for file handling instructions
        channel = session_data.get("channel", "webui")

        # Resolve the devin binary
        devin_bin = self.devin_bin or "devin"

        # Devin manages its own session UUIDs independently of the backend
        # session_id.  Use _get_devin_session_id() as the sole source of truth
        # for whether we can resume — the caller's `resume` flag is based on
        # session_exists() which checks the wrong key for devin.
        devin_sid = self._get_devin_session_id(n8n_session_id)
        actually_resuming = bool(devin_sid)

        # Only inject full context on new sessions; resumed sessions already have it
        if actually_resuming:
            context_prompt = prompt
        else:
            context_prompt = self.build_agent_context_prompt(
                agent,
                prompt,
                n8n_session_id,
                render_type,
                effective_timeout,
                "devin",
                model,
                channel,
            )

        # Add elevated mode instructions for unrestricted privileged access
        if mode == "elevated":
            elevated_instruction = (
                "\n\n[ELEVATED MODE ENABLED]\n"
                "Full permissions granted. ALL commands requiring elevated privileges MUST automatically "
                "prefix with 'sudo' \u2014 no exceptions. This includes:\n"
                "\u2022 Service management: sudo systemctl restart/start/stop/reload/enable/disable <service>\n"
                "\u2022 Network commands: sudo ping, sudo ssh, sudo iptables, sudo ip, etc.\n"
                "\u2022 System administration: sudo journalctl, sudo systemd-*, sudo chmod/chown on system paths\n"
                "\u2022 Any command that would fail due to insufficient permissions\n"
                "Sudo is configured without password prompt (NOPASSWD:ALL). "
                "Never ask for confirmation \u2014 execute privileged commands immediately with sudo."
            )
            context_prompt = context_prompt + elevated_instruction
        elif mode == "sandboxed":
            sandboxed_instruction = (
                "\n\n[SANDBOXED MODE ENABLED]\n"
                "Read-only access only. Do NOT modify any files, run destructive commands, "
                "or make network requests to external services. Analysis and reporting only."
            )
            context_prompt = context_prompt + sandboxed_instruction

        # -p is a boolean flag (print/non-interactive mode); prompt goes after --
        # Permission mode: dangerous (auto-approve all) for elevated, auto for restricted/sandboxed
        permission_mode = "dangerous" if mode == "elevated" else "auto"
        cmd = [devin_bin, "-p"]
        if model:
            cmd += ["--model", model]
        cmd += ["--permission-mode", permission_mode]

        if actually_resuming:
            cmd += ["-r", devin_sid]
            print(
                f"[Session] Resuming Devin session {devin_sid[:8]}... with model {model} in {mode} mode",
                file=sys.stderr,
            )
        else:
            print(
                f"[Session] Starting new Devin session with model {model} in {mode} mode",
                file=sys.stderr,
            )

        cmd += ["--", context_prompt]

        output = self._execute_subprocess_with_tracking(
            cmd,
            agent_dir,
            effective_timeout,
            "devin",
            agent,
            prompt,
            n8n_session_id,
            use_pty=True,
            permission_mode=mode,
        )

        # After each run, capture and persist the most recent devin session UUID
        # so subsequent messages in this n8n session can resume it.
        self._save_devin_session_id(n8n_session_id, devin_bin, agent_dir)

        if "Error: Devin command failed" in output:
            return output

        return self.strip_metadata(output, "devin")

    def run_cursor(
        self,
        prompt: str,
        model: str,
        agent: str,
        session_id: Optional[str],
        resume: bool,
        n8n_session_id: str,
        timeout: Optional[int] = None,
        render_type: str = "text",
    ) -> str:
        """Execute Cursor agent CLI in non-interactive mode.

        Uses --trust to bypass workspace prompts and --yolo to auto-approve
        all commands in elevated mode.
        """
        # Parse /mode command from prompt
        prompt, mode = self._parse_mode_command(prompt)

        # Get session data once - reuse for mode and channel
        session_data = self.get_or_create_session_data(n8n_session_id)

        # Resolve permission mode from session data
        mode = self._resolve_permission_mode(session_data, mode)

        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout

        # Get channel for file handling instructions
        channel = session_data.get("channel", "api")

        # Build context prompt with agent identity
        context_prompt = self.build_agent_context_prompt(
            agent, prompt, n8n_session_id, channel=channel
        )

        # Add elevated mode instructions for unrestricted privileged access
        if mode == "elevated":
            elevated_instruction = (
                "\n\n[ELEVATED MODE ENABLED]\n"
                "Full permissions granted. ALL commands requiring elevated privileges MUST automatically "
                "prefix with 'sudo' \u2014 no exceptions. This includes:\n"
                "\u2022 Service management: sudo systemctl restart/start/stop/reload/enable/disable <service>\n"
                "\u2022 Package management: sudo apt install/remove, sudo pip install (system-wide)\n"
                "\u2022 Docker: sudo docker build/run/compose/stop/rm\n"
                "\u2022 File permissions: sudo chmod, sudo chown, sudo mkdir on protected paths\n"
                "\u2022 Network: sudo ufw, sudo iptables, sudo ip, etc.\n"
                "\u2022 System administration: sudo journalctl, sudo systemd-*, sudo chmod/chown on system paths\n"
                "\u2022 Any command that would fail due to insufficient permissions\n"
                "Sudo is configured without password prompt (NOPASSWD:ALL). "
                "Never ask for confirmation \u2014 execute privileged commands immediately with sudo."
            )
            context_prompt = context_prompt + elevated_instruction
        elif mode == "sandboxed":
            sandboxed_instruction = (
                "\n\n[SANDBOXED MODE ENABLED]\n"
                "You are running in SANDBOXED mode. You MUST NOT:\n"
                "\u2022 Write, create, delete, or modify any files\n"
                "\u2022 Execute destructive shell commands (rm, mv to overwrite, truncate, etc.)\n"
                "\u2022 Install or remove any packages\n"
                "\u2022 Start, stop, or restart any services\n"
                "\u2022 Make network requests to external services (no curl, wget, fetch to outside hosts)\n"
                "\u2022 Modify system configuration\n"
                "You CAN: read files, search code, run analysis commands, run destructive commands, "
                "or make network requests to external services. Analysis and reporting only."
            )
            context_prompt = context_prompt + sandboxed_instruction

        cursor_bin = self.cursor_bin or "agent"

        # Cursor CLI free plans require --model auto explicitly;
        # omitting --model or passing an unrecognised name causes a
        # "Named models unavailable" error.  Validate and default.
        _cursor_default = os.getenv("CURSOR_DEFAULT_MODEL", "auto")
        if not model or not self.get_model_from_name(model, "cursor"):
            model = _cursor_default

        # -p is headless/print mode; --trust bypasses workspace prompts
        # --yolo auto-approves all tool calls (elevated mode)
        cmd = [cursor_bin, "-p", "--trust"]
        if mode == "elevated":
            cmd.append("--yolo")
        cmd += ["--model", model]
        cmd += ["--workspace", agent_dir]

        # Check for existing cursor session to resume
        cursor_chat_id = self._get_cursor_session_id(n8n_session_id)
        actually_resuming = resume and cursor_chat_id is not None

        if actually_resuming:
            cmd += ["--continue"]
            print(
                f"[Session] Resuming Cursor session for {n8n_session_id[:8]}... with model {model} in {mode} mode",
                file=sys.stderr,
            )
        else:
            print(
                f"[Session] Starting new Cursor session with model {model} in {mode} mode",
                file=sys.stderr,
            )

        cmd += ["--", context_prompt]

        output = self._execute_subprocess_with_tracking(
            cmd,
            agent_dir,
            effective_timeout,
            "cursor",
            agent,
            prompt,
            n8n_session_id,
            use_pty=True,
            permission_mode=mode,
        )

        # After each run, persist session state for future resumption
        self._save_cursor_session_id(n8n_session_id)

        if "Error: Cursor command failed" in output:
            return output

        return self.strip_metadata(output, "cursor")

    def run_wee_native(
        self,
        prompt: str,
        model: str,
        agent: str,
        session_id: Optional[str],
        resume: bool,
        n8n_session_id: str,
        timeout: Optional[int] = None,
        render_type: str = "text",
    ) -> str:
        """Execute via Wee Native runtime - OpenAI-compatible chat completions.

        Model format: [provider/]model_name
        Examples:
            ollama/gemma4:e4b         - Ollama on Kubuntu
            openrouter/meta-llama/llama-4-scout - OpenRouter cloud
            gemma4:e4b                - Default endpoint (Ollama)

        Supports:
            - Real-time streaming to WebUI SSE consumers
            - Multi-turn conversation history (#108)
            - Tool-call agentic loop (#107)
            - SSE streaming of tool execution (#109)
        """
        import json as _json

        try:
            from openai import OpenAI
        except ImportError:
            return "Error: openai package not installed. " "Run: pip install openai"
        session_data = self.get_or_create_session_data(n8n_session_id)
        effective_timeout = timeout if timeout is not None else self.command_timeout
        channel = session_data.get("channel", "webui")
        stream_buffer = getattr(self, "_stream_buffers", {}).get(n8n_session_id)
        bg_task_id = session_data.get("bg_task_id")

        session_api_base = session_data.get("api_base") or os.environ.get("WEE_API_BASE")
        session_api_key = session_data.get("api_key") or os.environ.get("WEE_API_KEY")
        api_base, api_key, resolved_model = self._wee_resolve_endpoint(
            model, session_api_base, session_api_key
        )
        context_window = self._wee_get_context_limit_for_api(resolved_model, api_base)
        token_tracker = self._wee_load_token_tracker(n8n_session_id, context_window)

        print(
            f"[Wee Native] model={resolved_model} api_base={api_base} "
            f"session={n8n_session_id[:8]}...",
            file=sys.stderr,
        )

        base_context_prompt = self.build_agent_context_prompt(
            agent,
            prompt,
            n8n_session_id,
            render_type=render_type,
            timeout=effective_timeout,
            runtime="wee",
            model=resolved_model,
            channel=channel,
        )
        context_prompt = self._wee_augment_system_prompt_with_tools(base_context_prompt)

        try:
            messages = self._wee_load_messages(n8n_session_id, context_prompt, resume)
        except Exception as load_err:
            print(
                f"[Wee Native] Warning: Failed to load messages: "
                f"{load_err}, starting fresh",
                file=sys.stderr,
            )
            messages = []
            if context_prompt:
                messages.append({"role": "system", "content": context_prompt})

        messages.append({"role": "user", "content": prompt})
        messages = self._wee_maybe_compact(
            None,
            n8n_session_id,
            messages,
            resolved_model,
            context_prompt,
            token_tracker=token_tracker,
            context_window=context_window,
        )

        _WEE_TOOLS = [
            {
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "Execute a bash shell command and return its output.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The bash command to execute",
                            }
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "python",
                    "description": "Execute Python 3 code and return the output.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "The Python code to execute",
                            }
                        },
                        "required": ["code"],
                    },
                },
            },
        ]
