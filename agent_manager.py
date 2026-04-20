#!/usr/bin/env python3
"""
Unified AI Session Wrapper for N8N Integration
Wraps GitHub Copilot CLI and OpenCode CLI
Manages session ID mapping between N8N chat sessions and AI backend sessions
"""

import argparse
import calendar
import copy
import hashlib
import hmac
import json
import logging
import os
import re
import shlex
import secrets as _secrets
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

from session_manager_components import (
    CliCommandHandler,
    RuntimeExecutor,
    StreamingManager,
)

# Dynamically determine the repo base directory (works regardless of where repo is cloned)
SCRIPT_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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
    credentials redacted from the ``input`` field.  Other fields are
    passed through unchanged."""
    if not isinstance(data, dict):
        return data
    inp = data.get("input")
    if inp is None:
        # Also check partial_json for streaming deltas
        pj = data.get("partial_json")
        if pj and isinstance(pj, str):
            sanitized = dict(data)
            sanitized["partial_json"] = _sanitize_command_for_display(pj)
            return sanitized
        return data
    sanitized = dict(data)
    if isinstance(inp, str):
        sanitized["input"] = _sanitize_command_for_display(inp)
    elif isinstance(inp, dict):
        # Claude-style structured input — sanitize known fields
        new_inp = dict(inp)
        for field in ("command", "input", "code", "content", "url", "body"):
            if field in new_inp and isinstance(new_inp[field], str):
                new_inp[field] = _sanitize_command_for_display(new_inp[field])
        sanitized["input"] = new_inp
    return sanitized


def _split_command_args(command: str) -> List[str]:
    """Parse a command string into argv without an implicit shell."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("No command provided")
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError(f"Invalid command syntax: {exc}") from exc
    if not argv:
        raise ValueError("No command provided")
    return argv


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
        return hmac.compare_digest(token[7:], self.shared_key)

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


class BackgroundTaskManager:
    """Manages background task lifecycle: creation, tracking, output capture, cleanup."""

    MAX_TASKS_PER_USER = int(os.environ.get("BG_MAX_TASKS_PER_USER", "5"))
    MAX_TOTAL_TASKS = int(os.environ.get("BG_MAX_TOTAL_TASKS", "500"))
    MAX_OUTPUT_LINES = 500
    MAX_TOOL_CALLS = 200
    MAX_TOOL_FIELD_CHARS = int(os.environ.get("BG_MAX_TOOL_FIELD_CHARS", "4000"))
    CLEANUP_AGE_HOURS = int(os.environ.get("BG_CLEANUP_HOURS", "24"))
    BG_CLEANUP_HOURS = int(os.environ.get("BG_CLEANUP_HOURS", "24"))

    def __init__(self):
        home = os.path.expanduser("~")
        copilot_dir = os.path.join(home, ".copilot")
        os.makedirs(copilot_dir, exist_ok=True)
        # Use environment-specific file path to avoid prod/dev contamination
        api_port = os.environ.get("API_PORT", "8001")
        env_suffix = "-dev" if api_port == "8001" else ""
        self._path = os.path.join(copilot_dir, f"background-tasks{env_suffix}.json")
        self._lock = threading.Lock()
        self._tasks_cache = None  # in-memory cache; avoids disk I/O on every API call
        self._bg_events = {}  # {origin_session_id: [event_dicts]}
        self._bg_events_lock = threading.Lock()
        self._cleanup_thread_started = False

    def _start_cleanup_thread(self):
        """Start a background thread that runs cleanup every 5 minutes."""
        if getattr(self, "_cleanup_thread_started", False):
            return
        self._cleanup_thread_started = True

        def _cleanup_loop():
            while True:
                time.sleep(300)  # 5 minutes
                try:
                    self.cleanup_old()
                except Exception:
                    pass

        t = threading.Thread(target=_cleanup_loop, daemon=True)
        t.start()

    def _load(self) -> list:
        """Return in-memory cache if populated; load from disk only on cold start."""
        if getattr(self, "_tasks_cache", None) is not None:
            return self._tasks_cache
        try:
            with open(self._path, "r") as f:
                self._tasks_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._tasks_cache = []
        return self._tasks_cache

    def _save(self, tasks: list):
        """Update in-memory cache and atomically flush to disk."""
        self._tasks_cache = tasks  # fast path: subsequent reads skip disk
        tmp_path = self._path + ".tmp"
        try:
            with open(tmp_path, "w") as f:
                json.dump(tasks, f, indent=2, default=str)
            os.replace(tmp_path, self._path)
        except Exception:
            # If atomic rename fails, try direct write as fallback
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            with open(self._path, "w") as f:
                json.dump(tasks, f, indent=2, default=str)

    def _load_unlocked(self, force: bool = False) -> list:
        """Load tasks without acquiring self._lock (must be called within lock)."""
        return self._load()

    def _save_unlocked(self, tasks: list) -> None:
        """Save tasks without acquiring self._lock (must be called within lock)."""
        self._save(tasks)

    def _evict_oldest_terminal(self, tasks: list) -> list:
        """Evict oldest completed/failed/killed tasks when store exceeds MAX_TOTAL_TASKS."""
        if len(tasks) <= self.MAX_TOTAL_TASKS:
            return tasks

        terminal_statuses = {"completed", "failed", "killed"}
        terminal_tasks = [
            (i, t)
            for i, t in enumerate(tasks)
            if t.get("status") in terminal_statuses
        ]

        if not terminal_tasks:
            return tasks

        evict_count = len(tasks) - self.MAX_TOTAL_TASKS
        terminal_tasks.sort(key=lambda x: x[1].get("completed_at", "") or x[1].get("created_at", ""))
        evict_indices = {idx for idx, _ in terminal_tasks[:evict_count]}

        return [t for i, t in enumerate(tasks) if i not in evict_indices]

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
        }
        with self._lock:
            tasks = self._load()
            tasks = self._evict_oldest_terminal(tasks)
            tasks.append(task)
            self._save(tasks)
        self._start_cleanup_thread()
        return task

    def get_task(self, task_id: str) -> Optional[dict]:
        with self._lock:
            for t in self._load_unlocked():
                if t["task_id"] == task_id:
                    return copy.deepcopy(t)
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
                copy.deepcopy(t)
                for t in self._load_unlocked()
                if self._identity_matches(t, channel, identity)
            ]

    def list_all_tasks(self) -> list:
        """Return all tasks regardless of identity/channel."""
        with self._lock:
            return copy.deepcopy(self._load_unlocked())

    def list_task_summaries(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        reconcile_running: bool = False,
    ) -> Tuple[list, int]:
        """Return paginated task summaries without large transcript payloads."""
        with self._lock:
            tasks = list(self._load_unlocked())
            if reconcile_running:
                changed = False
                completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                for task in tasks:
                    if task.get("status") != "running" or not task.get("pid"):
                        continue
                    try:
                        os.kill(task["pid"], 0)
                    except ProcessLookupError:
                        task["status"] = "failed"
                        task["error"] = "Process terminated unexpectedly"
                        task["completed_at"] = completed_at
                        changed = True
                if changed:
                    self._save_unlocked(tasks)
            if status:
                tasks = [t for t in tasks if t.get("status") == status]
            tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
            total = len(tasks)
            page = tasks[offset : offset + limit]
            summaries = [
                {
                    "task_id": t["task_id"],
                    "agent": t["agent"],
                    "runtime": t["runtime"],
                    "model": t["model"],
                    "prompt": (t.get("prompt") or "")[:200],
                    "status": t["status"],
                    "created_at": t["created_at"],
                    "completed_at": t.get("completed_at"),
                    "error": t.get("error"),
                }
                for t in page
            ]
            return summaries, total

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
            tasks = self._load_unlocked()
            for t in tasks:
                if t["task_id"] == task_id:
                    t.update(fields)
                    break
            self._save_unlocked(tasks)

    def append_output(self, task_id: str, line: str):
        with self._lock:
            tasks = self._load_unlocked()
            for t in tasks:
                if t["task_id"] == task_id:
                    t["output_lines"].append(line)
                    # Keep only last MAX_OUTPUT_LINES
                    if len(t["output_lines"]) > self.MAX_OUTPUT_LINES:
                        t["output_lines"] = t["output_lines"][-self.MAX_OUTPUT_LINES :]
                    break
            self._save_unlocked(tasks)

    def append_tool_call(self, task_id: str, tool_call: dict):
        """Append a new tool call event to the task."""
        with self._lock:
            tasks = self._load_unlocked()
            for t in tasks:
                if t["task_id"] == task_id:
                    if "tool_calls" not in t:
                        t["tool_calls"] = []
                    t["tool_calls"].append(self._trim_tool_call(tool_call))
                    if len(t["tool_calls"]) > self.MAX_TOOL_CALLS:
                        t["tool_calls"] = t["tool_calls"][-self.MAX_TOOL_CALLS :]
                    break
            self._save_unlocked(tasks)

    def update_tool_call(self, task_id: str, call_id: str, **fields):
        """Update an existing tool call by its id."""
        with self._lock:
            tasks = self._load_unlocked()
            for t in tasks:
                if t["task_id"] == task_id:
                    for tc in t.get("tool_calls", []):
                        if tc.get("id") == call_id:
                            tc.update(fields)
                            tc.update(self._trim_tool_call(tc))
                            break
                    break
            self._save_unlocked(tasks)

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
            tasks = self._load_unlocked()
            before = len(tasks)
            tasks = [t for t in tasks if t["task_id"] != task_id]
            if len(tasks) < before:
                self._save_unlocked(tasks)
                return True
        return False

    def cleanup_old(self):
        """Purge terminal tasks older than CLEANUP_AGE_HOURS and enforce MAX_TOTAL_TASKS cap."""
        cutoff = time.time() - (self.CLEANUP_AGE_HOURS * 3600)
        with self._lock:
            tasks = self._load()
            kept = []
            for t in tasks:
                if t["status"] == "running":
                    kept.append(t)
                    continue
                if t["status"] == "queued":
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
            # Enforce total task cap after TTL cleanup
            kept = self._evict_oldest_terminal(kept)
            if len(kept) < len(tasks):
                self._save_unlocked(kept)

    def reconcile_stale_tasks(self) -> dict:
        """Reconcile orphaned tasks after a service restart.

        - Mark 'running' tasks as 'failed' if their PID is no longer alive.
        - Return a summary dict with counts of what was reconciled.
        """
        now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        reconciled = {"stale_running": 0, "queued_ready": 0}
        with self._lock:
            tasks = self._load_unlocked()
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
                self._save_unlocked(tasks)
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
        {"id": "wee", "label": "wee", "icon": "🌿"},
    ]

    available = [rt for rt in all_runtimes if check_runtime_available(rt["id"])]
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

    def create_session(self, channel: str, identity: str, session_id: str) -> dict:
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
                "gemini-3.1-pro",
                "Gemini 3.1 Pro",
                ["pro-3.1", "pro-3"],
            ),
            (
                "gemini-3.1-flash-live",
                "Gemini 3.1 Flash Live",
                ["flash-live-3.1", "gemini-3.1-flash", "flash-3.1"],
            ),
            (
                "gemini-3.1-flash-lite",
                "Gemini 3.1 Flash Lite",
                ["flash-lite-3.1"],
            ),
            (
                "gemini-3-deep-think",
                "Gemini 3 Deep Think",
                ["deep-think", "thinking"],
            ),
            (
                "gemini-3-pro-preview",
                "Gemini 3 Pro (Preview)",
                ["pro-3-preview", "gemini-3-pro"],
            ),
            (
                "gemini-3-flash-preview",
                "Gemini 3 Flash (Preview)",
                ["flash-3-preview"],
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
            ("gemini-3.1-pro-latest", "Gemini 3.1 Pro (Latest)", ["gemini-3.1-pro"]),
        ],
    }

    # CODEX models configuration (from copilot CLI --model choices)
    CODEX_MODELS = {
        "OpenAI Models": [
            ("gpt-5.4", "GPT-5.4", ["gpt-5.4", "gpt-5.4-pro"]),
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

    WEE_MODELS = {
        "Ollama Models": [
            ("ollama/granite3.3-tuned", "Granite 3.3 Tuned", ["granite", "granite3.3"]),
            (
                "ollama/gemma4:e4b",
                "Gemma 4 E4B (local)",
                ["gemma4", "gemma", "gemma4-e4b"],
            ),
            ("ollama/gemma4:e2b", "Gemma 4 E2B", ["gemma4-e2b"]),
            (
                "ollama/gemma4:e4b-nothinker",
                "Gemma 4 E4B (No Thinker)",
                ["gemma4-nothinker"],
            ),
            ("ollama/gemma4:e2b-nothinker", "Gemma 4 E2B (No Thinker)", []),
            ("ollama/qwen3:32b", "Qwen 3 32B", ["qwen", "qwen3"]),
            ("ollama/llama4:scout", "Llama 4 Scout", ["scout", "llama4"]),
            ("ollama/phi4:14b", "Phi 4 14B", ["phi4", "phi"]),
            ("ollama/deepseek-r1:32b", "DeepSeek R1 32B", ["deepseek", "deepseek-r1"]),
            ("ollama/command-r:35b", "Command R 35B", ["command-r"]),
        ],
        "OpenRouter Models": [
            (
                "openrouter/meta-llama/llama-4-scout",
                "Llama 4 Scout (OpenRouter)",
                ["or-scout"],
            ),
            (
                "openrouter/meta-llama/llama-4-maverick",
                "Llama 4 Maverick (OpenRouter)",
                ["or-maverick"],
            ),
            (
                "openrouter/google/gemma-3-27b-it:free",
                "Gemma 3 27B (OpenRouter Free)",
                ["or-gemma"],
            ),
            (
                "openrouter/qwen/qwen3-32b:free",
                "Qwen 3 32B (OpenRouter Free)",
                ["or-qwen"],
            ),
            (
                "openrouter/deepseek/deepseek-r1:free",
                "DeepSeek R1 (OpenRouter Free)",
                ["or-deepseek"],
            ),
            (
                "openrouter/microsoft/phi-4-reasoning-plus:free",
                "Phi 4 Reasoning Plus (OpenRouter Free)",
                ["or-phi"],
            ),
        ],
    }

    OPENROUTER_PROVIDER_PRIORITY = [
        "OpenRouter - Anthropic",
        "OpenRouter - OpenAI",
        "OpenRouter - Google",
        "OpenRouter - Meta Llama",
        "OpenRouter - DeepSeek",
        "OpenRouter - Qwen",
    ]

    OPENROUTER_PROVIDER_NAMES = {
        "anthropic": "Anthropic",
        "openai": "OpenAI",
        "meta-llama": "Meta Llama",
        "google": "Google",
        "deepseek": "DeepSeek",
        "qwen": "Qwen",
        "microsoft": "Microsoft",
        "mistral": "Mistral",
        "perplexity": "Perplexity",
        "fireworks": "Fireworks",
        "together": "Together",
        "replicate": "Replicate",
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
        self._env_wee_models = None
        self._openrouter_cache_ts = 0  # TTL timestamp for wee model discovery cache
        self._openrouter_models_cache: Optional[Dict] = (
            None  # cached fetch_openrouter_models() result
        )
        self._openrouter_models_cache_ts: float = (
            0  # TTL timestamp for fetch_openrouter_models()
        )

        # Load command timeout from environment
        self.command_timeout = get_command_timeout()

        # Session idle timeout — sessions inactive longer than this are
        # candidates for cleanup.  Defaults to 30 min; override via env var.
        self.session_idle_timeout = int(os.environ.get("SESSION_IDLE_TIMEOUT", "1800"))

        # Session map TTL — entries inactive longer than this are evicted on every save.
        # Defaults to 30 days; override via SESSION_MAP_TTL_DAYS env var.
        self.session_map_ttl = int(os.environ.get("SESSION_MAP_TTL_DAYS", "30")) * 86400

        # Lock for session map file read-modify-write to prevent TOCTOU races
        self._session_map_lock = threading.Lock()
        self._session_map_cache: Optional[Dict] = None

        # Per-session streaming queues: session_id -> (asyncio.Queue, event_loop)
        # Streaming manager: per-session queues and replay buffers.
        self.streaming_manager = StreamingManager()
        # Keep dict refs so existing API handler code keeps working unchanged.
        self._stream_queues = self.streaming_manager._queues
        self._stream_buffers = self.streaming_manager._buffers

        # Last subprocess exit code per n8n_session_id (for debugging/monitoring)
        self._last_exit_codes: Dict[str, int] = {}

        # Per-session live status for mobile channel progress updates (F004).
        # Maps n8n_session_id -> {"text": str, "updated_at": float}
        self._live_status: Dict[str, Dict] = {}
        self._live_status_lock = threading.Lock()

        # CLI command handler (F020) — slash command registry + dispatcher.
        self.cli_handler = CliCommandHandler()
        # Keep registry ref for backward compat with callers using the raw dict.
        self._slash_command_registry = self.cli_handler._registry
        # Runtime executor — strategy registry for per-runtime dispatch.
        self.runtime_executor = RuntimeExecutor()
        self._init_slash_commands()
        self._register_runtime_executors()

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
        """Register a slash command via the CLI handler."""
        self.cli_handler.register(command, handler, description)

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
        return self.cli_handler.list_commands()

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
                [sys.executable, secret_tool, "list", "--backend", "pass"],
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
        """Handle /notifications slash command."""
        if not argument:
            argument = "current"

        # Resolve identity for per-user preference store
        _notif_identity = self._bg_identity or session_data.get("identity")
        _notif_channel = session_data.get("channel", "webui")

        if argument == "current":
            # Check global preference first, then per-identity, then session
            if self._notification_mgr:
                if self._notification_mgr.is_muted("_global"):
                    pref = "off"
                elif _notif_identity:
                    pref = self._notification_mgr.get_user_pref(_notif_identity)
                else:
                    pref = session_data.get("notification_preference", "all")
            else:
                pref = session_data.get("notification_preference", "all")
            status = "ON (All updates)" if pref == "all" else "OFF (WebUI only)"
            return f"🔔 **Background Notifications:** `{status}`"

        elif argument in ["on", "all"]:
            self.update_session_field(n8n_session_id, "notification_preference", "all")
            if self._notification_mgr:
                # Store under specific identity if available
                if _notif_identity:
                    self._notification_mgr.set_user_pref(
                        _notif_identity, _notif_channel, "all"
                    )
                # Always store global preference so it applies across all channels
                self._notification_mgr.set_user_pref("_global", _notif_channel, "all")
            return "✓ Background task notifications enabled for Telegram/WebEx."

        elif argument in ["off", "mute"]:
            self.update_session_field(n8n_session_id, "notification_preference", "off")
            if self._notification_mgr:
                if _notif_identity:
                    self._notification_mgr.set_user_pref(
                        _notif_identity, _notif_channel, "off"
                    )
                # Always store global preference so it applies across all channels
                self._notification_mgr.set_user_pref("_global", _notif_channel, "off")
            return (
                "✓ Background task notifications muted for Telegram/WebEx (WebUI only)."
            )
        else:
            return "Usage: `/notifications [on|off]` to toggle background task notifications."

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

        if not bg_prompt:
            return "❌ No prompt provided. Usage: `/background <prompt>`"

        channel = session_data.get("channel", "webui")
        identity = self._bg_identity or "unknown"

        running = self._bg_task_mgr.count_running(channel, identity)
        if running >= BackgroundTaskManager.MAX_TASKS_PER_USER:
            return f"❌ Maximum {BackgroundTaskManager.MAX_TASKS_PER_USER} concurrent background tasks allowed."

        bg_timeout = (
            bg_timeout_override
            if bg_timeout_override is not None
            else get_bg_command_timeout()
        )
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
                        "path": agent.get("path", ""),
                        "description": agent.get("description", ""),
                        "max_concurrent": agent.get("max_concurrent", 1),
                        "runtime": agent.get("runtime", "copilot"),
                        "model": agent.get("model", ""),
                    }
                return agents
        except json.JSONDecodeError as e:
            print(f"[Error] Failed to parse agents config: {e}", file=sys.stderr)
            return {}
        except Exception as e:
            print(f"[Error] Failed to load agents config: {e}", file=sys.stderr)
            return {}

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
                "path": agent.get("path", ""),
                "description": agent.get("description", ""),
                "max_concurrent": agent.get("max_concurrent", 1),
                "runtime": agent.get("runtime", "copilot"),
                "model": agent.get("model", ""),
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
            "Claude Models": [
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
                        for kw in ["gpt", "claude", "gemini", "o1", "o3", "o4"]
                    )
                ]

            # Method 2: Fallback (if regex fails due to layout changes)
            if not models:
                # Look for known models as a sanity check/fallback
                fallback_models = [
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
                    "Claude Models": [
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
        except ValueError as e:
            return f"Error: {e}"
        except subprocess.TimeoutExpired:
            print(
                f"[Error] opencode models command timed out after {self.command_timeout}s",
                file=sys.stderr,
            )
            return self._static_models_to_dict(self.OPENCODE_MODELS)
        except Exception as e:
            print(f"Error fetching opencode models: {e}", file=sys.stderr)
            return self._static_models_to_dict(self.OPENCODE_MODELS)

    def _static_models_to_dict(self, static_dict: Dict) -> Dict:
        """Convert static model config {cat: [(id, desc, aliases)...]} to {cat: [id,...]}."""
        return {
            cat: [model_id for model_id, _desc, _aliases in entries]
            for cat, entries in static_dict.items()
        }

    # Human-readable labels for known OpenRouter provider prefixes
    OPENROUTER_PROVIDER_NAMES = {
        "meta-llama": "Meta Llama",
        "anthropic": "Anthropic",
        "google": "Google",
        "openai": "OpenAI",
        "deepseek": "DeepSeek",
        "mistralai": "Mistral AI",
        "qwen": "Qwen",
        "microsoft": "Microsoft",
        "nvidia": "NVIDIA",
        "cohere": "Cohere",
        "perplexity": "Perplexity",
        "x-ai": "xAI",
        "01-ai": "01.AI",
        "amazon": "Amazon",
        "nousresearch": "Nous Research",
        "liquid": "Liquid",
        "bytedance": "ByteDance",
    }

    # Priority order for OpenRouter provider groups in the model selector
    OPENROUTER_PROVIDER_PRIORITY = [
        "OpenRouter - Anthropic",
        "OpenRouter - OpenAI",
        "OpenRouter - Google",
        "OpenRouter - Meta Llama",
        "OpenRouter - DeepSeek",
        "OpenRouter - Mistral AI",
        "OpenRouter - xAI",
    ]

    def fetch_openrouter_models(self) -> Dict:
        """Fetch ALL available models from the OpenRouter API, grouped by provider.

        Returns {category: [(model_id, description, aliases), ...]} where model_id
        uses the "openrouter/<provider>/<name>" prefix understood by wee_runtime.py.

        Resolution order:
          1. Per-call 300s TTL cache (self._openrouter_models_cache)
          2. Live API call to https://openrouter.ai/api/v1/models
          3. Static WEE_MODELS["OpenRouter Models"] fallback

        Authentication: keyring("openrouter", "api_key") -> OPENROUTER_API_KEY env var
        """
        static_fallback = {
            "OpenRouter Models": list(self.WEE_MODELS.get("OpenRouter Models", []))
        }

        cache_ttl = 300
        if (
            self._openrouter_models_cache is not None
            and time.time() - self._openrouter_models_cache_ts < cache_ttl
        ):
            return self._openrouter_models_cache

        api_key = None
        try:
            import keyring as _keyring

            api_key = _keyring.get_password("openrouter", "api_key")
        except Exception:
            pass
        if not api_key:
            api_key = os.getenv("OPENROUTER_API_KEY")

        if not api_key:
            print(
                "[wee] OpenRouter: no API key available, using static fallback",
                file=sys.stderr,
            )
            return static_fallback

        try:
            import urllib.request as _urlreq

            req = _urlreq.Request(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": "Bearer " + api_key},
            )
            resp = _urlreq.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            all_models = data.get("data", [])

            static_aliases = {
                e[0]: e[2] for e in self.WEE_MODELS.get("OpenRouter Models", [])
            }

            grouped = {}
            for m in all_models:
                mid = m.get("id", "")
                if not mid:
                    continue
                name = m.get("name", mid)
                or_id = "openrouter/" + mid
                aliases = static_aliases.get(or_id, [])

                provider_prefix = mid.split("/")[0] if "/" in mid else mid
                friendly = self.OPENROUTER_PROVIDER_NAMES.get(
                    provider_prefix,
                    provider_prefix.replace("-", " ").title(),
                )
                category = "OpenRouter - " + friendly
                grouped.setdefault(category, []).append((or_id, name, aliases))

            if not grouped:
                return static_fallback

            for cat in grouped:
                grouped[cat].sort(key=lambda t: t[1].lower())

            ordered = {}
            for priority_cat in self.OPENROUTER_PROVIDER_PRIORITY:
                if priority_cat in grouped:
                    ordered[priority_cat] = grouped.pop(priority_cat)
            for cat in sorted(grouped):
                ordered[cat] = grouped[cat]

            total = sum(len(v) for v in ordered.values())
            print(
                f"[wee] OpenRouter: discovered {total} models in {len(ordered)} groups",
                file=sys.stderr,
            )

            self._openrouter_models_cache = ordered
            self._openrouter_models_cache_ts = time.time()
            return ordered

        except Exception as e:
            print(f"[wee] OpenRouter discovery failed: {e}", file=sys.stderr)
            return static_fallback

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

    def fetch_wee_models(self) -> Dict:
        """Return available wee models: local Ollama + OpenRouter cloud models.

        Resolution order:
          1. WEE_MODELS_JSON env var (custom model list)
          2. Live Ollama discovery + live OpenRouter discovery (300s TTL cache)
          3. Static WEE_MODELS fallback
        """
        # Check for env var override first
        env_models = os.getenv("WEE_MODELS_JSON")
        if env_models:
            try:
                models_dict = json.loads(env_models)
                normalized = {}
                for cat, entries in models_dict.items():
                    normalized[cat] = [
                        tuple(e) if isinstance(e, list) else e for e in entries
                    ]
                self._env_wee_models = normalized
                return self._static_models_to_dict(normalized)
            except (json.JSONDecodeError, ValueError):
                pass

        cache_ttl = 300  # 5 minutes

        # Return cache if still valid
        if (
            self._env_wee_models is not None
            and time.time() - self._openrouter_cache_ts < cache_ttl
        ):
            return self._static_models_to_dict(self._env_wee_models)

        # Start with static Ollama models
        result = {}
        for cat, entries in self.WEE_MODELS.items():
            result[cat] = list(entries)

        # Try live Ollama discovery
        try:
            import httpx

            resp = httpx.get(
                "http://192.168.1.101:11434/api/tags",
                timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0),
            )
            if resp.status_code == 200:
                tags = resp.json().get("models", [])
                ollama_models = [(f"ollama/{t['name']}", t["name"], []) for t in tags]
                if ollama_models:
                    # Merge aliases from static WEE_MODELS into discovered models
                    static_ollama = {
                        e[0]: e for e in self.WEE_MODELS.get("Ollama Models", [])
                    }
                    merged_ollama = []
                    discovered_ids = set()
                    for or_id, name, _ in ollama_models:
                        if or_id in static_ollama:
                            merged_ollama.append(static_ollama[or_id])
                        else:
                            merged_ollama.append((or_id, name, []))
                        discovered_ids.add(or_id)
                    # Add static models not found via live discovery (preserves aliases)
                    for entry in self.WEE_MODELS.get("Ollama Models", []):
                        if entry[0] not in discovered_ids:
                            merged_ollama.append(entry)
                    result["Ollama Models"] = merged_ollama
        except Exception:
            pass

        # Try live OpenRouter discovery via fetch_openrouter_models()
        try:
            or_models = self.fetch_openrouter_models()
            if or_models and any(v for v in or_models.values()):
                # Remove the static OpenRouter Models key and replace with dynamic groups
                result.pop("OpenRouter Models", None)
                result.update(or_models)
        except Exception as e:
            print(f"[wee] OpenRouter discovery error: {e}", file=sys.stderr)

        self._env_wee_models = result
        self._openrouter_cache_ts = time.time()
        return self._static_models_to_dict(result)

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
            self._session_map_cache = {}
            return {}

        try:
            with open(self.session_map_file, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            data = {}
        self._session_map_cache = data
        return data

    def _prune_session_map_ttl(self, session_map: dict) -> dict:
        """Return a copy of session_map with entries older than session_map_ttl removed.

        An entry is considered inactive if its ``last_activity`` timestamp is
        older than ``self.session_map_ttl`` seconds.  Entries that lack a
        ``last_activity`` field are kept so legacy data is not silently dropped.
        """
        now = time.time()
        cutoff = now - self.session_map_ttl
        pruned = {}
        evicted = 0
        for key, entry in session_map.items():
            last_activity = (
                entry.get("last_activity") if isinstance(entry, dict) else None
            )
            if last_activity is not None and last_activity < cutoff:
                evicted += 1
                continue
            pruned[key] = entry
        if evicted:
            print(
                f"[SessionMap] TTL evicted {evicted} inactive entries "
                f"(threshold {self.session_map_ttl / 86400:.0f}d)",
                file=__import__('sys').stderr,
            )
        return pruned

    def save_session_map(self, session_map: dict):
        """Save the N8N -> Session ID mapping (caller must hold _session_map_lock)"""
        session_map = self._prune_session_map_ttl(session_map)
        with open(self.session_map_file, "w") as f:
            json.dump(session_map, f, indent=2)
        self._session_map_cache = dict(session_map)

    def get_cached_session_count(self) -> int:
        """Return the in-memory session count without touching disk."""
        cached = getattr(self, "_session_map_cache", None)
        if not isinstance(cached, dict):
            return 0
        return len(cached)

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
                current_model = merged.get("model", "")
                if not current_model or not self.get_model_from_name(
                    current_model, "wee"
                ):
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

        with self._session_map_lock:
            session_map = self.load_session_map()

            # Generate a new session ID for the backend because sessions are often project-scoped
            new_backend_session_id = str(uuid4())

            if n8n_session_id not in session_map:
                session_map[n8n_session_id] = {
                    "session_id": new_backend_session_id,
                    "model": "gpt-5-mini",
                    "agent": agent,
                    "runtime": "copilot",
                }
            else:
                if isinstance(session_map[n8n_session_id], dict):
                    session_map[n8n_session_id]["agent"] = agent
                    session_map[n8n_session_id]["session_id"] = new_backend_session_id
                else:
                    # Convert old format
                    session_map[n8n_session_id] = {
                        "session_id": new_backend_session_id,
                        "model": "gpt-5-mini",
                        "agent": agent,
                        "runtime": "copilot",
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
        if runtime in ("claude", "claude-sdk", "gemini", "codex", "devin", "cursor", "wee"):
            self.get_models_for_runtime(runtime)

        # Step 1: check env-loaded or static alias tables for all runtimes that have them.
        env_alias_map = {
            "claude": self._env_claude_models,
            "claude-sdk": self._env_claude_models,
            "gemini": self._env_gemini_models,
            "codex": self._env_codex_models,
            "devin": self._env_devin_models,
            "cursor": self._env_cursor_models,
            "wee": self._env_wee_models,
        }
        static_alias_map = {
            "claude": self.CLAUDE_MODELS,
            "claude-sdk": self.CLAUDE_MODELS,
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

        # Exact match with provider prefix stripped (e.g., "gemma4:e4b" matches "ollama/gemma4:e4b")
        for m in all_models:
            model_lower = m.lower()
            if "/" in model_lower:
                suffix = model_lower.split("/", 1)[1]
                if suffix == name_lower:
                    return m

        # Substring matching with shortest-match preference
        matches = [m for m in all_models if name_lower in m.lower()]
        if len(matches) == 1:
            return matches[0]
        if matches:
            matches.sort(key=len)
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
            # CODEX output format (when stripped of headers):
            # 1. First response line (often echoed/repeated)
            # 2. Header section (OpenAI Codex...)
            # 3. Metadata (workdir, model, etc.)
            # 4. "user" marker + user input/context
            # 5. File listings
            # 6. "thinking" marker + reasoning
            # 7. "codex" marker + actual response(s)
            # 8. "tokens used" metadata

            found_codex_marker = False
            response_lines = []

            for i, line in enumerate(lines):
                line_lower = line.lower()

                # Track if we've hit the "codex" marker - only keep content after this
                if line_lower.strip() == "codex":
                    found_codex_marker = True
                    continue

                # Stop at tokens metadata
                if "tokens" in line_lower and "used" in line_lower:
                    break

                # Before codex marker, skip everything
                if not found_codex_marker:
                    continue

                # After codex marker, skip empty lines at start
                if not line.strip() and not response_lines:
                    continue

                # Keep the actual response content
                response_lines.append(line)

            # Clean up trailing empty lines
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
        """Execute a direct command without an implicit shell.

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
            argv = _split_command_args(command)
            # Execute the command with the configured timeout
            argv = _split_command_args(command)
            result = subprocess.run(
                argv,
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

        except ValueError as e:
            return f"Error: {e}"
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {self.command_timeout} seconds"
        except Exception as e:
            return f"Error executing command: {str(e)}"

    def _execute_shell_command(self, command: str, agent: str = "orchestrator") -> str:
        """Execute a trusted shell command with full bash semantics.

        This is reserved for agent-authored shell tool calls that rely on pipes,
        redirects, and command chaining. User-controlled scheduler command mode
        must continue to use argv parsing via _execute_bash_command.
        """
        if not command:
            return "Error: No command provided. Usage: !<command>"

        agent_info = self.AGENTS.get(agent)
        if not agent_info:
            agent_dir = str(Path.cwd())
        else:
            agent_dir = agent_info["path"]

        print(f"[Shell] Executing in {agent_dir}: {command}", file=sys.stderr)

        try:
            result = subprocess.run(
                ["bash", "-o", "pipefail", "-c", command],
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
                cwd=agent_dir,
            )

            output = result.stdout
            if result.stderr:
                output += result.stderr

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

    def _execute_shell_command(self, command: str, agent: str = "orchestrator") -> str:
        """Execute a trusted shell command with full bash semantics.

        This is reserved for agent-authored shell tool calls that rely on pipes,
        redirects, and command chaining. User-controlled scheduler command mode
        must continue to use argv parsing via _execute_bash_command.
        """
        if not command:
            return "Error: No command provided. Usage: !<command>"

        agent_info = self.AGENTS.get(agent)
        if not agent_info:
            agent_dir = str(Path.cwd())
        else:
            agent_dir = agent_info["path"]

        print(f"[Shell] Executing in {agent_dir}: {command}", file=sys.stderr)

        try:
            result = subprocess.run(
                ["bash", "-o", "pipefail", "-c", command],
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
                cwd=agent_dir,
            )

            output = result.stdout
            if result.stderr:
                output += result.stderr

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
- /notifications <on|off> - Toggle background task notifications for Telegram/WebEx
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


    def _get_or_create_stream_buffer(self, session_id: str):
        """Get existing buffer for session or create a new one."""
        return self.streaming_manager.get_or_create_buffer(session_id)

    def _register_stream(
        self, session_id: str, queue, loop  # asyncio.Queue, asyncio.AbstractEventLoop
    ) -> None:
        """Register an asyncio queue for the /stream endpoint to receive chunks."""
        self.streaming_manager.register_stream(session_id, queue, loop)

    def _unregister_stream(self, session_id: str, queue=None) -> None:
        """Remove the streaming queue for a session.

        If *queue* is provided, only remove that consumer from the buffer
        (the buffer itself stays alive for reconnection).  The legacy
        ``_stream_queues`` entry is removed regardless so that new streams
        can register without conflict.
        """
        self.streaming_manager.unregister_stream(session_id, queue)

    def _cleanup_stream_buffer(self, session_id: str) -> None:
        """Remove the stream buffer entirely (called after query completes)."""
        self.streaming_manager.cleanup_buffer(session_id)

    def _cleanup_stale_stream_buffers(self, max_age: float = 600.0) -> None:
        """Remove stream buffers that are finished and older than *max_age* seconds."""
        self.streaming_manager.cleanup_stale_buffers(max_age)

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
                    bufsize=1,  # line-buffered for faster streaming chunk delivery
                    env=_sub_env,
                )

            self.track_running_query(
                n8n_session_id, process.pid, runtime, agent, prompt
            )

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
                                # Parse stream-json output and push text deltas + tool calls
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
                                                # Push newline separator between text blocks
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
                                                tool_id = cb.get(
                                                    "id", f"tool_{cb_index}"
                                                )
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
                                                    stream_buffer.push(
                                                        "tool_call", tc_event
                                                    )
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
                                                        stream_buffer.push(
                                                            "chunk", {"text": text}
                                                        )
                                                    else:
                                                        loop.call_soon_threadsafe(
                                                            queue.put_nowait,
                                                            ("chunk", {"text": text}),
                                                        )
                                            elif delta_type == "input_json_delta":
                                                partial = delta.get("partial_json", "")
                                                if cb_index in _active_tool_calls:
                                                    _active_tool_calls[cb_index][
                                                        "input_parts"
                                                    ].append(partial)
                                                    tc_event = {
                                                        "event": "input_delta",
                                                        "id": _active_tool_calls[
                                                            cb_index
                                                        ]["id"],
                                                        "partial_json": partial,
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
                                        elif inner_type == "content_block_stop":
                                            cb_index = event.get("index", 0)
                                            if cb_index in _active_tool_calls:
                                                tc_info = _active_tool_calls.pop(
                                                    cb_index
                                                )
                                                full_input = "".join(
                                                    tc_info["input_parts"]
                                                )
                                                try:
                                                    parsed_input = (
                                                        _json.loads(full_input)
                                                        if full_input
                                                        else {}
                                                    )
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
                                                    stream_buffer.push(
                                                        "tool_call", tc_event
                                                    )
                                                else:
                                                    loop.call_soon_threadsafe(
                                                        queue.put_nowait,
                                                        ("tool_call", tc_event),
                                                    )
                                    elif evt_type == "assistant":
                                        # Parse tool results from assistant messages
                                        msg = obj.get("message") or {}
                                        for block in msg.get("content") or []:
                                            if block.get("type") == "tool_result":
                                                tc_event = {
                                                    "event": "result",
                                                    "id": block.get("tool_use_id", ""),
                                                    "is_error": block.get(
                                                        "is_error", False
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
                                except (ValueError, KeyError, AttributeError):
                                    pass
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
                                                "output": _gobj.get("output", "")[:500],
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

        # Expand home path for MCP config file
        mcp_config_path = os.path.expanduser("~/.copilot/mcp-config.json")

        cmd = [
            self.copilot_bin,
            "-p",
            context_prompt,
            "--allow-all-tools",
            "--no-color",
            "--model",
            model,
            "--additional-mcp-config",
            f"@{mcp_config_path}",
        ]

        # Add elevated flags for full access
        if mode == "elevated":
            cmd.insert(4, "--allow-all-paths")
            cmd.append("--yolo")

        if resume and session_id:
            cmd.extend(["--resume", session_id])
            print(f"[Session] Resuming Copilot session: {session_id}", file=sys.stderr)
        else:
            print(
                f"[Session] Starting new Copilot session in {mode} permission mode",
                file=sys.stderr,
            )

        output = self._execute_subprocess_with_tracking(
            cmd, agent_dir, effective_timeout, "copilot", agent, prompt, n8n_session_id
        )
        return self.strip_metadata(output, "copilot")

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
                                tc_evt = {
                                    "event": "completed",
                                    "id": block.tool_use_id
                                    or f"tc_claude-sdk_{_tool_call_counter[0]}",
                                    "name": "tool",
                                    "input": (
                                        str(block.content)[:200]
                                        if block.content
                                        else ""
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
            cmd, agent_dir, effective_timeout, "opencode", agent, prompt, n8n_session_id
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
            cmd.extend(["--resume", session_id])
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
            cmd, agent_dir, effective_timeout, "claude", agent, prompt, n8n_session_id
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
        # Always use stream-json for structured output to ensure clean response extraction
        # and consistent tool call tracking.
        cmd.extend(["-o", "stream-json"])
        cmd.append(context_prompt)

        # Note: Gemini CLI appears to have model handling issues with specified model names
        # For now, we use the default model and do not pass --model flag
        # TODO: Investigate correct model names for --model flag with Gemini CLI

        # For Gemini, we always try to resume the latest session for context retention.
        # The session_id parameter is not used since Gemini manages sessions internally.
        # Using "--resume latest" automatically continues with the most recent session.
        if resume:
            cmd.extend(["--resume", "latest"])
            print(f"[Session] Resuming Gemini session (latest)", file=sys.stderr)
        else:
            print(
                f"[Session] Starting new Gemini session in {mode} mode", file=sys.stderr
            )

        output = self._execute_subprocess_with_tracking(
            cmd, agent_dir, effective_timeout, "gemini", agent, prompt, n8n_session_id
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
            # Resume existing session - flags must come before session_id positional arg
            # codex exec resume supports --dangerously-bypass-approvals-and-sandbox
            cmd = ["codex", "exec", "resume"]
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
            # Start new session - flags must come BEFORE the prompt positional arg
            cmd = ["codex", "exec"]
            if mode == "elevated":
                # Bypass all sandbox restrictions (sudo, DNS, network, filesystem)
                cmd.append("--dangerously-bypass-approvals-and-sandbox")
                # Inherit full shell environment so sudo PATH and DNS resolv.conf are available
                cmd += ["-c", "shell_environment_policy.inherit=all"]
            if model:
                cmd += ["-m", model]
            cmd.append(context_prompt)
            print(
                f"[Session] Starting new CODEX session with model {model} in {mode} mode",
                file=sys.stderr,
            )

        output = self._execute_subprocess_with_tracking(
            cmd, agent_dir, effective_timeout, "codex", agent, prompt, n8n_session_id
        )

        if "Error: CODEX command failed" in output:
            return output

        return self.strip_metadata(output, "codex")

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
        mode: str = "restricted",
    ) -> str:
        """Execute Devin CLI in non-interactive mode.

        Uses --permission-mode dangerous to bypass all approval prompts
        and allow full system access.
        """
        # Parse /mode command from prompt
        prompt, parsed_mode = self._parse_mode_command(prompt)

        # Get session data once - reuse for mode and channel
        session_data = self.get_or_create_session_data(n8n_session_id)

        # Prefer explicitly passed-in mode over re-deriving from session data.
        # This ensures scheduler/background dispatches with --mode elevated
        # propagate correctly instead of being lost to session defaults.
        if mode != "restricted":
            # Explicit mode was passed in (e.g. from _dispatch_single_runtime)
            pass
        elif parsed_mode != "restricted":
            # /mode command was found in the prompt
            mode = parsed_mode
        else:
            # Fall back to session data resolution
            mode = self._resolve_permission_mode(session_data, parsed_mode)

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
        # Permission mode: dangerous (auto-approve all) for elevated, normal for restricted/sandboxed
        # Devin CLI valid values: normal, dangerous, bypass (NOT "auto")
        permission_mode = "dangerous" if mode == "elevated" else "normal"
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

        Connects to any OpenAI-compatible API endpoint (Ollama, OpenRouter,
        LM Studio, etc.) using the openai Python package. No external CLI
        binary required.

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
        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout
        channel = session_data.get("channel", "webui")

        # -- Resolve model, endpoint, and API key --
        api_base = session_data.get("api_base") or os.environ.get("WEE_API_BASE")
        api_key = session_data.get("api_key") or os.environ.get("WEE_API_KEY")

        # Provider presets
        _PRESETS = {
            "ollama": ("http://192.168.1.101:11434/v1", "ollama"),
            "openrouter": ("https://openrouter.ai/api/v1", None),
            "lmstudio": ("http://localhost:1234/v1", "lm-studio"),
        }

        resolved_model = model
        print(f"[wee-runtime] Session model: {model}", file=sys.stderr)
        for prefix, (preset_base, preset_key) in _PRESETS.items():
            if model.lower().startswith(f"{prefix}/"):
                resolved_model = model[len(prefix) + 1 :]
                if not api_base:
                    api_base = preset_base
                if not api_key and preset_key:
                    api_key = preset_key
                break

        if not api_base:
            api_base = "http://192.168.1.101:11434/v1"
        if not api_key:
            # Issue #153: Check OPENROUTER_API_KEY env var for OpenRouter models
            if "openrouter" in api_base.lower():
                api_key = os.environ.get("OPENROUTER_API_KEY")
            # Try keyring for OpenRouter
            if not api_key and "openrouter" in api_base.lower():
                try:
                    import keyring

                    api_key = keyring.get_password("openrouter", "api_key")
                except Exception:
                    pass
            # Issue #153: Raise clear error instead of defaulting to "ollama"
            if not api_key:
                if "openrouter" in api_base.lower():
                    raise ValueError(
                        "OpenRouter API key not found. Set OPENROUTER_API_KEY "
                        'env var or store via: python3 -c "import keyring; '
                        "keyring.set_password('openrouter', 'api_key', "
                        "'sk-or-...')\".\"."
                    )
                api_key = "ollama"

        print(
            f"[Wee Native] model={resolved_model} api_base={api_base} "
            f"session={n8n_session_id[:8]}...",
            file=sys.stderr,
        )

        # Issue #111: Build context prompt with correct args (after model resolution)
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
        # Issue #111: Augment system prompt with explicit tool capability section
        # so models that ignore JSON schemas still know tools are available.
        context_prompt = self._wee_augment_system_prompt_with_tools(base_context_prompt)
        # Issue #113: Augment system prompt with anti-hallucination rules
        context_prompt += self._wee_anti_hallucination_prompt()

        # -- Streaming infrastructure --
        stream_buffer = getattr(self, "_stream_buffers", {}).get(n8n_session_id)

        # -- Create OpenAI client and call API --
        # Use httpx.Timeout for granular control: fast connect failure,
        # generous read timeout for streaming
        import httpx

        client = OpenAI(
            base_url=api_base,
            api_key=api_key,
            timeout=httpx.Timeout(
                timeout=effective_timeout,
                connect=15.0,
            ),
            max_retries=0,
        )

        # -- Issue #108: Load conversation history --
        messages = self._wee_load_messages(n8n_session_id, context_prompt, resume)
        messages.append({"role": "user", "content": prompt})

        # -- Tool definitions for agentic loop (Issue #107) --
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

        collected_output = []
        _tool_call_counter = 0
        MAX_TOOL_ROUNDS = 10
        # Issue #160: Track token usage across all rounds
        _total_prompt_tokens = 0
        _total_completion_tokens = 0
        _usage_available = False

        # Issue #160: Track token usage across tool rounds
        _total_prompt_tokens = 0
        _total_completion_tokens = 0
        _usage_available = False

        try:
            for round_num in range(MAX_TOOL_ROUNDS + 1):
                # Build create kwargs — include tools unless on final safety round
                create_kwargs = {
                    "model": resolved_model,
                    "messages": messages,
                    "stream": True,
                    # Issue #160: Request usage stats in streaming response
                    "stream_options": {"include_usage": True},
                }
                if round_num < MAX_TOOL_ROUNDS:
                    create_kwargs["tools"] = _WEE_TOOLS

                try:
                    stream = client.chat.completions.create(**create_kwargs)
                except Exception as tools_err:
                    # Some models/endpoints may not support tools or stream_options
                    retried = False
                    if "tools" in create_kwargs:
                        print(
                            f"[Wee Native] Tools not supported, retrying without: {tools_err}",
                            file=sys.stderr,
                        )
                        create_kwargs.pop("tools", None)
                        try:
                            stream = client.chat.completions.create(**create_kwargs)
                            retried = True
                        except Exception:
                            pass  # fall through to stream_options removal
                    if not retried and "stream_options" in create_kwargs:
                        # Issue #160: Ollama/LM Studio may not support stream_options
                        print(
                            f"[Wee Native] stream_options not supported, retrying without: {tools_err}",
                            file=sys.stderr,
                        )
                        create_kwargs.pop("stream_options", None)
                        stream = client.chat.completions.create(**create_kwargs)
                    elif not retried:
                        raise

                # Accumulate content and tool calls from streaming response
                round_content = []
                tool_calls_acc = {}  # index -> {id, name, arguments}

                for chunk in stream:
                    # Issue #160: Capture usage stats from final streaming chunk
                    if hasattr(chunk, "usage") and chunk.usage is not None:
                        _u = chunk.usage
                        _total_prompt_tokens += getattr(_u, "prompt_tokens", 0) or 0
                        _total_completion_tokens += (
                            getattr(_u, "completion_tokens", 0) or 0
                        )
                        _usage_available = True

                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    # Content tokens — stream to user
                    if delta.content:
                        token = delta.content
                        round_content.append(token)
                        if stream_buffer:
                            stream_buffer.push("chunk", {"text": token})

                    # Tool call deltas (Issue #107)
                    if getattr(delta, "tool_calls", None):
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_calls_acc:
                                _tool_call_counter += 1
                                tool_calls_acc[idx] = {
                                    "id": getattr(tc_delta, "id", None)
                                    or f"tc_wee_{_tool_call_counter}",
                                    "name": "",
                                    "arguments": "",
                                }
                            if tc_delta.id and not tool_calls_acc[idx]["id"].startswith(
                                "tc_wee_"
                            ):
                                pass  # keep first real id
                            elif tc_delta.id:
                                tool_calls_acc[idx]["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    tool_calls_acc[idx]["name"] = tc_delta.function.name
                                if tc_delta.function.arguments:
                                    tool_calls_acc[idx][
                                        "arguments"
                                    ] += tc_delta.function.arguments

                content_text = "".join(round_content)

                # No tool calls — we have the final answer
                if not tool_calls_acc:
                    collected_output.append(content_text)
                    messages.append({"role": "assistant", "content": content_text})
                    break

                # -- Tool calls detected (Issues #107 + #109) --
                print(
                    f"[Wee Native] Round {round_num + 1}: {len(tool_calls_acc)} tool call(s) detected",
                    file=sys.stderr,
                )

                # Build assistant message with tool_calls for conversation history
                assistant_tool_calls = []
                for idx in sorted(tool_calls_acc.keys()):
                    tc = tool_calls_acc[idx]
                    assistant_tool_calls.append(
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        }
                    )

                assistant_msg = {
                    "role": "assistant",
                    "content": content_text or None,
                    "tool_calls": assistant_tool_calls,
                }
                messages.append(assistant_msg)

                # Execute each tool call and emit SSE events (Issue #109)
                for tc_entry in assistant_tool_calls:
                    tc_id = tc_entry["id"]
                    func_name = tc_entry["function"]["name"]
                    func_args_str = tc_entry["function"]["arguments"]

                    # Parse arguments
                    try:
                        func_args = _json.loads(func_args_str)
                    except (ValueError, _json.JSONDecodeError):
                        func_args = {"raw": func_args_str}

                    # Issue #109: Emit tool start event to SSE stream
                    tc_start_event = {
                        "id": tc_id,
                        "name": func_name,
                        "arguments": func_args,
                        "status": "running",
                    }
                    if stream_buffer:
                        stream_buffer.push("tool_call", tc_start_event)

                    print(
                        f"[Wee Native] Tool: {func_name}({_json.dumps(func_args)[:200]})",
                        file=sys.stderr,
                    )

                    # Execute the tool
                    tool_result = self._wee_execute_tool(func_name, func_args, agent)

                    # Issue #109: Emit tool complete event to SSE stream
                    tc_done_event = {
                        "id": tc_id,
                        "name": func_name,
                        "arguments": func_args,
                        "result": tool_result[:2000] if tool_result else "",
                        "status": "complete",
                    }
                    if stream_buffer:
                        stream_buffer.push("tool_call", tc_done_event)

                    # Append tool result to conversation for next round
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": tool_result or "No output",
                        }
                    )

            else:
                # All MAX_TOOL_ROUNDS had tool calls with no final text
                last_tool_results = [
                    m["content"] for m in messages if m.get("role") == "tool"
                ]
                if last_tool_results:
                    collected_output.append(
                        "Tool execution completed. Last result:\n"
                        + last_tool_results[-1][:2000]
                    )
                else:
                    collected_output.append(
                        "Max tool rounds reached without final response."
                    )

            output = "".join(collected_output)

            # Issue #112: Fallback when LLM generates empty synthesis after tool execution.
            # Some models (e.g. qwen3:8b) return zero text tokens after processing
            # tool results, yielding output=''. Surface the last tool result instead.
            if not output.strip():
                tool_results = [
                    m["content"]
                    for m in messages
                    if m.get("role") == "tool" and m.get("content")
                ]
                if tool_results:
                    last_result = tool_results[-1]
                    output = f"Tool execution result:\n{last_result[:4000]}"
                    print(
                        f"[Wee Native] Empty synthesis fallback: surfacing last tool result ({len(last_result)} chars)",
                        file=sys.stderr,
                    )
                    if stream_buffer:
                        stream_buffer.push("chunk", {"text": output})
                elif any(m.get("role") == "tool" for m in messages):
                    output = "(Tool executed but produced no output)"
                    print(
                        "[Wee Native] Empty synthesis fallback: tool produced no output",
                        file=sys.stderr,
                    )
                    if stream_buffer:
                        stream_buffer.push("chunk", {"text": output})

            # Issue #108: Persist conversation history
            self._wee_save_messages(n8n_session_id, messages)

            # Issue #160: Build and store wee_meta with token usage and cost
            _wee_meta = self._build_wee_meta(
                api_base,
                resolved_model,
                model,
                _total_prompt_tokens,
                _total_completion_tokens,
                _usage_available,
            )
            self.update_session_field(n8n_session_id, "_wee_meta", _wee_meta)

            # Push done sentinel
            if stream_buffer:
                stream_buffer.push("done", output)

            print(
                f"[Wee Native] Completed. Output length: {len(output)} chars, "
                f"tokens: {_wee_meta.get('tokens', 'N/A')}",
                file=sys.stderr,
            )
            return output

        except Exception as e:
            error_msg = f"Error: Wee native runtime failed: {e}"
            print(f"[Wee Native] {error_msg}", file=sys.stderr)

            # Push error as done sentinel
            if stream_buffer:
                stream_buffer.push("done", error_msg)

            return error_msg

    # -- Wee runtime helper methods (Issues #107, #108, #109) --

    # Issue #160: Model pricing per 1M tokens (input, output) in USD
    _WEE_MODEL_PRICING = {
        # OpenRouter pricing (per 1M tokens)
        "google/gemini-2.5-flash-preview": (0.15, 0.60),
        "google/gemini-2.5-pro-preview": (1.25, 10.00),
        "google/gemini-2.0-flash-001": (0.10, 0.40),
        "anthropic/claude-sonnet-4": (3.00, 15.00),
        "anthropic/claude-3.5-sonnet": (3.00, 15.00),
        "anthropic/claude-haiku-4": (0.80, 4.00),
        "anthropic/claude-3.5-haiku": (0.80, 4.00),
        "openai/gpt-4.1": (2.00, 8.00),
        "openai/gpt-4.1-mini": (0.40, 1.60),
        "openai/gpt-4.1-nano": (0.10, 0.40),
        "openai/gpt-4o": (2.50, 10.00),
        "openai/gpt-4o-mini": (0.15, 0.60),
        "meta-llama/llama-4-maverick": (0.20, 0.60),
        "meta-llama/llama-4-scout": (0.15, 0.40),
        "meta-llama/llama-3.3-70b-instruct": (0.10, 0.15),
        "deepseek/deepseek-chat-v3-0324": (0.30, 0.88),
        "deepseek/deepseek-r1": (0.55, 2.19),
        "qwen/qwen3-235b-a22b": (0.20, 0.60),
        "microsoft/mai-ds-r1": (0.55, 2.19),
        "nvidia/llama-3.1-nemotron-ultra-253b-v1": (0.00, 0.00),
    }

    def _build_wee_meta(
        self,
        api_base: str,
        resolved_model: str,
        original_model: str,
        prompt_tokens: int,
        completion_tokens: int,
        usage_available: bool,
    ) -> dict:
        """Build wee_meta dict with token usage and estimated cost (Issue #160).

        Returns a dict suitable for inclusion in the SSE done event:
        {runtime, tokens, prompt_tokens, completion_tokens, cost_label}
        """
        meta = {"runtime": "wee"}
        is_ollama = "11434" in (api_base or "") or (api_base or "").startswith(
            "http://192.168.1.101"
        )
        is_openrouter = "openrouter" in (api_base or "").lower()
        is_lmstudio = "1234" in (api_base or "")

        if not usage_available:
            if is_ollama or is_lmstudio:
                meta["cost_label"] = "local"
            return meta

        total = prompt_tokens + completion_tokens
        meta["tokens"] = total
        meta["prompt_tokens"] = prompt_tokens
        meta["completion_tokens"] = completion_tokens

        # Determine cost label
        if is_ollama or is_lmstudio:
            meta["cost_label"] = "local"
        elif is_openrouter:
            # Look up pricing — try full model ID, then with prefix
            pricing = None
            for candidate in [original_model, resolved_model]:
                candidate_lower = candidate.lower() if candidate else ""
                # Strip openrouter/ prefix if present
                if candidate_lower.startswith("openrouter/"):
                    candidate_lower = candidate_lower[len("openrouter/") :]
                for key, val in self._WEE_MODEL_PRICING.items():
                    if key.lower() == candidate_lower or candidate_lower.startswith(
                        key.lower()
                    ):
                        pricing = val
                        break
                if pricing:
                    break
            if pricing:
                input_cost = (prompt_tokens / 1_000_000) * pricing[0]
                output_cost = (completion_tokens / 1_000_000) * pricing[1]
                total_cost = input_cost + output_cost
                if total_cost < 0.001:
                    meta["cost_label"] = f"${total_cost:.6f}"
                elif total_cost < 0.01:
                    meta["cost_label"] = f"${total_cost:.4f}"
                else:
                    meta["cost_label"] = f"${total_cost:.2f}"
            elif any(":free" in (original_model or "").lower() for _ in [1]):
                meta["cost_label"] = "free"
            else:
                meta["cost_label"] = "est. N/A"
        else:
            meta["cost_label"] = ""

        return meta

    def _wee_load_messages(
        self,
        n8n_session_id: str,
        context_prompt: str,
        resume: bool = True,
    ) -> list:
        """Load wee conversation history from session map.

        Issue #108: Ollama is stateless — the full conversation must be
        included in every request.  This loads persisted messages from the
        session_map so multi-turn context is preserved.
        """
        if resume:
            session_data = self.load_session_data(n8n_session_id)
            if session_data and session_data.get("wee_messages"):
                msgs = list(session_data["wee_messages"])
                # Always refresh the system prompt to pick up context changes
                if msgs and msgs[0].get("role") == "system":
                    msgs[0]["content"] = context_prompt
                elif context_prompt:
                    msgs.insert(0, {"role": "system", "content": context_prompt})
                return msgs

        # Fresh conversation — start with system prompt
        messages = []
        if context_prompt:
            messages.append({"role": "system", "content": context_prompt})
        return messages

    def _wee_save_messages(self, n8n_session_id: str, messages: list) -> None:
        """Persist wee conversation history to session map.

        Issue #108: Saves the full message array (system + user + assistant +
        tool) so the next turn can reconstruct the conversation.
        Caps at MAX_WEE_MESSAGES to prevent unbounded growth.
        """
        MAX_WEE_MESSAGES = 100
        with self._session_map_lock:
            session_map = self.load_session_map()
            if n8n_session_id in session_map:
                # Keep system prompt + last N messages
                if len(messages) > MAX_WEE_MESSAGES:
                    system_msgs = [m for m in messages if m.get("role") == "system"]
                    non_system = [m for m in messages if m.get("role") != "system"]
                    saved = (
                        system_msgs
                        + non_system[-(MAX_WEE_MESSAGES - len(system_msgs)) :]
                    )
                else:
                    saved = list(messages)
                # Strip tool_calls from assistant messages for JSON serialization
                clean = []
                for m in saved:
                    if m.get("tool_calls"):
                        mc = dict(m)
                        mc["tool_calls"] = [
                            {
                                "id": (
                                    tc.get("id", "")
                                    if isinstance(tc, dict)
                                    else getattr(tc, "id", "")
                                ),
                                "type": "function",
                                "function": {
                                    "name": (
                                        tc.get("function", {}).get("name", "")
                                        if isinstance(tc, dict)
                                        else getattr(
                                            getattr(tc, "function", None), "name", ""
                                        )
                                    ),
                                    "arguments": (
                                        tc.get("function", {}).get("arguments", "")
                                        if isinstance(tc, dict)
                                        else getattr(
                                            getattr(tc, "function", None),
                                            "arguments",
                                            "",
                                        )
                                    ),
                                },
                            }
                            for tc in m["tool_calls"]
                        ]
                        clean.append(mc)
                    else:
                        clean.append(m)
                session_map[n8n_session_id]["wee_messages"] = clean
                self.save_session_map(session_map)

    def _wee_augment_system_prompt_with_tools(self, system_prompt: str) -> str:
        """Issue #111: Append explicit tool capability declaration to system prompt.

        Many Ollama models ignore JSON tool schemas entirely and respond as if
        no tools exist. Explicitly stating tool availability in the system
        prompt text reliably fixes this across all models.
        """
        tool_section = (
            "\n[Available Tools]\n"
            "You have access to the following tools. ALWAYS use them when the user asks you to\n"
            "perform any action -- do NOT say you cannot do something that these tools enable.\n"
            "\n"
            "**bash** -- Execute a bash shell command and return its output.\n"
            '  Call: bash tool with {"command": "your shell command here"}\n'
            "  Use for: running commands, SSH, file operations, checking system state\n"
            "\n"
            "**python** -- Execute Python 3 code and return its output.\n"
            '  Call: python tool with {"code": "your python code here"}\n'
            "  Use for: data processing, calculations, scripting, file parsing\n"
            "\n"
            "CRITICAL: When asked to run a command, SSH somewhere, check system status,\n"
            "list files, or perform any shell action -- call the bash tool immediately.\n"
            "NEVER refuse or claim you lack capability. The tools are active and functional."
        )
        return system_prompt + tool_section

    def _wee_execute_tool(self, func_name: str, func_args: dict, agent: str) -> str:
        """Execute a tool call from the wee runtime agentic loop.

        Issue #107: Supports bash and python tools. Issue #111: SSH sanitization wired in.
        Uses the same _execute_bash_command infrastructure as other runtimes.
        """
        try:
            if func_name == "bash":
                command = func_args.get("command", "")
                if not command:
                    return "Error: No command provided"
                # Issue #111: Sanitize SSH commands (wire #113 fix)
                command = self._wee_sanitize_bash_command(command)
                if self._SHELL_GRAMMAR_RE.search(command):
                    return self._execute_shell_command(command, agent)
                return self._execute_bash_command(command, agent)
            elif func_name == "python":
                code = func_args.get("code", "")
                if not code:
                    return "Error: No code provided"
                agent_info = self.AGENTS.get(agent, self.AGENTS.get("orchestrator"))
                cwd = agent_info["path"] if agent_info else str(Path.cwd())
                result = subprocess.run(
                    [sys.executable, "-c", code],
                    capture_output=True,
                    text=True,
                    timeout=min(self.command_timeout, 120),
                    cwd=cwd,
                )
                output = result.stdout
                if result.stderr:
                    output += ("\n" if output else "") + result.stderr
                if not output.strip():
                    if result.returncode == 0:
                        return "✓ Executed successfully (exit code: 0)"
                    else:
                        return f"✗ Failed with exit code: {result.returncode}"
                return output.strip()
            else:
                return f"Error: Unknown tool '{func_name}'. Available: bash, python"
        except subprocess.TimeoutExpired:
            return f"Error: Tool '{func_name}' timed out"
        except Exception as e:
            return f"Error executing {func_name}: {e}"

    # -- Issue #113: SSH command sanitisation and anti-hallucination --

    _SSH_BIN_RE = re.compile(r"\b(ssh|scp|sftp)\b")
    _SHELL_GRAMMAR_RE = re.compile(r"(\|\||&&|[|;<>`]|[$][(]|\n)")

    # Issue #111: SSH sanitization wired into _wee_execute_tool (resolves #113 TODO).
    # The wee runtime now has a full tool execution loop.
    @staticmethod
    def _wee_sanitize_bash_command(command: str) -> str:
        """Auto-inject SSH flags to prevent host key verification failures.

        When a bash command contains an ssh/scp/sftp invocation without
        StrictHostKeyChecking already set, inject
        ``-o StrictHostKeyChecking=accept-new`` so first-connect succeeds
        without manual intervention.  ``accept-new`` is safer than ``no``
        because it still rejects CHANGED keys (potential MITM).

        Wired into _wee_execute_tool by Issue #111. Called on every bash tool input before
        execution in the wee runtime tool execution loop.
        """
        if not command:
            return command
        # Quick check — does the command even mention ssh/scp/sftp?
        if not SessionManager._SSH_BIN_RE.search(command):
            return command
        # Already has StrictHostKeyChecking set — leave it alone
        if "StrictHostKeyChecking" in command:
            return command

        # Inject -o StrictHostKeyChecking=accept-new after each ssh/scp/sftp binary
        def _inject(m):
            return m.group(0) + " -o StrictHostKeyChecking=accept-new"

        return SessionManager._SSH_BIN_RE.sub(_inject, command, count=0)

    @staticmethod
    def _wee_anti_hallucination_prompt() -> str:
        """Issue #113: Return system-prompt section that prevents hallucinated tool output.

        Smaller Ollama models tend to fabricate command output when a tool
        call fails.  This prompt section explicitly forbids that.
        """
        return (
            "\n\n[CRITICAL — Output Integrity Rules]\n"
            "1. NEVER fabricate, invent, or hallucinate command output. If a command "
            "fails or you cannot execute it, report the EXACT error message.\n"
            "2. NEVER provide example or placeholder output and present it as real. "
            'If you show an example, clearly label it as "EXAMPLE (not real output)".\n'
            "3. When a tool call returns an error, relay the error verbatim to the user. "
            "Do NOT attempt to guess what the successful output would have looked like.\n"
            "4. For SSH commands: ALWAYS use ``-o StrictHostKeyChecking=accept-new`` to "
            "avoid host-key verification failures on first connect.\n"
        )

    def _get_cursor_session_id(self, n8n_session_id: str) -> Optional[str]:
        """Return the stored cursor session flag for this n8n session, or None."""
        mapping_file = self.cursor_session_dir / f"{n8n_session_id}.json"
        try:
            with open(mapping_file) as f:
                data = json.load(f)
                return data.get("cursor_session_active")
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _save_cursor_session_id(self, n8n_session_id: str):
        """Mark that a cursor session exists for this n8n session."""
        try:
            self.cursor_session_dir.mkdir(parents=True, exist_ok=True)
            mapping_file = self.cursor_session_dir / f"{n8n_session_id}.json"
            with open(mapping_file, "w") as f:
                json.dump(
                    {"cursor_session_active": True, "n8n_session_id": n8n_session_id}, f
                )
            print(
                f"[Session] Stored cursor session mapping: {n8n_session_id[:8]}...",
                file=sys.stderr,
            )
        except Exception as e:
            print(
                f"[Session] Warning: could not save cursor session ID: {e}",
                file=sys.stderr,
            )

    def _get_devin_session_id(self, n8n_session_id: str) -> Optional[str]:
        """Return the stored devin session UUID for this n8n session, or None."""
        mapping_file = self.devin_session_dir / f"{n8n_session_id}.json"
        try:
            with open(mapping_file) as f:
                data = json.load(f)
                return data.get("devin_session_id")
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _save_devin_session_id(
        self, n8n_session_id: str, devin_bin: str, cwd: Optional[str] = None
    ):
        """Capture the most recently active devin session UUID and store it."""
        try:
            result = subprocess.run(
                [devin_bin, "list", "--format", "json"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=cwd,
            )
            sessions = json.loads(result.stdout)
            if not sessions:
                return
            # Most recent session is first in the list
            devin_sid = sessions[0].get("id")
            if not devin_sid:
                return
            mapping_file = self.devin_session_dir / f"{n8n_session_id}.json"
            with open(mapping_file, "w") as f:
                json.dump({"devin_session_id": devin_sid}, f)
            print(
                f"[Session] Stored devin session mapping: {n8n_session_id[:8]}... → {devin_sid[:8]}...",
                file=sys.stderr,
            )
        except Exception as e:
            print(
                f"[Session] Warning: could not save devin session ID: {e}",
                file=sys.stderr,
            )

    def session_exists(
        self, session_id: str, runtime: str, n8n_session_id: Optional[str] = None
    ) -> bool:
        """Check if session state exists for runtime"""
        if runtime in ("copilot", "copilot-sdk"):
            # Modern Copilot stores sessions as directories with events.jsonl inside
            session_dir = self.session_state_dir / session_id
            if session_dir.is_dir() and (session_dir / "events.jsonl").exists():
                return True
            # Legacy: also check for old .jsonl format at root level
            return (self.session_state_dir / f"{session_id}.jsonl").exists()
        elif runtime == "opencode":
            if not session_id or not session_id.startswith("ses_"):
                return False
            try:
                for session_file in self._find_opencode_session_files():
                    if session_file.stem == session_id:
                        return True
            except Exception:
                pass
            return False
        elif runtime in ("claude", "claude-sdk"):
            if not session_id:
                return False
            # Verify session actually exists in Claude's project storage.
            # Claude stores sessions as {session_id}.jsonl under ~/.claude/projects/*/
            # A pre-generated UUID from session map creation won't have a file yet,
            # so this correctly returns False on first message and True after Claude
            # has created the session and we've captured its ID.
            claude_projects_dir = Path.home() / ".claude" / "projects"
            try:
                return any(
                    (p / f"{session_id}.jsonl").exists()
                    for p in claude_projects_dir.iterdir()
                    if p.is_dir()
                )
            except (OSError, FileNotFoundError):
                return False
        elif runtime == "gemini":
            return (self.gemini_session_dir / f"{session_id}.json").exists()
        elif runtime == "codex":
            # CODEX stores sessions in nested date-based directories
            # Format: ~/.codex/sessions/YYYY/MM/DD/rollout-YYYY-MM-DDTHH-MM-SS-SESSION_ID.jsonl
            # Session ID is a UUID at the end of the filename
            try:
                for session_file in self.codex_session_dir.glob(
                    "*/*/*/rollout-*.jsonl"
                ):
                    # Extract the UUID from the filename (last 36 chars before .jsonl)
                    filename = session_file.name.replace(".jsonl", "")
                    file_session_id = (
                        filename[-36:] if len(filename) >= 36 else filename
                    )
                    if file_session_id == session_id:
                        return True
            except Exception:
                pass
            return False
        elif runtime == "devin":
            # Devin session mappings are keyed by n8n_session_id, not backend session_id
            key = n8n_session_id if n8n_session_id else session_id
            return (self.devin_session_dir / f"{key}.json").exists()
        elif runtime == "cursor":
            # Cursor session mappings are keyed by n8n_session_id
            key = n8n_session_id if n8n_session_id else session_id
            return (self.cursor_session_dir / f"{key}.json").exists()
        elif runtime == "wee":
            # Issue #108: Wee stores conversation history in session_map
            data = self.load_session_data(n8n_session_id or session_id)
            return bool(data and data.get("wee_messages"))
        return False

    def get_most_recent_session_id(
        self, runtime: str, agent: str = "devops"
    ) -> Optional[str]:
        """Get most recent session ID from storage or CLI"""
        try:
            if runtime in ("copilot", "copilot-sdk"):
                # Modern Copilot: sessions are directories with events.jsonl inside
                session_dirs = [
                    d
                    for d in self.session_state_dir.iterdir()
                    if d.is_dir() and (d / "events.jsonl").exists()
                ]
                if session_dirs:
                    # Sort by modification time of events.jsonl
                    dirs_sorted = sorted(
                        session_dirs,
                        key=lambda d: (d / "events.jsonl").stat().st_mtime,
                        reverse=True,
                    )
                    return dirs_sorted[0].name
                # Legacy fallback: check for old .jsonl format
                files = sorted(
                    self.session_state_dir.glob("*.jsonl"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                return files[0].stem if files else None
            elif runtime == "opencode":
                # Prefer filesystem lookup because `opencode session list` may fail on some hosts
                # (for example due to sqlite/model service issues) even when session files exist.
                files = self._find_opencode_session_files()
                if files:
                    files_sorted = sorted(
                        files,
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    return files_sorted[0].stem

                # Fallback to CLI listing if no files found.
                agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
                env = os.environ.copy()
                env["PAGER"] = "cat"
                cmd = [str(self.opencode_bin), "session", "list"]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, cwd=agent_dir, env=env
                )
                if result.returncode != 0:
                    return None
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line.startswith("ses_"):
                        return line.split()[0]
                return None
            elif runtime == "claude":
                # Prefer the 'latest' symlink if it exists (fastest)
                latest_link = self.claude_debug_dir / "latest"
                if latest_link.is_symlink():
                    target = latest_link.resolve()
                    if target.exists() and target.suffix == ".txt":
                        return target.stem
                # Fallback: most recently modified .txt file in debug dir
                files = sorted(
                    self.claude_debug_dir.glob("*.txt"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                return files[0].stem if files else None
            elif runtime == "gemini":
                files = sorted(
                    self.gemini_session_dir.glob("*.json"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                return files[0].stem if files else None
            elif runtime == "codex":
                # CODEX stores sessions in nested date directories
                # Filenames: rollout-YYYY-MM-DDTHH-MM-SS-SESSION_ID.jsonl
                files = sorted(
                    self.codex_session_dir.glob("*/*/*/rollout-*.jsonl"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if files:
                    # Extract session ID from filename
                    # Format: rollout-2025-12-15T22-39-34-019b242b-476d-7f90-8bfa-4eb0c7095532.jsonl
                    # The session ID is the UUID at the end (last 36 chars before .jsonl)
                    filename = files[0].name
                    # Remove .jsonl extension and get the last 36 characters (UUID)
                    name_without_ext = filename.replace(".jsonl", "")
                    # Session ID should be the last UUID-like part
                    session_id = (
                        name_without_ext[-36:]
                        if len(name_without_ext) >= 36
                        else name_without_ext
                    )
                    return session_id
                return None
            elif runtime == "devin":
                # Devin CLI does not persist local session files
                return None
            elif runtime == "cursor":
                # Cursor agent CLI does not persist local session files
                return None
        except Exception as e:
            print(f"Error getting recent session ID: {e}", file=sys.stderr)
            return None

    def _find_opencode_session_files(self) -> List[Path]:
        """Find OpenCode session JSON files across known storage layouts."""
        storage_root = Path.home() / ".local" / "share" / "opencode" / "storage"
        candidates = []
        # Newer OpenCode layout observed on this host.
        session_diff_dir = storage_root / "session_diff"
        if session_diff_dir.exists():
            candidates.extend(session_diff_dir.glob("ses_*.json"))
        # Legacy layout used by older OpenCode versions.
        session_dir = storage_root / "session"
        if session_dir.exists():
            candidates.extend(session_dir.glob("*/ses_*.json"))
            candidates.extend(session_dir.glob("ses_*.json"))
        return [p for p in candidates if p.is_file()]

    # Background task support
    _bg_task_mgr = None  # Set by create_api_app
    _bg_identity = None  # Set per-call for slash command context
    _notification_mgr = None  # Set by create_api_app

    def _execute_background_task(
        self, task_id, session_id, prompt, agent, runtime, model, channel, timeout=None
    ):
        """Run a background task in the current thread (called from thread pool)."""
        self.get_or_create_session_data(session_id)
        self.update_session_field(session_id, "agent", agent)
        self.update_session_field(session_id, "model", model)
        self.update_session_field(session_id, "runtime", runtime)
        self.update_session_field(session_id, "channel", channel)
        self.update_session_field(session_id, "render_type", "text")
        # Background tasks run unattended — grant elevated permissions so
        # SDK runtimes (copilot-sdk, claude-sdk) don't block on approval gates
        self.update_session_field(session_id, "permissions", {"mode": "elevated"})
        if timeout is not None:
            self.update_session_field(session_id, "timeout", timeout)
        try:
            result = self.execute(prompt, session_id)
            if self._bg_task_mgr:
                self._bg_task_mgr.complete_task(task_id, result)
                task_rec = self._bg_task_mgr.get_task(task_id)
                o_sid = task_rec.get("origin_session_id") if task_rec else None
                if o_sid:
                    self._bg_task_mgr.push_bg_event(
                        o_sid,
                        {
                            "task_id": task_id,
                            "summary": prompt[:80],
                            "status": "completed",
                            "agent": agent,
                            "timestamp": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ",
                                time.gmtime(),
                            ),
                        },
                    )

        except Exception as exc:
            if self._bg_task_mgr:
                self._bg_task_mgr.fail_task(task_id, str(exc))
                task_rec = self._bg_task_mgr.get_task(task_id)
                o_sid = task_rec.get("origin_session_id") if task_rec else None
                if o_sid:
                    self._bg_task_mgr.push_bg_event(
                        o_sid,
                        {
                            "task_id": task_id,
                            "summary": prompt[:80],
                            "status": "failed",
                            "agent": agent,
                            "timestamp": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ",
                                time.gmtime(),
                            ),
                        },
                    )

    def _dispatch_single_runtime(
        self,
        runtime: str,
        prompt: str,
        model: str,
        agent: str,
        session_id: Optional[str],
        can_resume: bool,
        n8n_session_id: str,
        effective_timeout: int,
        render_type: str,
        mode: str = "restricted",
    ) -> str:
        """Dispatch prompt to a single runtime and return the output."""
        # Touch before dispatch to keep session alive during long operations
        self.touch_session(n8n_session_id)

        handler = self.runtime_executor.get(runtime)
        if handler is None:
            return f"Error: Unknown runtime '{runtime}'"

        result = handler(
            prompt,
            model,
            agent,
            session_id if can_resume else None,
            can_resume,
            n8n_session_id,
            effective_timeout,
            render_type,
            mode,
        )

        # Touch after dispatch to record completion activity
        self.touch_session(n8n_session_id)
        return result

    def _register_runtime_executors(self) -> None:
        """Populate runtime_executor with per-runtime handler wrappers.

        Wrappers have a uniform signature:
            handler(prompt, model, agent, session_id, can_resume,
                    n8n_session_id, timeout, render_type, mode)
        Runtimes that do not accept *mode* receive it but ignore it.
        """

        def _mode_handler(fn):
            """Pass-through wrapper for API uniformity — all runtime dispatch uses the same 9-arg signature."""
            def _h(prompt, model, agent, session_id, can_resume, n8n_session_id, timeout, render_type, mode):
                return fn(prompt, model, agent, session_id, can_resume, n8n_session_id, timeout, render_type, mode)
            return _h

        def _no_mode_handler(fn):
            def _h(prompt, model, agent, session_id, can_resume, n8n_session_id, timeout, render_type, _mode):
                return fn(prompt, model, agent, session_id, can_resume, n8n_session_id, timeout, render_type)
            return _h

        for rt, fn in [
            ("copilot-sdk", self.run_copilot_sdk),
            ("claude", self.run_claude),
            ("claude-sdk", self.run_claude_sdk),
            ("devin", self.run_devin),
        ]:
            self.runtime_executor.register(rt, _mode_handler(fn))

        for rt, fn in [
            ("copilot", self.run_copilot),
            ("opencode", self.run_opencode),
            ("gemini", self.run_gemini),
            ("codex", self.run_codex),
            ("cursor", self.run_cursor),
            ("wee", self.run_wee_native),
        ]:
            self.runtime_executor.register(rt, _no_mode_handler(fn))

    def execute(self, prompt: str, n8n_session_id: str) -> str:
        """Main execution logic"""
        # Touch session to mark activity and prevent cleanup
        self.touch_session(n8n_session_id)

        # Get session data first — pass identity so it's persisted in the
        # session map, enabling preference lookups by identity later.
        session_data = self.get_or_create_session_data(
            n8n_session_id, identity=self._bg_identity
        )
        current_runtime = session_data.get("runtime", "copilot")
        current_agent = session_data.get("agent", "orchestrator")

        # Check for bash command (prompts starting with !)
        if prompt.startswith("!"):
            return self._execute_bash_command(prompt[1:].strip(), current_agent)

        # First check for explicit slash commands
        command, argument = self.parse_slash_command(prompt)

        # If not a slash command, check for implicit agent delegation
        if command is None:
            delegated_agent, cleaned_prompt = self.detect_agent_delegation(prompt)
            if delegated_agent and delegated_agent in self.AGENTS:
                # User asked for specific agent help - auto-delegate
                print(
                    f"[Auto-Delegate] Detected request for '{delegated_agent}' agent",
                    file=sys.stderr,
                )
                return self._execute_with_context(
                    cleaned_prompt,
                    {
                        "session_id": str(uuid4()),
                        "model": session_data.get("model", "gpt-5-mini"),
                        "agent": delegated_agent,
                        "runtime": current_runtime,
                        "is_delegation": True,
                    },
                    n8n_session_id,
                )

        # --- Slash Commands ---

        # F020: Check registry for pure-server handlers first.
        # Commands with a handler bypass the LLM entirely.
        if command and command in self._slash_command_registry:
            entry = self._slash_command_registry[command]
            if entry.get("handler"):
                return entry["handler"](argument, session_data, n8n_session_id)

        # --- Execution ---

        # Prepare for execution
        session_id = session_data.get("session_id")
        model = session_data.get("model", "gpt-5-mini")
        agent = session_data.get("agent", "orchestrator")
        effective_timeout = self.get_effective_timeout(session_data)
        render_type = self.get_render_type(session_data)
        # Get permission mode from session (backward compat with yolo_mode)
        _perms = (
            session_data.get("permissions") or {}
        )  # Handle None from session template
        if isinstance(_perms, dict) and _perms.get("mode") in (
            "elevated",
            "restricted",
            "sandboxed",
        ):
            mode = _perms["mode"]
        elif session_data.get("yolo_mode") == "on":
            mode = "elevated"
        else:
            mode = "restricted"

        # --- Direct Single-Runtime Dispatch (simplified from auto-runtime logic) ---

        # Check if we can resume
        # For Gemini, we always try to resume the latest session for context retention
        if current_runtime == "gemini":
            can_resume = True  # Always attempt to resume latest Gemini session
        elif current_runtime == "devin":
            # Devin handles its own session resumption internally via
            # _get_devin_session_id(); pass n8n_session_id for correct lookup
            can_resume = (
                self.session_exists(
                    session_id, current_runtime, n8n_session_id=n8n_session_id
                )
                if session_id
                else self.session_exists(
                    "", current_runtime, n8n_session_id=n8n_session_id
                )
            )
        elif current_runtime == "cursor":
            # Cursor handles session resumption via --continue flag
            can_resume = (
                self.session_exists(
                    session_id, current_runtime, n8n_session_id=n8n_session_id
                )
                if session_id
                else self.session_exists(
                    "", current_runtime, n8n_session_id=n8n_session_id
                )
            )
        elif current_runtime == "wee":
            # Issue #108 fix: Wee has no external session_id — history is keyed
            # by n8n_session_id in session_map.  Must pass n8n_session_id so
            # session_exists() finds wee_messages regardless of session_id.
            can_resume = self.session_exists(
                session_id, current_runtime, n8n_session_id=n8n_session_id
            )
        else:
            can_resume = (
                self.session_exists(session_id, current_runtime)
                if session_id
                else False
            )

        output = self._dispatch_single_runtime(
            current_runtime,
            prompt,
            model,
            agent,
            session_id,
            can_resume,
            n8n_session_id,
            effective_timeout,
            render_type,
            mode,
        )

        # Handle session ID mapping for runtimes that auto-generate IDs
        if not can_resume and current_runtime in (
            "copilot",
            "opencode",
            "gemini",
            "codex",
        ):
            new_id = self.get_most_recent_session_id(current_runtime, agent)
            if new_id:
                self.update_session_field(n8n_session_id, "session_id", new_id)

        # Handle opencode session loss
        if (
            current_runtime == "opencode"
            and can_resume
            and ("Resource not found" in output or "NotFoundError" in output)
        ):
            print(
                f"[Session] Session {session_id} lost/corrupted. Starting new session.",
                file=sys.stderr,
            )
            output = self._dispatch_single_runtime(
                "opencode",
                prompt,
                model,
                agent,
                None,
                False,
                n8n_session_id,
                effective_timeout,
                render_type,
                mode,
            )
            new_id = self.get_most_recent_session_id("opencode", agent)
            if new_id:
                self.update_session_field(n8n_session_id, "session_id", new_id)

        # Post-process output for telegram_html to ensure Telegram compatibility
        if render_type == "telegram_html":
            # Sanitize output to remove unsupported tags
            output = self.sanitize_telegram_html(output)

        return output


def _check_command_result(result: str, error_keywords: List[str]) -> None:
    """Helper function to check command results and exit on error

    Args:
        result: The output from executing a command
        error_keywords: List of keywords that indicate an error occurred

    Raises:
        SystemExit: If any error keywords are found in the result
    """
    for keyword in error_keywords:
        if keyword in result:
            print(result, file=sys.stderr)
            sys.exit(1)


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

_api_auth_manager: Optional["AuthManager"] = None


def _send_pairing_code(channel: str, identity: str, code: str) -> bool:
    """Deliver a pairing code. Returns True on success, False on failure."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        if channel == "telegram":
            from telegram_connector import TelegramConnector

            config_path = os.path.join(script_dir, "telegram_config.json")
            with open(config_path) as f:
                cfg = json.load(f)
            token = cfg.get("token") or os.getenv("TELEGRAM_BOT_TOKEN", "")
            connector = TelegramConnector(token, config_file=config_path)
            last_exc = None
            for attempt in range(1, 4):
                try:
                    connector.send_message(
                        int(identity),
                        f"Your pairing code is: {code}\nIt expires in 5 minutes.",
                    )
                    return True
                except Exception as _exc:
                    last_exc = _exc
                    print(
                        f"[API] Telegram send attempt {attempt}/3 failed: {_exc}",
                        file=sys.stderr,
                    )
                    if attempt < 3:
                        time.sleep(2)
            print(
                f"[API] All 3 Telegram send attempts failed: {last_exc}",
                file=sys.stderr,
            )
            return False
        elif channel == "webex":
            config_path = os.path.join(script_dir, "webex_config.json")
            with open(config_path) as f:
                cfg = json.load(f)
            token = (
                cfg.get("bot_token")
                or cfg.get("token")
                or os.getenv("WEBEX_BOT_TOKEN", "")
            )
            msg = f"Your pairing code is: **{code}**\nIt expires in 5 minutes."
            # Route to the correct WebEx target based on identity format:
            #   email       → toPersonEmail
            #   person ID   → toPersonId  (base64 of ciscospark://us/PEOPLE/...)
            #   anything else → roomId
            import re as _re
            import base64 as _b64

            if _re.match(r"[^@]+@[^@]+\.[^@]+", identity):
                payload = {"toPersonEmail": identity, "text": msg, "markdown": msg}
            else:
                # Detect WebEx person IDs (base64-encoded ciscospark://us/PEOPLE/...)
                _is_person_id = False
                try:
                    _padded = identity + "=" * (-len(identity) % 4)
                    _decoded = _b64.b64decode(_padded).decode("utf-8", errors="replace")
                    _is_person_id = "/PEOPLE/" in _decoded
                except Exception:
                    pass
                if _is_person_id:
                    payload = {"toPersonId": identity, "text": msg, "markdown": msg}
                else:
                    payload = {"roomId": identity, "text": msg, "markdown": msg}
            import requests as _req

            resp = _req.post(
                "https://webexapis.com/v1/messages",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10,
            )
            if resp.status_code != 200:
                print(
                    f"[API] WebEX send failed ({resp.status_code}): {resp.text[:200]}",
                    file=sys.stderr,
                )
                return False
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"[API] Warning: could not send pairing code via {channel}: {exc}")
        return False


def _get_telegram_username(user_id: str):
    """Look up @username for a numeric Telegram user_id in telegram_config.json.
    Returns the username string (without @), or None if not found."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "telegram_config.json")
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        pairing = cfg.get("user_pairings", {}).get(str(user_id), {})
        username = pairing.get("username", "")
        return username.lstrip("@") if username else None
    except Exception:
        return None


def _ddg_image_search(query: str, max_results: int = 4) -> list:
    """Fetch image results from DuckDuckGo without an API key.
    Returns list of {url, thumbnail, title, source} dicts."""
    import re as _re

    import requests as _req

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://duckduckgo.com/",
    }
    try:
        # Step 1: get VQD token
        r1 = _req.get(
            "https://duckduckgo.com/",
            params={"q": query},
            headers=headers,
            timeout=8,
        )
        m = _re.search(r"vqd=([\d-]+)", r1.text)
        if not m:
            return []
        vqd = m.group(1)
        # Step 2: fetch image JSON
        r2 = _req.get(
            "https://duckduckgo.com/i.js",
            params={
                "q": query,
                "o": "json",
                "l": "us-en",
                "s": "0",
                "f": ",,,,,",
                "p": "1",
                "vqd": vqd,
            },
            headers=headers,
            timeout=8,
        )
        out = []
        for item in r2.json().get("results", [])[:max_results]:
            if item.get("image"):
                out.append(
                    {
                        "url": item["image"],
                        "thumbnail": item.get("thumbnail", item["image"]),
                        "title": item.get("title", ""),
                        "source": item.get("source", ""),
                    }
                )
        return out
    except Exception:
        return []


def _resolve_telegram_identity(username: str):
    """Reverse-lookup @username in telegram_config.json user_pairings.
    Returns numeric user_id string, or None if not found (user must message bot first).
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "telegram_config.json")
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        for uid, pairing in cfg.get("user_pairings", {}).items():
            if pairing.get("username", "").lower() == username.lower():
                return uid
        return None
    except Exception:
        return None


def _compute_bg_task_defaults(session_map, identity, channel):
    """Compute inheritable defaults from existing sessions for a new background task.

    Only safe fields (notification_preference, runtime, model) are inherited.
    The 'agent' field is intentionally excluded to prevent session agent leakage
    (see issue #75).

    Args:
        session_map: dict of n8n_sid -> session_data
        identity: user identity string
        channel: auth channel string

    Returns:
        dict with only safe inherited fields (never contains 'agent')
    """
    SAFE_FIELDS = ("notification_preference", "runtime", "model")
    matching_same = []
    matching_other = []

    for n8n_sid, data in session_map.items():
        if isinstance(data, str):
            data = {"session_id": data}
        sid_identity = data.get("identity")
        sid_channel = data.get("channel")
        if sid_identity and sid_identity == identity:
            if sid_channel == channel:
                matching_same.append(data)
            else:
                matching_other.append(data)

    defaults = {}
    for data in matching_same + matching_other:
        if not defaults:
            for key in SAFE_FIELDS:
                if key in data:
                    defaults[key] = data[key]
        # Always prefer notification_preference from any matching session
        pref = data.get("notification_preference")
        if pref and not defaults.get("notification_preference"):
            defaults["notification_preference"] = pref

    return defaults


def create_api_app():  # noqa: C901 – factory kept in one place intentionally
    """Factory that builds and returns the FastAPI application."""
    import asyncio
    import concurrent.futures
    import mimetypes
    from enum import Enum

    from fastapi import (
        FastAPI,
        File,
        Header,
        HTTPException,
        Query,
        Request,
        UploadFile,
        WebSocket,
        WebSocketDisconnect,
    )
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import (
        FileResponse,
        JSONResponse,
        Response,
        StreamingResponse,
    )
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, field_validator

    global _api_auth_manager

    # ---- configuration from environment ----
    APP_ENV = os.environ.get("APP_ENV", "PROD").upper()
    IS_PRODUCTION = APP_ENV != "DEV"
    SHARED_KEY = os.environ.get("API_SHARED_KEY", "")
    if not SHARED_KEY:
        print(
            "[SECURITY][WARN] API shared key is empty — authentication is effectively disabled. Set API_SHARED_KEY env var.",
            file=sys.stderr,
        )
    PAIRING_CODE_LENGTH = int(os.environ.get("PAIRING_CODE_LENGTH", "6"))
    PAIRING_CODE_TTL = int(os.environ.get("PAIRING_CODE_TTL", "300"))
    SESSION_TOKEN_TTL = int(os.environ.get("SESSION_TOKEN_TTL", "3600"))
    SESSION_TOKEN_ABSOLUTE_TTL = int(
        os.environ.get("SESSION_TOKEN_ABSOLUTE_TTL", "86400")
    )
    CONFIG_FILE = os.environ.get("AGENT_CONFIG_FILE")
    SCHEDULER_JOBS_FILE = os.environ.get(
        "SCHEDULER_JOBS_FILE",
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".task-scheduler", "jobs.json"
        ),
    )
    SCHEDULER_ENABLED = os.environ.get(
        "SCHEDULER_ENABLED", "true"
    ).strip().lower() not in ("false", "0", "no")

    # ---- shared instances ----
    auth_mgr = AuthManager(
        shared_key=SHARED_KEY,
        pairing_code_length=PAIRING_CODE_LENGTH,
        pairing_code_ttl=PAIRING_CODE_TTL,
        session_token_ttl=SESSION_TOKEN_TTL,
        session_token_absolute_ttl=SESSION_TOKEN_ABSOLUTE_TTL,
        sessions_file=os.path.join(
            os.path.dirname(SCHEDULER_JOBS_FILE), "sessions.json"
        ),
    )
    _api_auth_manager = auth_mgr
    rate_limiter = RateLimiter()
    session_mgr = SessionManager(config_file=CONFIG_FILE, app_env=APP_ENV)
    history_mgr = HistoryManager()
    bg_task_mgr = BackgroundTaskManager()
    session_mgr._bg_task_mgr = bg_task_mgr
    # Expose for testing/introspection (read-only reference)
    import sys as _sys

    _sys.modules[__name__]._session_mgr = session_mgr
    usage_tracker = RuntimeUsageTracker()

    # Shared thread pool executor for background tasks
    # Allow up to MAX_TASKS_PER_USER concurrent background tasks
    bg_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=BackgroundTaskManager.MAX_TASKS_PER_USER,
        thread_name_prefix="bg_task_",
    )

    # Notification manager for background task completion notifications
    try:
        from notification_manager import NotificationManager

        notification_mgr = NotificationManager()
    except ImportError:
        notification_mgr = None
        print(
            "[API] NotificationManager not available — notifications disabled",
            file=sys.stderr,
        )

    session_mgr._notification_mgr = notification_mgr

    _start_time = time.time()

    # ---- LLM Session Title Generation ----
    QUOTE_CHARS = chr(34) + chr(39)  # ASCII double-quote + single-quote
    import httpx as _httpx

    _TITLE_GEN_OLLAMA_URL = os.environ.get(
        "TITLE_GEN_OLLAMA_URL", "http://192.168.1.101:11434"
    )
    _TITLE_GEN_MODEL = os.environ.get("TITLE_GEN_MODEL", "granite3.3-tuned")
    _TITLE_REFRESH_INTERVAL = int(os.environ.get("TITLE_REFRESH_INTERVAL", "10"))

    def _smart_heuristic_title(messages: list) -> str:
        """Generate a reasonable title without an LLM using heuristic extraction."""
        if not messages:
            return ""
        # Find first substantive user message (skip slash commands)
        user_msg = ""
        for m in messages:
            if m.get("role") == "user":
                text = m.get("content", "").strip()
                if text and not text.startswith("/"):
                    user_msg = text
                    break
        if not user_msg:
            return ""
        # Clean up: remove markdown, code fences, URLs
        cleaned = re.sub(r"```[\s\S]*?```", "", user_msg)
        cleaned = re.sub(r"`[^`]+`", "", cleaned)
        cleaned = re.sub(r"https?://\S+", "", cleaned)
        cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"", cleaned)
        cleaned = re.sub(r"[#*_~>]", "", cleaned)
        cleaned = " ".join(cleaned.split())
        if not cleaned:
            return user_msg[:60]
        # If it's a question, keep the question form
        if "?" in cleaned:
            q_end = cleaned.index("?") + 1
            title = cleaned[:q_end]
        else:
            title = cleaned
        # Truncate intelligently at word boundary
        if len(title) > 60:
            title = title[:57]
            last_space = title.rfind(" ")
            if last_space > 30:
                title = title[:last_space]
            title += "..."
        return title.strip()

    async def _generate_title_via_llm(messages: list) -> Optional[str]:
        """Generate a concise session title using an LLM.

        Tries Ollama first (free, local), then Anthropic API.
        Returns None if all providers fail.
        """
        context_msgs = (
            messages[:6] if len(messages) <= 6 else messages[:3] + messages[-3:]
        )
        conversation = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: "
            f"{m.get('content', '')[:300]}"
            for m in context_msgs
        )

        system_prompt = (
            "Generate a concise, descriptive title (max 60 chars) for this "
            "conversation. The title should help a human quickly understand "
            "the topic. Return ONLY the title text, no quotes, no explanation."
        )

        # Try Ollama (free, local)
        try:
            async with _httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{_TITLE_GEN_OLLAMA_URL}/api/generate",
                    json={
                        "model": _TITLE_GEN_MODEL,
                        "prompt": (
                            f"{system_prompt}\n\n"
                            f"Conversation:\n{conversation}\n\nTitle:"
                        ),
                        "stream": False,
                        "options": {"temperature": 0.3, "num_predict": 30},
                    },
                )
                if resp.status_code == 200:
                    title = resp.json().get("response", "").strip().strip(QUOTE_CHARS)
                    if title and 3 <= len(title) <= 120:
                        return title
        except Exception:
            pass

        # Try Anthropic API
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        if anthropic_key:
            try:
                import anthropic as _anthropic

                client = _anthropic.AsyncAnthropic(api_key=anthropic_key)
                resp = await client.messages.create(
                    model="claude-haiku-4.5",
                    max_tokens=30,
                    system=system_prompt,
                    messages=[
                        {"role": "user", "content": f"Conversation:\n{conversation}"}
                    ],
                )
                title = resp.content[0].text.strip().strip(QUOTE_CHARS)
                if title and 3 <= len(title) <= 120:
                    return title
            except Exception:
                pass

        return None

    async def _maybe_auto_generate_title(channel: str, identity: str, session_id: str):
        """Check if a session needs title generation and do it if so."""
        try:
            info = history_mgr.get_session_for_title_check(
                channel, identity, session_id
            )
            if not info:
                return

            title_source = info["title_source"]
            msg_count = info["message_count"]
            msg_at_gen = info["message_count_at_title_gen"]

            if title_source == "user":
                return

            needs_generation = msg_count >= 2 and title_source == "auto"

            if (
                not needs_generation
                and title_source == "llm"
                and msg_count - msg_at_gen >= _TITLE_REFRESH_INTERVAL
            ):
                needs_generation = True

            if not needs_generation:
                return

            # Try LLM first, fall back to smart heuristic
            title = await _generate_title_via_llm(info["messages"])
            source = "llm"
            if not title:
                title = _smart_heuristic_title(info["messages"])
                source = "heuristic"

            if title and title != info.get("title"):
                history_mgr.update_title_llm(
                    channel, identity, session_id, title, source=source
                )
                logging.info(
                    f"[TitleGen] {source} title for {session_id[:12]}: {title}"
                )
        except Exception as exc:
            logging.warning(f"[TitleGen] Failed for {session_id[:12]}: {exc}")

    # ---- Pydantic models ----
    class ChannelEnum(str, Enum):
        telegram = "telegram"
        webex = "webex"
        webui = "webui"

    class PairingRequest(BaseModel):
        identity: str
        channel: ChannelEnum

    class PairingVerification(BaseModel):
        code: str
        identity: str

    class SessionCreate(BaseModel):
        session_id: Optional[str] = (
            None  # Optional: if provided, use this session_id instead of generating
        )
        agent: Optional[str] = None
        model: Optional[str] = None
        runtime: Optional[str] = None

    class ExecuteRequest(BaseModel):
        query: str
        timeout: Optional[int] = None
        model: Optional[str] = None
        runtime: Optional[str] = None
        agent: Optional[str] = None

        @field_validator("query")
        @classmethod
        def validate_query_length(cls, v):
            if len(v) > 10000:
                raise ValueError("Query must be 10,000 characters or less")
            return v

    class QueryRequest(BaseModel):
        """One-shot query without session management."""

        prompt: str
        runtime: Optional[str] = None
        model: Optional[str] = None
        agent: Optional[str] = None
        timeout: Optional[int] = None

        @field_validator("prompt")
        @classmethod
        def validate_prompt_length(cls, v):
            if len(v) > 10000:
                raise ValueError("Prompt must be 10,000 characters or less")
            return v

    # ---- authentication dependency ----
    async def authenticate(
        request: Request,
        authorization: Optional[str] = Header(None),
        x_user_identity: Optional[str] = Header(None),
        x_auth_channel: Optional[str] = Header(None),
    ) -> dict:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401, detail="Missing or invalid authorization header"
            )

        token = authorization[7:]

        if token.startswith("shared_"):
            if not auth_mgr.validate_shared_key(token):
                raise HTTPException(status_code=401, detail="Invalid shared key")
            # Only trust X-User-Identity from loopback (where our connectors run)
            client_ip = request.client.host if request.client else ""
            is_local = client_ip in ("127.0.0.1", "::1", "localhost")
            identity = (
                x_user_identity if (is_local and x_user_identity) else "shared_key_user"
            )
            return {
                "identity": identity,
                "channel": x_auth_channel or "api",
                "auth_type": "shared_key",
            }

        if token.startswith("session_"):
            token_data = auth_mgr.validate_session_token(token)
            if not token_data:
                raise HTTPException(
                    status_code=401, detail="Invalid or expired session token"
                )
            return {
                "identity": token_data["identity"],
                "channel": token_data["channel"],
                "auth_type": "session_token",
            }

        raise HTTPException(status_code=401, detail="Unrecognized token type")

    # ---- lifespan for background cleanup ----
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(app):
        async def _periodic_cleanup():
            while True:
                await asyncio.sleep(300)
                auth_mgr.cleanup_expired()
                rate_limiter.cleanup()
                await asyncio.to_thread(bg_task_mgr.cleanup_old)
                # Periodic reconciliation: promote queued tasks if slots available
                try:
                    _all = await asyncio.to_thread(bg_task_mgr.list_all_tasks)
                    _queued_periodic = [t for t in _all if t["status"] == "queued"]
                    _queued_periodic.sort(key=lambda t: t.get("created_at", ""))
                    for _qt in _queued_periodic:
                        _agent = _qt.get("agent", "orchestrator")
                        _ch = _qt.get("channel", "api")
                        _uid = _qt.get("user_identity", "")
                        _acfg = session_mgr.AGENTS.get(_agent, {})
                        _mc = _acfg.get(
                            "max_concurrent",
                            BackgroundTaskManager.MAX_TASKS_PER_USER,
                        )
                        _rn = await asyncio.to_thread(
                            bg_task_mgr.count_running, _ch, _uid, _agent
                        )
                        if _rn >= _mc:
                            continue
                        _nsid = str(uuid4())
                        await asyncio.to_thread(
                            bg_task_mgr.promote_queued_task, _qt["task_id"], _nsid
                        )
                        print(
                            f"[Periodic] Promoting queued task {_qt['task_id']}",
                            flush=True,
                        )
                        bg_executor.submit(
                            _run_background_task,
                            _qt["task_id"],
                            _nsid,
                            _qt["prompt"],
                            _qt["agent"],
                            _qt["runtime"],
                            _qt["model"],
                            _qt["channel"],
                            _qt["user_identity"],
                            _qt.get("timeout") or 900,
                            _qt.get("notify", True),
                        )
                except Exception as _rec_exc:
                    print(
                        f"[Periodic] Queue reconciliation error: {_rec_exc}",
                        flush=True,
                    )

        async def _agents_file_watcher():
            """Poll agents.json mtime every 10s and hot-reload on change."""
            while True:
                await asyncio.sleep(10)
                try:
                    cfg_path = getattr(session_mgr, "_agents_config_path", None)
                    if not cfg_path or not cfg_path.exists():
                        continue
                    current_mtime = cfg_path.stat().st_mtime
                    last_mtime = getattr(session_mgr, "_agents_json_mtime", 0.0)
                    if current_mtime != last_mtime:
                        ok, msg = session_mgr.reload_agents_from_disk()
                        if ok:
                            print(
                                f"[Hot-Reload] agents.json changed on disk — {msg}",
                                file=sys.stderr,
                            )
                        else:
                            # Update mtime even on failure to avoid log-spam on every poll cycle
                            session_mgr._agents_json_mtime = current_mtime
                            print(
                                f"[Hot-Reload] agents.json changed but reload failed: {msg}",
                                file=sys.stderr,
                            )
                except Exception as exc:
                    print(
                        f"[Hot-Reload] Error watching agents.json: {exc}",
                        file=sys.stderr,
                    )

        # Reconcile orphaned tasks from previous process lifetime
        _reconcile_result = await asyncio.to_thread(bg_task_mgr.reconcile_stale_tasks)
        if _reconcile_result["stale_running"] or _reconcile_result["queued_ready"]:
            print(
                f"[Startup] Task reconciliation: "
                f"{_reconcile_result['stale_running']} stale running → failed, "
                f"{_reconcile_result['queued_ready']} queued tasks ready for promotion",
                flush=True,
            )
            # Promote queued tasks that now have available slots
            _all_tasks = await asyncio.to_thread(bg_task_mgr.list_all_tasks)
            _queued = [t for t in _all_tasks if t["status"] == "queued"]
            _queued.sort(key=lambda t: t.get("created_at", ""))
            for _qt in _queued:
                _agent = _qt.get("agent", "orchestrator")
                _channel = _qt.get("channel", "api")
                _identity = _qt.get("user_identity", "")
                _agent_config = session_mgr.AGENTS.get(_agent, {})
                _max_conc = _agent_config.get(
                    "max_concurrent", BackgroundTaskManager.MAX_TASKS_PER_USER
                )
                _running_now = await asyncio.to_thread(
                    bg_task_mgr.count_running, _channel, _identity, _agent
                )
                if _running_now >= _max_conc:
                    continue
                _new_sid = str(uuid4())
                await asyncio.to_thread(
                    bg_task_mgr.promote_queued_task, _qt["task_id"], _new_sid
                )
                print(
                    f"[Startup] Promoting queued task {_qt['task_id']} → running",
                    flush=True,
                )
                bg_executor.submit(
                    _run_background_task,
                    _qt["task_id"],
                    _new_sid,
                    _qt["prompt"],
                    _qt["agent"],
                    _qt["runtime"],
                    _qt["model"],
                    _qt["channel"],
                    _qt["user_identity"],
                    _qt.get("timeout") or 900,
                    _qt.get("notify", True),
                )

        cleanup_task = asyncio.ensure_future(_periodic_cleanup())
        watcher_task = asyncio.ensure_future(_agents_file_watcher())
        yield
        cleanup_task.cancel()
        watcher_task.cancel()

    # ---- FastAPI app ----
    app = FastAPI(
        title="Wee-Orchestrator API",
        version="1.0.0",
        docs_url="/api/v1/docs" if not IS_PRODUCTION else None,
        redoc_url="/api/v1/redoc" if not IS_PRODUCTION else None,
        lifespan=_lifespan,
    )

    # Expose managers on app.state for testing
    app.state.bg_task_mgr = bg_task_mgr

    # ---- CORS middleware ----
    cors_origins = [
        o.strip()
        for o in os.environ.get("API_CORS_ORIGINS", "").split(",")
        if o.strip()
    ]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-User-Identity",
                "X-Auth-Channel",
            ],
        )

    # ---- generic exception handler ----
    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception):
        if IS_PRODUCTION:
            return JSONResponse(
                status_code=500, content={"detail": "Internal server error"}
            )
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    # ---- endpoints ----

    # ---- endpoints ----

    @app.get("/api/v1/agents")
    async def get_agents():
        """Return list of configured agents for WebUI."""
        try:
            agents = []
            for name, info in session_mgr.AGENTS.items():
                agents.append(
                    {
                        "name": name,
                        "description": info.get("description", ""),
                        "path": info.get("path", ""),
                    }
                )
            return {"agents": agents}
        except Exception as e:
            return {"agents": [], "error": str(e)}

    @app.get("/api/v1/health")
    async def health():
        # Do NOT call load_session_map() or any disk I/O here —
        # health must return immediately regardless of task store size.
        return {
            "status": "ok",
            "uptime_seconds": time.time() - _start_time,
            "version": "1.0.0",
            "environment": APP_ENV,
            "agents_loaded": len(session_mgr.AGENTS),
            "scheduler_enabled": SCHEDULER_ENABLED,
        }

    @app.get("/api/v1/config")
    async def get_config():
        """Public endpoint — returns feature flags for the WebUI."""
        return {
            "scheduler_enabled": SCHEDULER_ENABLED,
            "background_tasks_enabled": True,
            "app_env": APP_ENV,
        }

    @app.get("/api/v1/agents")
    async def get_agents():
        """Return available agents from agents.json."""
        agents = []
        for name, info in session_mgr.AGENTS.items():
            agents.append(
                {
                    "name": name,
                    "description": info.get("description", ""),
                    "path": info.get("path", ""),
                }
            )
        return {"agents": agents}

    @app.get("/api/v1/runtimes")
    async def get_runtimes():
        """Return list of available runtimes on this system.

        Only runtimes that are actually installed/available are returned.
        This prevents the WebUI from showing runtimes that cannot be used.
        """
        runtimes = get_available_runtimes()
        return {"runtimes": runtimes}

    @app.get("/api/v1/models")
    async def get_models(runtime: str = "copilot"):
        """Return available models for the specified runtime.

        Uses CLI discovery for all runtimes where possible; falls back to the
        built-in static model list when the runtime CLI is unavailable or does
        not expose a model-listing command.
        """
        runtime = runtime.lower().strip()
        known_runtimes = {
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
        }
        if runtime not in known_runtimes:
            return {
                "runtime": runtime,
                "models": [],
                "error": f"Unknown runtime: {runtime}",
            }

        try:
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None, session_mgr.get_models_for_runtime, runtime
            )
            models = []
            for _group, model_ids in raw.items():
                for model_id in model_ids:
                    label = (
                        session_mgr._get_model_description(model_id, runtime)
                        or model_id
                    )
                    entry = {"id": model_id, "label": label}
                    if _group:
                        entry["group"] = _group
                    models.append(entry)
            return {"runtime": runtime, "models": models}
        except Exception as e:
            return {"runtime": runtime, "models": [], "error": str(e)}

    @app.post("/api/v1/auth/request-pairing")
    async def request_pairing(body: PairingRequest, request: Request):
        client_ip = request.client.host if request.client else "unknown"
        if not rate_limiter.check(client_ip, "pairing", max_requests=5, window=900):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

        identity = body.identity.lstrip("@")
        if body.channel.value == "telegram":
            resolved = _resolve_telegram_identity(identity)
            if resolved is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Telegram user @{identity} not found. "
                        "Please send any message to the bot first, then try again."
                    ),
                )
            identity = resolved

        code = auth_mgr.generate_pairing_code(identity, body.channel.value)
        delivered = _send_pairing_code(body.channel.value, identity, code)
        if not delivered:
            raise HTTPException(
                status_code=503,
                detail=f"Pairing code generated but failed to deliver via {body.channel.value}. Please try again.",
            )
        return {
            "message": f"Pairing code sent via {body.channel.value}",
            "expires_in": PAIRING_CODE_TTL,
            "identity_resolved": identity,
        }

    @app.post("/api/v1/auth/verify-pairing")
    async def verify_pairing(body: PairingVerification):
        token = auth_mgr.verify_pairing_code(body.code, body.identity)
        if not token:
            raise HTTPException(
                status_code=400, detail="Invalid or expired pairing code"
            )
        token_data = auth_mgr.validate_session_token(token)
        channel = token_data["channel"] if token_data else "unknown"
        username = None
        if channel == "telegram":
            username = _get_telegram_username(body.identity)
        return {
            "token": token,
            "expires_in": SESSION_TOKEN_TTL,
            "absolute_expires_in": SESSION_TOKEN_ABSOLUTE_TTL,
            "identity": body.identity,
            "channel": channel,
            "username": username,
        }

    @app.post("/api/v1/sessions/create")
    async def create_session(
        body: SessionCreate,
        user: dict = Header(None),
        request: Request = None,
    ):
        # Manual auth – FastAPI Depends() isn't used here so we call directly
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        # Use provided session_id or generate a new one
        session_id = body.session_id if body.session_id else str(uuid4())[:8]
        session_mgr.get_or_create_session_data(session_id, identity=user["identity"])
        history_mgr.create_session(user["channel"], user["identity"], session_id)

        # Store channel in session so file instructions are channel-aware
        session_mgr.update_session_field(session_id, "channel", user["channel"])

        # WebUI sessions default to markdown so media instructions are active
        session_mgr.update_session_field(session_id, "render_type", "markdown")

        if body.agent:
            session_mgr.update_session_field(session_id, "agent", body.agent)

        # Inherit agent default permissions into session
        effective_agent = body.agent or get_default_agent()
        if effective_agent and effective_agent in session_mgr.AGENTS:
            agent_cfg = session_mgr.AGENTS[effective_agent]
            default_perms = agent_cfg.get(
                "permissions",
                {
                    "mode": "restricted",
                    "directories": {"allow_read": [], "allow_write": [], "deny": []},
                    "tools": {"allow": ["*"], "deny": []},
                    "network": {"allow_urls": ["*"], "deny_urls": []},
                    "mcp": {"allow": ["*"], "deny": []},
                },
            )
            session_mgr.update_session_field(session_id, "permissions", default_perms)
        if body.model:
            session_mgr.update_session_field(session_id, "model", body.model)
        if body.runtime:
            session_mgr.update_session_field(session_id, "runtime", body.runtime)

        session_data = session_mgr.get_or_create_session_data(session_id)
        return {
            "session_id": session_id,
            "agent": session_data.get("agent", "orchestrator"),
            "model": session_data.get("model"),
            "runtime": session_data.get("runtime"),
        }

    @app.post("/api/v1/sessions/{session_id}/execute")
    async def execute_session(session_id: str, body: ExecuteRequest, request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )

        client_ip = request.client.host if request.client else "unknown"
        if not rate_limiter.check(client_ip, "execute", max_requests=60, window=60):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

        existing = session_mgr.load_session_data(session_id)
        if not existing:
            print(
                f"[Session Recovery] Session {session_id} not in session map, "
                f"attempting recovery (user={user['identity']}, channel={user['channel']})",
                file=sys.stderr,
            )
            history_sessions = history_mgr.get_sessions(
                user["channel"], user["identity"]
            )
            session_ids_in_history = {s["session_id"] for s in history_sessions}
            if session_id not in session_ids_in_history:
                print(
                    f"[Session Recovery] Session {session_id} not in history — 404",
                    file=sys.stderr,
                )
                raise HTTPException(status_code=404, detail="Session not found")
            print(
                f"[Session Recovery] Restored session {session_id} from history",
                file=sys.stderr,
            )
            existing = session_mgr.get_or_create_session_data(
                session_id, identity=user["identity"]
            )
            session_mgr.update_session_field(session_id, "channel", user["channel"])
            session_mgr.update_session_field(session_id, "render_type", "markdown")

        # Touch session to mark activity
        session_mgr.touch_session(session_id)

        # Persist identity so mute preferences can be discovered later
        session_mgr.update_session_field(session_id, "identity", user["identity"])

        # Apply per-query overrides for model, runtime, and agent if provided
        if body.runtime:
            session_mgr.update_session_field(session_id, "runtime", body.runtime)
        if body.model:
            # Resolve model name/alias to actual model ID for current runtime
            current_rt = body.runtime or existing.get("runtime", "copilot")
            model_id = session_mgr.get_model_from_name(body.model, current_rt)
            if model_id:
                session_mgr.update_session_field(session_id, "model", model_id)
            else:
                # Fallback: use the name as-is if lookup fails
                session_mgr.update_session_field(session_id, "model", body.model)
        # NOTE: body.agent is intentionally NOT persisted to the session here.
        # Agent changes must go through /agent set (slash command) or session
        # creation — not silently via every API call.  See F015.

        session_mgr._bg_identity = user["identity"]
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(
                pool, session_mgr.execute, body.query, session_id
            )

        history_mgr.append_message(
            user["channel"], user["identity"], session_id, "user", body.query
        )
        history_mgr.append_message(
            user["channel"], user["identity"], session_id, "assistant", result
        )

        # Fire-and-forget LLM title generation
        asyncio.create_task(
            _maybe_auto_generate_title(user["channel"], user["identity"], session_id)
        )

        session_data = session_mgr.get_or_create_session_data(
            session_id, identity=user["identity"]
        )
        runtime = session_data.get("runtime", "copilot")
        return {
            "session_id": session_id,
            "response": result,
            "runtime": runtime,
            "model": session_data.get("model"),
        }

    @app.post("/api/v1/query")
    async def stateless_query(body: QueryRequest, request: Request):
        """One-shot query endpoint - no session management required.

        Creates a temporary session, executes the prompt, returns the result.
        Useful for testing, scripting, and simple integrations.
        """
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )

        client_ip = request.client.host if request.client else "unknown"
        if not rate_limiter.check(client_ip, "query", max_requests=30, window=60):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

        session_id = f"query_{str(uuid4())[:8]}"
        session_mgr.get_or_create_session_data(session_id, identity=user["identity"])
        session_mgr.update_session_field(session_id, "channel", user["channel"])
        session_mgr.update_session_field(session_id, "render_type", "text")

        effective_agent = body.agent or get_default_agent()
        session_mgr.update_session_field(session_id, "agent", effective_agent)

        if body.model:
            current_rt = body.runtime or "copilot"
            model_id = session_mgr.get_model_from_name(body.model, current_rt)
            session_mgr.update_session_field(
                session_id, "model", model_id or body.model
            )
        if body.runtime:
            session_mgr.update_session_field(session_id, "runtime", body.runtime)

        if body.timeout:
            session_mgr.update_session_field(session_id, "timeout", body.timeout)

        session_mgr._bg_identity = user["identity"]
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(
                pool, session_mgr.execute, body.prompt, session_id
            )

        session_data = session_mgr.get_or_create_session_data(
            session_id, identity=user["identity"]
        )
        runtime = session_data.get("runtime", "copilot")
        model = session_data.get("model")
        # Clean up temporary query session to avoid session_map bloat
        try:
            with session_mgr._session_map_lock:
                _smap = session_mgr.load_session_map()
                _smap.pop(session_id, None)
                session_mgr.save_session_map(_smap)
        except Exception:
            pass

        # Strip ANSI escape codes from runtime output (#68)
        _ansi_re = re.compile(
            r"\x1b\[[0-9;]*[a-zA-Z]" r"|\x1b\][^\x07]*\x07" r"|\x1b\([A-Z0-9]"
        )
        if result and isinstance(result, str):
            result = _ansi_re.sub("", result)

        # Detect empty / null responses — runtime produced no output (#68)
        if not result or (isinstance(result, str) and not result.strip()):
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "empty_response",
                    "message": "Runtime returned no output",
                    "runtime": runtime,
                    "model": model,
                },
            )

        # Detect runtime execution errors and return proper HTTP codes
        _RUNTIME_ERROR_PATTERNS = [
            ("ProviderModelNotFoundError", 422, "model_not_found"),
            ("Model not found:", 422, "model_not_found"),
            ("NotFoundError:", 422, "resource_not_found"),
            ("Resource not found", 422, "resource_not_found"),
            ("rate limit", 429, "rate_limited"),
            ("RateLimitError", 429, "rate_limited"),
            ("PermissionDeniedError", 403, "permission_denied"),
            ("AuthenticationError", 401, "auth_error"),
            ("Error: executable not found", 503, "runtime_unavailable"),
            ("ECONNREFUSED", 502, "connection_refused"),
            ("Connection refused", 502, "connection_refused"),
            ("connect ECONNREFUSED", 502, "connection_refused"),
            ("ETIMEDOUT", 504, "connection_timeout"),
            ("ECONNRESET", 502, "connection_reset"),
            ("socket hang up", 502, "connection_reset"),
        ]
        if isinstance(result, str):
            for pattern, status_code, error_code in _RUNTIME_ERROR_PATTERNS:
                if pattern.lower() in result.lower():
                    raise HTTPException(
                        status_code=status_code,
                        detail={
                            "error": error_code,
                            "message": result.strip()[:500],
                            "runtime": runtime,
                            "model": model,
                        },
                    )

        return {
            "session_id": session_id,
            "response": result,
            "runtime": runtime,
            "model": model,
        }

    @app.post("/api/v1/sessions/{session_id}/stream")
    async def stream_session(session_id: str, body: ExecuteRequest, request: Request):
        """SSE streaming endpoint — WebUI only.

        Returns a ``text/event-stream`` response.  Events:

        ``{"type":"start"}``            — emitted immediately so the browser can
                                          create the streaming bubble.
        ``{"type":"chunk","text":"…"}`` — one or more lines of raw stdout as they
                                          arrive from the AI CLI subprocess.
        ``{"type":"done","response":"…","runtime":"…","model":"…"}``
                                        — final, metadata-stripped response.
                                          The browser replaces the streaming
                                          bubble with this fully-rendered text.
        ``{"type":"error","message":"…"}`` — on failure.

        Slash-commands (``/…``) and bash commands (``!…``) produce no subprocess
        output; the ``start`` event is followed immediately by ``done``.
        """
        import json as _json

        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )

        client_ip = request.client.host if request.client else "unknown"
        if not rate_limiter.check(client_ip, "execute", max_requests=60, window=60):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

        existing = session_mgr.load_session_data(session_id)
        if not existing:
            # Session map entry was lost — likely due to cleanup daemon
            # removing the backend session while the UI was idle.
            print(
                f"[Session Recovery] Stream: session {session_id} not in map, "
                f"attempting recovery (user={user['identity']}, "
                f"channel={user['channel']})",
                file=sys.stderr,
            )
            history_sessions = history_mgr.get_sessions(
                user["channel"], user["identity"]
            )
            session_ids_in_history = {s["session_id"] for s in history_sessions}
            if session_id not in session_ids_in_history:
                print(
                    f"[Session Recovery] Stream: session {session_id} not in "
                    f"history — returning 404",
                    file=sys.stderr,
                )
                raise HTTPException(status_code=404, detail="Session not found")
            print(
                f"[Session Recovery] Stream: restored session {session_id} "
                f"from chat history — backend will be recreated",
                file=sys.stderr,
            )
            existing = session_mgr.get_or_create_session_data(
                session_id, identity=user["identity"]
            )
            session_mgr.update_session_field(session_id, "channel", user["channel"])
            session_mgr.update_session_field(session_id, "render_type", "markdown")

        # Touch session to mark activity and prevent cleanup
        session_mgr.touch_session(session_id)

        # Persist identity so mute preferences can be discovered later
        session_mgr.update_session_field(session_id, "identity", user["identity"])

        # Apply per-query overrides for model, runtime, and agent if provided
        if body.runtime:
            session_mgr.update_session_field(session_id, "runtime", body.runtime)
        if body.model:
            # Resolve model name/alias to actual model ID for current runtime
            current_rt = body.runtime or existing.get("runtime", "copilot")
            model_id = session_mgr.get_model_from_name(body.model, current_rt)
            if model_id:
                session_mgr.update_session_field(session_id, "model", model_id)
            else:
                # Fallback: use the name as-is if lookup fails
                session_mgr.update_session_field(session_id, "model", body.model)
        # NOTE: body.agent is intentionally NOT persisted to the session here.
        # Agent changes must go through /agent set (slash command) or session
        # creation — not silently via every API call.  See F015.

        session_mgr._bg_identity = user["identity"]
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        # Slash / bash commands don't spawn a subprocess, so no chunks will
        # ever arrive on the queue.  Detect them early so we can skip the
        # queue-draining loop and just await the future directly.
        is_command = body.query.lstrip().startswith(("/", "!"))

        async def generate():
            # Register the queue so _execute_subprocess_with_tracking can push chunks
            if not is_command:
                session_mgr._register_stream(session_id, queue, loop)

            # Tell the browser to create its streaming bubble right away
            yield f"data: {_json.dumps({'type': 'start'})}\n\n"

            # Track whether we successfully delivered the done event to the
            # client.  If we didn't (client disconnected), keep the buffer
            # alive so a reconnecting client can replay it.
            done_delivered = False

            # IMPORTANT: Do NOT use `with ThreadPoolExecutor(...) as pool:` here.
            # Its __exit__ calls shutdown(wait=True) which blocks the asyncio
            # event loop when the client disconnects (e.g. /cancel aborts the
            # SSE stream).  That freeze prevents ALL requests (including the
            # cancel endpoint) from being processed until execute() finishes.
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = loop.run_in_executor(
                pool, session_mgr.execute, body.query, session_id
            )

            try:
                if is_command:
                    # No subprocess → just wait for the result, no chunks to stream
                    try:
                        result = await future
                    except Exception as exc:
                        result = f"Error: {exc}"
                else:
                    # Drain chunks from the queue until the subprocess sends the
                    # 'done' sentinel, then await the (already-complete) future
                    # for the final stripped response.
                    try:
                        while True:
                            try:
                                kind, data = await asyncio.wait_for(
                                    queue.get(), timeout=1.0
                                )
                            except asyncio.TimeoutError:
                                # Send a keepalive comment every second so the
                                # connection is not torn down by proxies/browsers
                                yield ": keepalive\n\n"
                                # If execute() finished early (e.g. an error before
                                # the subprocess started) break out gracefully
                                if future.done():
                                    break
                                continue

                            if kind == "chunk":
                                # data is now a dict with text, ends_sentence, ends_paragraph
                                if isinstance(data, dict):
                                    yield f"data: {_json.dumps({'type': 'chunk', **data})}\n\n"
                                else:
                                    # Fallback for non-Claude runtimes
                                    yield f"data: {_json.dumps({'type': 'chunk', 'text': data})}\n\n"
                            elif kind == "tool_call":
                                # F026: skip tool_call SSE events in silent mode
                                _sd = session_mgr.load_session_data(session_id)
                                if not (_sd and _sd.get("silent_mode")):
                                    yield f"data: {_json.dumps({'type': 'tool_call', **_sanitize_tool_call_for_display(data)})}\n\n"
                            elif kind == "done":
                                break  # subprocess finished; final result in future
                    except Exception as exc:
                        yield f"data: {_json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
                        return

                    try:
                        result = await future
                    except Exception as exc:
                        result = f"Error: {exc}"

                history_mgr.append_message(
                    user["channel"], user["identity"], session_id, "user", body.query
                )
                history_mgr.append_message(
                    user["channel"], user["identity"], session_id, "assistant", result
                )

                # Fire-and-forget LLM title generation
                asyncio.create_task(
                    _maybe_auto_generate_title(
                        user["channel"], user["identity"], session_id
                    )
                )

                session_data = session_mgr.get_or_create_session_data(session_id)
                runtime = session_data.get("runtime", "copilot")
                _done_evt = {
                    "type": "done",
                    "response": result,
                    "runtime": runtime,
                    "model": session_data.get("model"),
                }
                # Issue #160: Include wee_meta (token usage + cost) if available
                _wm = session_data.pop("_wee_meta", None)
                if _wm:
                    _done_evt["wee_meta"] = _wm
                done_payload = _json.dumps(_done_evt)
                yield f"data: {done_payload}\n\n"
                done_delivered = True
            finally:
                if not is_command:
                    session_mgr._unregister_stream(session_id, queue=queue)
                    # Only clean up the buffer if we successfully delivered
                    # the done event to the client.  If the client disconnected
                    # mid-stream, keep the buffer so they can reconnect and
                    # replay the missed output.
                    if done_delivered:
                        session_mgr._cleanup_stream_buffer(session_id)
                pool.shutdown(wait=False)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.get("/api/v1/sessions/{session_id}/stream/reconnect")
    async def reconnect_stream(session_id: str, request: Request):
        """Reconnect to an active or recently-finished stream for *session_id*.

        Replays all buffered chunks, then continues streaming live output
        until the query completes.  Returns 200 JSON ``{"active": false}``
        when there is nothing to reconnect to.

        This enables the WebUI to switch between session tabs without losing
        stream output — when the user switches back, the frontend calls this
        endpoint to catch up on missed chunks and resume live streaming.
        """
        import json as _json

        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )

        # Opportunistically clean up stale buffers
        session_mgr._cleanup_stale_stream_buffers()

        buf = session_mgr._stream_buffers.get(session_id)
        query_info = session_mgr.get_running_query(session_id)

        # Nothing to reconnect to
        if not buf and not query_info:
            return {"active": False, "message": "No active stream for this session"}

        # If there's a query running but no buffer (e.g. non-streaming command),
        # just report it as active so the UI can show a spinner
        if not buf:
            return {
                "active": True,
                "streaming": False,
                "message": "Query running (non-streaming)",
            }

        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        async def generate():
            # Register as consumer FIRST so we don't miss chunks
            replay_index = buf.add_consumer(queue, loop)
            # Also register in _stream_queues so new connections are tracked
            session_mgr._stream_queues[session_id] = (queue, loop)

            try:
                yield f"data: {_json.dumps({'type': 'reconnect', 'buffered_chunks': replay_index})}\n\n"

                # Replay buffered chunks up to the registration point
                replay_chunks = buf.get_replay_chunks(replay_index)
                for kind, data in replay_chunks:
                    if kind == "chunk":
                        if isinstance(data, dict):
                            yield f"data: {_json.dumps({'type': 'chunk', **data})}\n\n"
                        else:
                            yield f"data: {_json.dumps({'type': 'chunk', 'text': data})}\n\n"
                    elif kind == "tool_call":
                        # F026: skip tool_call SSE events in silent mode
                        _sd = session_mgr.load_session_data(session_id)
                        if not (_sd and _sd.get("silent_mode")):
                            yield f"data: {_json.dumps({'type': 'tool_call', **_sanitize_tool_call_for_display(data)})}\n\n"
                    elif kind == "done":
                        # Query already finished — send done event with stored result
                        session_data = session_mgr.get_or_create_session_data(
                            session_id
                        )
                        runtime = session_data.get("runtime", "copilot")
                        _done_evt = {
                            "type": "done",
                            "response": (data if isinstance(data, str) else str(data)),
                            "runtime": runtime,
                            "model": session_data.get("model"),
                        }
                        # Issue #160: Include wee_meta if available
                        _wm = session_data.pop("_wee_meta", None)
                        if _wm:
                            _done_evt["wee_meta"] = _wm
                        done_payload = _json.dumps(_done_evt)
                        yield f"data: {done_payload}\n\n"
                        return

                # If buffer is already finished after replay, send done
                if buf.finished:
                    session_data = session_mgr.get_or_create_session_data(session_id)
                    runtime = session_data.get("runtime", "copilot")
                    result = buf.done_result if isinstance(buf.done_result, str) else ""
                    _done_evt = {
                        "type": "done",
                        "response": result,
                        "runtime": runtime,
                        "model": session_data.get("model"),
                    }
                    # Issue #160: Include wee_meta if available
                    _wm = session_data.pop("_wee_meta", None)
                    if _wm:
                        _done_evt["wee_meta"] = _wm
                    done_payload = _json.dumps(_done_evt)
                    yield f"data: {done_payload}\n\n"
                    return

                # Drain live chunks from the queue
                while True:
                    try:
                        kind, data = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        if buf.finished:
                            break
                        continue

                    if kind == "chunk":
                        if isinstance(data, dict):
                            yield f"data: {_json.dumps({'type': 'chunk', **data})}\n\n"
                        else:
                            yield f"data: {_json.dumps({'type': 'chunk', 'text': data})}\n\n"
                    elif kind == "tool_call":
                        # F026: skip tool_call SSE events in silent mode
                        _sd = session_mgr.load_session_data(session_id)
                        if not (_sd and _sd.get("silent_mode")):
                            yield f"data: {_json.dumps({'type': 'tool_call', **_sanitize_tool_call_for_display(data)})}\n\n"
                    elif kind == "done":
                        break

                # Send final done event
                session_data = session_mgr.get_or_create_session_data(session_id)
                runtime = session_data.get("runtime", "copilot")
                result = buf.done_result if isinstance(buf.done_result, str) else ""
                _done_evt = {
                    "type": "done",
                    "response": result,
                    "runtime": runtime,
                    "model": session_data.get("model"),
                }
                # Issue #160: Include wee_meta if available
                _wm = session_data.pop("_wee_meta", None)
                if _wm:
                    _done_evt["wee_meta"] = _wm
                done_payload = _json.dumps(_done_evt)
                yield f"data: {done_payload}\n\n"
            finally:
                buf.remove_consumer(queue)
                session_mgr._stream_queues.pop(session_id, None)
                # Clean up buffer only if done AND no other consumers remain
                if buf.finished and not buf.has_consumers():
                    session_mgr._cleanup_stream_buffer(session_id)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.get("/api/v1/sessions/{session_id}/status")
    async def session_status(session_id: str, request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )

        data = session_mgr.load_session_data(session_id)
        if not data:
            # Auto-recreate session_map entry for sessions that exist in history
            # but whose session_map entry was lost (restart, map mismatch, etc.)
            history_sessions = history_mgr.get_sessions(
                user["channel"], user["identity"]
            )
            session_ids_in_history = {s["session_id"] for s in history_sessions}
            if session_id not in session_ids_in_history:
                raise HTTPException(status_code=404, detail="Session not found")
            data = session_mgr.get_or_create_session_data(
                session_id, identity=user["identity"]
            )
            session_mgr.update_session_field(session_id, "channel", user["channel"])
            session_mgr.update_session_field(session_id, "render_type", "markdown")

        result = {
            "session_id": session_id,
            "agent": data.get("agent"),
            "runtime": data.get("runtime"),
            "model": data.get("model"),
            "yolo_mode": data.get("yolo_mode", "restricted"),
            "permissions": data.get("permissions"),
            "silent_mode": data.get("silent_mode", False),  # F027
        }

        # Include running query info so the frontend can reconnect streams
        query_info = session_mgr.get_running_query(session_id)
        has_buffer = session_id in session_mgr._stream_buffers
        if query_info:
            result["running_query"] = True
            result["has_stream_buffer"] = has_buffer
        else:
            result["running_query"] = False
            result["has_stream_buffer"] = has_buffer

        return result

    @app.get("/api/v1/sessions/{session_id}/live-status")
    async def get_live_status(session_id: str, request: Request):
        """Return the latest live status update for a running session (F004).

        Mobile channel connectors poll this endpoint to replace static
        "Still working on it..." messages with real LLM progress updates.
        Requires only bearer token auth (no user identity needed).
        """
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing auth")
        token = auth_header[7:]
        # Accept shared key (used by connectors) or session token
        if token.startswith("shared_"):
            if not auth_mgr.validate_shared_key(token):
                raise HTTPException(status_code=401, detail="Invalid auth")
        elif not auth_mgr.validate_token(token):
            raise HTTPException(status_code=401, detail="Invalid auth")

        status = session_mgr.get_live_status(session_id)
        if status:
            return {
                "status": status["text"],
                "updated_at": status["updated_at"],
            }
        return {"status": None, "updated_at": None}

    @app.post("/api/v1/sessions/{session_id}/cancel")
    async def cancel_session(session_id: str, request: Request):
        """Cancel a running query for a session.

        This is a dedicated endpoint that bypasses the execute pipeline so it
        can be called even while a streaming response is in-flight.
        """
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )

        query_info = session_mgr.get_running_query(session_id)
        if not query_info:
            return {"cancelled": False, "message": "No running query for this session"}

        pid = query_info["pid"]

        if not session_mgr.is_process_running(pid):
            session_mgr.clear_running_query(session_id)
            return {"cancelled": False, "message": "Query has already completed"}

        runtime = query_info.get("runtime", "unknown")
        if session_mgr.kill_process(pid):
            session_mgr.clear_running_query(session_id)
            session_mgr._cleanup_stream_buffer(session_id)
            return {
                "cancelled": True,
                "message": f"Cancelled running query (PID: {pid}, Runtime: {runtime})",
            }
        else:
            return {
                "cancelled": False,
                "message": f"Failed to cancel query (PID: {pid})",
            }

    # --- Runtime usage endpoint ---

    @app.get("/api/v1/runtime-usage")
    async def get_runtime_usage(request: Request):
        """Return Copilot premium request usage from GitHub billing."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, usage_tracker.get_usage)

    # --- History endpoints ---

    @app.get("/api/v1/history/sessions")
    async def list_history_sessions(request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        return {"sessions": history_mgr.get_sessions(user["channel"], user["identity"])}

    @app.get("/api/v1/history/sessions/{session_id}/messages")
    async def get_history_messages(
        session_id: str,
        request: Request,
        limit: int = Query(100, ge=1, le=1000),
        offset: Optional[int] = Query(None, ge=0),
    ):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        messages = history_mgr.get_session_messages(
            user["channel"], user["identity"], session_id
        )
        if messages is None:
            raise HTTPException(status_code=404, detail="Session not found in history")
        total = len(messages)
        # Default offset: from the end (most recent messages)
        if offset is None:
            offset = max(total - limit, 0)
        page = messages[offset : offset + limit]
        return {
            "session_id": session_id,
            "messages": page,
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    @app.delete("/api/v1/history/sessions/{session_id}")
    async def delete_history_session(session_id: str, request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        if not history_mgr.delete_session(
            user["channel"], user["identity"], session_id
        ):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"deleted": True, "session_id": session_id}

    @app.patch("/api/v1/history/sessions/{session_id}")
    async def rename_history_session(session_id: str, request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        body = await request.json()
        title = body.get("title", "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title is required")
        if not history_mgr.rename_session(
            user["channel"], user["identity"], session_id, title
        ):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"renamed": True, "session_id": session_id, "title": title[:120]}

    @app.post("/api/v1/history/sessions/{session_id}/generate-title")
    async def generate_session_title(session_id: str, request: Request):
        """Force (re)generate an LLM title for a session."""
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        info = history_mgr.get_session_for_title_check(
            user["channel"], user["identity"], session_id
        )
        if not info:
            raise HTTPException(status_code=404, detail="Session not found")

        if not info["messages"]:
            raise HTTPException(status_code=400, detail="Session has no messages")

        # Try LLM first, then heuristic
        title = await _generate_title_via_llm(info["messages"])
        source = "llm"
        if not title:
            title = _smart_heuristic_title(info["messages"])
            source = "heuristic"

        if title:
            history_mgr.update_title_llm(
                user["channel"], user["identity"], session_id, title, source=source
            )
            return {
                "session_id": session_id,
                "title": title,
                "source": source,
            }

        raise HTTPException(status_code=500, detail="Could not generate title")

    # --- File upload ---

    @app.post("/api/v1/sessions/{session_id}/upload")
    async def upload_file(
        session_id: str, request: Request, file: UploadFile = File(...)
    ):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        if not session_mgr.load_session_data(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        contents = await file.read()
        if len(contents) > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="File exceeds 50MB limit")
        safe_name = re.sub(r"[^\w.\-]", "_", Path(file.filename).name)[:200]
        upload_dir = Path(f"/tmp/webui_uploads/{session_id}")
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / safe_name
        dest.write_bytes(contents)
        mime, _ = mimetypes.guess_type(safe_name)
        return {
            "file_path": str(dest),
            "filename": safe_name,
            "size": len(contents),
            "mime_type": mime or "application/octet-stream",
        }

    # --- Serve uploaded files (authenticated) ---

    @app.get("/api/v1/uploads/{session_id}/{filename}")
    async def serve_upload(session_id: str, filename: str, request: Request):
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        safe_name = re.sub(r"[^\w.\-]", "_", Path(filename).name)
        path = Path(f"/tmp/webui_uploads/{session_id}/{safe_name}")
        if not path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(str(path))

    # --- File Viewer ---

    # Allowed base directories for the file viewer (security)
    _FILE_VIEWER_ALLOWED_BASES = [
        "/opt/",
        "/tmp/",
        "/home/",
    ]
    _FILE_VIEWER_MAX_SIZE = 5 * 1024 * 1024  # 5MB text limit
    _FILE_VIEWER_BINARY_EXTS = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".ico",
        ".bmp",
        ".pdf",
    }
    _FILE_VIEWER_TEXT_EXTS = {
        ".md",
        ".txt",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".cfg",
        ".ini",
        ".conf",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".less",
        ".xml",
        ".sql",
        ".graphql",
        ".gql",
        ".rs",
        ".go",
        ".java",
        ".kt",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".rb",
        ".php",
        ".pl",
        ".lua",
        ".r",
        ".swift",
        ".scala",
        ".env",
        ".env.example",
        ".gitignore",
        ".dockerignore",
        ".csv",
        ".log",
        ".diff",
        ".patch",
        "",  # extensionless files (Makefile, Dockerfile, etc.)
    }

    @app.get("/api/v1/files/view")
    async def view_file(path: str, request: Request):
        """Read a file from the server for the file viewer panel."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        # Resolve and validate path
        try:
            resolved = Path(path).resolve()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid path")

        resolved_str = str(resolved)
        if not any(resolved_str.startswith(b) for b in _FILE_VIEWER_ALLOWED_BASES):
            raise HTTPException(
                status_code=403, detail="Path not in allowed directories"
            )
        if not resolved.exists():
            raise HTTPException(status_code=404, detail="File not found")
        if not resolved.is_file():
            raise HTTPException(status_code=400, detail="Path is a directory")

        ext = resolved.suffix.lower()
        file_size = resolved.stat().st_size
        mime, _ = mimetypes.guess_type(str(resolved))

        # Binary files (images, PDFs) → serve as FileResponse
        if ext in _FILE_VIEWER_BINARY_EXTS:
            return FileResponse(
                str(resolved),
                media_type=mime or "application/octet-stream",
                headers={"X-File-Name": resolved.name, "X-File-Size": str(file_size)},
            )

        # Text files → return JSON with content
        if file_size > _FILE_VIEWER_MAX_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large ({file_size} bytes, max {_FILE_VIEWER_MAX_SIZE})",
            )

        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Cannot read file: {e}")

        # Determine language hint for syntax highlighting
        lang_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".jsx": "javascript",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".sh": "bash",
            ".bash": "bash",
            ".html": "html",
            ".htm": "html",
            ".css": "css",
            ".sql": "sql",
            ".md": "markdown",
            ".xml": "xml",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
            ".rb": "ruby",
            ".php": "php",
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".hpp": "cpp",
        }
        lang = lang_map.get(ext, "plaintext")
        # Detect extensionless files
        if ext == "":
            name_lower = resolved.name.lower()
            if "makefile" in name_lower:
                lang = "makefile"
            elif "dockerfile" in name_lower:
                lang = "dockerfile"

        return {
            "path": str(resolved),
            "name": resolved.name,
            "size": file_size,
            "mime": mime or "text/plain",
            "language": lang,
            "content": content,
            "type": "text",
        }

    @app.get("/api/v1/files/view/raw")
    async def view_file_raw(path: str, request: Request):
        """Serve a file as raw binary (for images, PDFs)."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        try:
            resolved = Path(path).resolve()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid path")

        resolved_str = str(resolved)
        if not any(resolved_str.startswith(b) for b in _FILE_VIEWER_ALLOWED_BASES):
            raise HTTPException(
                status_code=403, detail="Path not in allowed directories"
            )
        if not resolved.exists():
            raise HTTPException(status_code=404, detail="File not found")
        if not resolved.is_file():
            raise HTTPException(status_code=400, detail="Path is a directory")

        mime, _ = mimetypes.guess_type(str(resolved))
        return FileResponse(
            str(resolved), media_type=mime or "application/octet-stream"
        )

    # --- Image search ---

    @app.get("/api/v1/search/images")
    async def search_images(q: str, request: Request, max_results: int = 4):
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        if not q.strip():
            raise HTTPException(status_code=400, detail="Query required")
        max_r = min(max(1, max_results), 8)
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results = await loop.run_in_executor(
                pool, _ddg_image_search, q.strip(), max_r
            )
        return {"query": q, "results": results}

    # --- Audio Transcription ---

    @app.post("/api/v1/sessions/{session_id}/transcribe")
    async def transcribe_audio(
        session_id: str, request: Request, file: UploadFile = File(...)
    ):
        """Upload an audio file and get text transcription back."""
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        if not session_mgr.load_session_data(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        contents = await file.read()
        if len(contents) > 25 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Audio file exceeds 25MB limit")
        if len(contents) == 0:
            raise HTTPException(status_code=400, detail="Empty audio file")

        # Save to temp file
        safe_name = re.sub(r"[^\w.\-]", "_", Path(file.filename).name)[:200]
        upload_dir = Path(f"/tmp/webui_uploads/{session_id}")
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / safe_name
        dest.write_bytes(contents)

        try:
            import audio_transcriber

            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as pool:
                text, backend = await loop.run_in_executor(
                    pool, audio_transcriber.transcribe, str(dest)
                )
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Transcription failed: {str(e)}"
            )

        if not text:
            raise HTTPException(status_code=422, detail="Could not transcribe audio")

        return {
            "text": text,
            "backend": backend,
            "filename": safe_name,
            "size": len(contents),
        }

    @app.get("/api/v1/transcription/status")
    async def transcription_status(request: Request):
        """Check transcription backend availability."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        import audio_transcriber

        return audio_transcriber.get_status()

    # --- Scratch Notes (Session-based) ---

    @app.get("/api/v1/sessions/{session_id}/scratch")
    async def get_scratch_notes(session_id: str, request: Request):
        """Retrieve scratch notes for a session."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        session_map = session_mgr.load_session_map()
        session_data = session_map.get(session_id)
        if not session_data:
            raise HTTPException(status_code=404, detail="Session not found")

        scratch = session_data.get("scratch", "")
        return {"scratch": scratch, "session_id": session_id}

    class ScratchNotesRequest(BaseModel):
        scratch: str

        @field_validator("scratch")
        @classmethod
        def validate_scratch(cls, v):
            if len(v) > 1000:
                raise ValueError("Scratch notes must be 1000 characters or less")
            return v

    @app.post("/api/v1/sessions/{session_id}/scratch")
    async def save_scratch_notes(
        session_id: str, request: Request, body: ScratchNotesRequest
    ):
        """Save scratch notes for a session."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        session_map = session_mgr.load_session_map()
        session_data = session_map.get(session_id)
        if not session_data:
            raise HTTPException(status_code=404, detail="Session not found")

        session_data["scratch"] = body.scratch
        session_map[session_id] = session_data
        session_mgr.save_session_map(session_map)

        return {"success": True, "scratch": body.scratch, "session_id": session_id}

    # --- Background Tasks ---

    class BackgroundTaskRequest(BaseModel):
        prompt: str
        agent: Optional[str] = None
        runtime: Optional[str] = None
        model: Optional[str] = None
        timeout: Optional[int] = None
        notify: Optional[bool] = None
        permission_mode: Optional[str] = (
            None  # elevated, restricted (default), sandboxed
        )
        description: Optional[str] = (
            None  # human-readable task name shown in Agents panel
        )
        origin_session_id: Optional[str] = None  # chat session that initiated this task

        @field_validator("prompt")
        @classmethod
        def validate_prompt(cls, v):
            if len(v) > 10000:
                raise ValueError("Prompt must be 10,000 characters or less")
            return v

    def _emit_bg_notification(
        task_id: str,
        prompt: str,
        status: str,
        channel: str,
        user_identity: str,
        output_preview=None,
        error=None,
        notify: bool = True,
    ):
        """Emit a background task completion notification via notification_mgr."""
        if notification_mgr is None:
            return
        try:
            # Re-check per-identity AND global mute preference at emit time
            # (user may have muted after the task was created, or muted from
            # a different channel whose identity doesn't match).
            if notify:
                if notification_mgr.is_muted(
                    user_identity
                ) or notification_mgr.is_muted("_global"):
                    notify = False

            user_key = bg_task_mgr._user_key(channel, user_identity)
            notification_mgr.create_notification(
                task_id=task_id,
                description=prompt[:200],
                status=status,
                channel=channel,
                user_key=user_key,
                output_preview=output_preview,
                error=error,
                skip_external=not notify,
            )
            # Push in-thread event to originating session
            task = bg_task_mgr.get_task(task_id)
            origin_sid = task.get("origin_session_id") if task else None
            if origin_sid:
                bg_task_mgr.push_bg_event(
                    origin_sid,
                    {
                        "task_id": task_id,
                        "summary": prompt[:80],
                        "status": status,
                        "agent": (task.get("agent", "") if task else ""),
                        "timestamp": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(),
                        ),
                    },
                )
        except Exception as exc:
            print(
                f"[API] Notification emit failed for {task_id}: {exc}", file=sys.stderr
            )

    def _run_command_task(
        task_id: str,
        command: str,
        working_dir: str,
        timeout: int,
        job_id: str,
        job_name: str,
        notify: bool = False,
    ):
        """Blocking function that runs a command-mode scheduled task directly via
        subprocess.  Called from a thread pool executor — no LLM involved.

        Mirrors the logic of scheduler/executor.py _execute_command_mode() but
        integrates with the background-task manager so the result appears in
        the Tasks panel with stdout/stderr output.
        """
        import subprocess as _sp

        logger.info(
            f"[Command Mode] Run Now executing job {job_id}: cmd={command[:80]}..."
        )

        try:
            argv = _split_command_args(command)
            result = _sp.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=working_dir,
            )

            if result.returncode == 0:
                output = result.stdout.strip() or "(no output)"
                bg_task_mgr.complete_task(task_id, output)
                logger.info(
                    f"[Command Mode] Run Now job {job_id} completed successfully"
                )
            else:
                error_msg = (
                    result.stderr
                    or result.stdout
                    or f"Command failed with exit code {result.returncode}"
                )
                bg_task_mgr.fail_task(task_id, error_msg)
                logger.error(
                    f"[Command Mode] Run Now job {job_id} failed: exit code {result.returncode}"
                )
        except ValueError as e:
            bg_task_mgr.fail_task(task_id, str(e))
            logger.error(f"[Command Mode] Run Now job {job_id} invalid command: {e}")
        except _sp.TimeoutExpired:
            bg_task_mgr.fail_task(task_id, f"Command timed out after {timeout}s")
            logger.error(
                f"[Command Mode] Run Now job {job_id} timed out after {timeout}s"
            )
        except Exception as e:
            bg_task_mgr.fail_task(task_id, str(e))
            logger.error(f"[Command Mode] Run Now job {job_id} exception: {e}")

        # Save result to scheduler logs/results for consistency
        try:
            sched = _get_scheduler()
            task_rec = bg_task_mgr.get_task(task_id)
            success = task_rec and task_rec.get("status") == "completed"
            sched._save_result(
                job_id,
                job_name,
                success=success,
                output=(task_rec or {}).get("final_response", ""),
                error=(task_rec or {}).get("error", ""),
            )
            status_label = "succeeded" if success else "failed"
            sched._log_job(job_id, f"Run Now (command mode) {status_label}")
        except Exception as exc:
            logger.warning(
                f"[Command Mode] Could not save scheduler result for {job_id}: {exc}"
            )

    def _run_background_task(
        task_id: str,
        session_id: str,
        prompt: str,
        agent: str,
        runtime: str,
        model: str,
        channel: str,
        user_identity: str,
        timeout: int = None,
        notify: bool = True,
        permission_mode: str = "restricted",
    ):
        """Blocking function that runs a background task in a subprocess.
        Called from a thread pool executor.

        Uses Popen for incremental output capture and real-time tool call
        parsing.  Output lines are appended to the task as they arrive so
        the WebUI can poll for live progress.

        NOTE: The entire function body is wrapped in a single try/except to
        guarantee that any unexpected exception (e.g. file-lock race on the
        session-map JSON) always transitions the task to 'failed' instead of
        leaving it stuck in 'running' forever.
        """
        import json as _json
        import re as _re
        import subprocess

        _tool_call_counter = 0

        def _parse_tool_call_from_line(line_text, rt):
            """Detect tool call patterns from a single output line."""
            nonlocal _tool_call_counter
            stripped = line_text.strip()
            if not stripped:
                return None

            tc = None
            if rt == "copilot":
                # Copilot shows tool calls as bullet points: "\u25cf <description>"
                m_tool = _re.match(
                    r"^[\u25cf\u2b24]\s+(.+?)(?:\s+\(\+\d+\))?$", stripped
                )
                if m_tool:
                    _desc = m_tool.group(1).strip()
                    if any(kw in _desc.lower() for kw in ["read", "view", "open"]):
                        _tn = "read"
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
                        _tn = "write"
                    elif any(kw in _desc.lower() for kw in ["delete", "remove", "rm"]):
                        _tn = "shell"
                    elif any(
                        kw in _desc.lower()
                        for kw in ["list", "ls", "find", "search", "glob"]
                    ):
                        _tn = "glob"
                    elif any(
                        kw in _desc.lower()
                        for kw in ["run", "exec", "install", "deploy", "build", "test"]
                    ):
                        _tn = "shell"
                    elif any(
                        kw in _desc.lower()
                        for kw in ["fetch", "curl", "http", "api", "download"]
                    ):
                        _tn = "web_fetch"
                    else:
                        _tn = "tool"
                    tc = {"name": _tn, "input": _desc}
                else:
                    # Shell command: "  $ <command>"
                    m_cmd = _re.match(r"^\$\s+(.+)", stripped)
                    if m_cmd:
                        tc = {"name": "shell", "input": m_cmd.group(1).strip()}
                    else:
                        # Legacy fallback
                        m = _re.match(
                            r"^(?:Running|Calling|Using|Ran)\s+(\w[\w.]*)\s*(.*)",
                            stripped,
                        )
                        if m:
                            tc = {"name": m.group(1), "input": m.group(2).strip()}
            elif rt == "opencode":
                # OpenCode tool invocation lines: "| ToolName args..."
                # Support the full set of known OpenCode tools
                m = _re.match(r"^\|\s+(\w+)\b(.*)", stripped)
                if m:
                    _oc_tool = m.group(1)
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
                    if _oc_tool in _oc_known or _oc_tool[0].isupper():
                        tc = {"name": _oc_tool, "input": m.group(2).strip()}
                # Also detect "Running: <command>" or "Executing: <cmd>"
                if not tc:
                    m2 = _re.match(r"^(?:Running|Executing|>)\s+(.+)", stripped)
                    if m2:
                        tc = {"name": "shell", "input": m2.group(1).strip()}
            elif rt == "codex":
                # Codex exec shows tool calls in several formats:
                # 1. "Calling function: name ..." or "Tool: name ..."
                m = _re.match(
                    r"^(?:Calling function|Tool|Executing|Running):\s*(\w[\w.]*)\s*(.*)",
                    stripped,
                    _re.IGNORECASE,
                )
                if m:
                    tc = {"name": m.group(1), "input": m.group(2).strip()}
                # 2. Shell command execution: lines starting with "$ command" or "> command"
                if not tc:
                    m2 = _re.match(r"^[$>]\s+(.+)", stripped)
                    if m2:
                        tc = {"name": "shell", "input": m2.group(1).strip()}
                # 3. "read_file(path=...)" or "write_file(path=...)" function-call syntax
                if not tc:
                    m3 = _re.match(r"^(\w+)\((.+)\)\s*$", stripped)
                    if m3 and any(
                        kw in m3.group(1).lower()
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
                        tc = {"name": m3.group(1), "input": m3.group(2).strip()}
            elif rt == "gemini":
                # Gemini CLI tool call patterns (with --yolo, tools auto-execute):
                # 1. "✦ Calling tool_name(args)" or "Calling tool_name(args)"
                m = _re.match(
                    r"^[✦*]?\s*(?:Calling|Using tool|Function call|Running)\s+(\w[\w.]*)\s*(.*)",
                    stripped,
                    _re.IGNORECASE,
                )
                if m:
                    tc = {"name": m.group(1), "input": m.group(2).strip()}
                # 2. "⚡ <tool_name>(<args>)" or "tool_name(<args>)"
                if not tc:
                    m2 = _re.match(r"^[⚡✦*]?\s*(\w+)\((.+)\)\s*$", stripped)
                    if m2 and any(
                        kw in m2.group(1).lower()
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
                        tc = {"name": m2.group(1), "input": m2.group(2).strip()}
                # 3. Shell-like execution: "$ command" or "> command"
                if not tc:
                    m3 = _re.match(r"^[$>]\s+(.+)", stripped)
                    if m3:
                        tc = {"name": "shell", "input": m3.group(1).strip()}
                # 4. "Running command: <cmd>" pattern
                if not tc:
                    m4 = _re.match(
                        r"^Running\s+command:\s*(.+)", stripped, _re.IGNORECASE
                    )
                    if m4:
                        tc = {"name": "shell", "input": m4.group(1).strip()}

            elif rt == "claude":
                # Claude background tasks now use claude binary with stream-json.
                # Try to parse JSON tool_use events first.
                try:
                    _obj = _json.loads(stripped)
                    _evt_type = _obj.get("type")
                    if _evt_type == "stream_event":
                        _event = _obj.get("event") or {}
                        _inner = _event.get("type", "")
                        if _inner == "content_block_start":
                            _cb = _event.get("content_block") or {}
                            if _cb.get("type") == "tool_use":
                                tc = {
                                    "name": _cb.get("name", "tool"),
                                    "input": _json.dumps(_cb.get("input", {})),
                                }
                        elif _inner == "content_block_stop":
                            pass  # tool result will follow
                except (ValueError, KeyError, TypeError):
                    pass
                # Plain-text fallback for non-JSON output
                if not tc:
                    m = _re.match(
                        r"^(?:Tool|Calling|Using tool):\s*(\w[\w.]*)\s*(.*)",
                        stripped,
                        _re.IGNORECASE,
                    )
                    if m:
                        tc = {"name": m.group(1), "input": m.group(2).strip()}

            if tc:
                _tool_call_counter += 1
                return {
                    "id": f"bg_{task_id[:8]}_{_tool_call_counter}",
                    "name": tc["name"],
                    "input": tc.get("input", ""),
                    "status": "detected",
                    "runtime": rt,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            return None

        try:
            # Build full context prompt with agent/runtime/channel metadata
            # Pass user_identity explicitly so the system prompt curl command
            # gets the correct X-User-Identity (avoids telegram_unknown race).
            context_prompt = session_mgr.build_agent_context_prompt(
                agent,
                prompt,
                session_id,
                render_type="text",
                timeout=timeout,
                runtime=runtime,
                model=model,
                channel=channel,
                bg_identity=user_identity,
            )
            # Memory injection moved to build_agent_context_prompt (Issue #72)

            # -- Inject steering check instruction ----------------------------
            steering_path = bg_task_mgr.get_steering_path(task_id)
            context_prompt += (
                f"\n\n[STEERING] You are background task `{task_id}`. Periodically "
                f"(every 3-5 tool calls), check the file `{steering_path}` for new "
                f"instructions from the user. If the file exists and has content, read "
                f"it, incorporate the guidance into your current work, then continue. "
                f"New instructions are appended with timestamps -- only act on ones you "
                f"have not seen yet. This is how the user steers your work in real time."
            )

            # ── Build runtime-specific command ──────────────────────────
            # Each runtime CLI has its own binary and argument format.
            # Previously all background tasks used copilot; now we dispatch
            # to the correct binary so the chosen runtime actually executes.
            from shutil import which as _which_bin

            # Set agent working directory for all runtimes
            agent_dir = session_mgr.AGENTS.get(
                agent, session_mgr.AGENTS.get("orchestrator", {})
            ).get("path", os.getcwd())

            if runtime == "gemini":
                _gemini_bin = _which_bin("gemini") or "gemini"
                cmd = [_gemini_bin]
                if permission_mode == "elevated":
                    cmd.append("--yolo")
                cmd.extend(["-o", "stream-json", "-p", context_prompt])
                if model:
                    cmd.extend(["--model", model])
            elif runtime == "opencode":
                _oc_bin = (
                    str(session_mgr.opencode_bin)
                    if session_mgr.opencode_bin
                    else (_which_bin("opencode") or "opencode")
                )
                cmd = [_oc_bin, "run", "--model", model, context_prompt]
            elif runtime == "codex":
                _codex_bin = _which_bin("codex") or "codex"
                cmd = [_codex_bin, "exec"]
                if permission_mode == "elevated":
                    cmd.extend(
                        [
                            "--dangerously-bypass-approvals-and-sandbox",
                            "-c",
                            "shell_environment_policy.inherit=all",
                        ]
                    )
                if model:
                    cmd.extend(["-m", model])
                cmd.append(context_prompt)
            elif runtime == "claude":
                _claude_bin = session_mgr.claude_bin or _which_bin("claude") or "claude"
                _claude_perm = {
                    "elevated": "bypassPermissions",
                    "sandboxed": "plan",
                }.get(permission_mode, "default")
                cmd = [
                    _claude_bin,
                    "-p",
                    context_prompt,
                    "--output-format",
                    "stream-json",
                    "--verbose",
                    "--model",
                    model,
                    "--permission-mode",
                    _claude_perm,
                ]
            elif runtime == "devin":
                _devin_bin = (
                    session_mgr.devin_bin
                    if hasattr(session_mgr, "devin_bin") and session_mgr.devin_bin
                    else (_which_bin("devin") or "devin")
                )
                # Devin CLI valid values: normal, dangerous, bypass (NOT "auto").
                # Background tasks are non-interactive (no human to approve),
                # so always use "dangerous" to prevent tool call rejections.
                _devin_perm = "dangerous"
                cmd = [_devin_bin, "-p", "--permission-mode", _devin_perm]
                if model:
                    cmd.extend(["--model", model])
                cmd.extend(["--", context_prompt])
            elif runtime == "cursor":
                _cursor_bin = (
                    session_mgr.cursor_bin
                    if hasattr(session_mgr, "cursor_bin") and session_mgr.cursor_bin
                    else (_which_bin("agent") or "agent")
                )
                # Validate cursor model - free plans require "auto"
                _cursor_model = model
                if not _cursor_model or not session_mgr.get_model_from_name(
                    _cursor_model, "cursor"
                ):
                    _cursor_model = os.environ.get("CURSOR_DEFAULT_MODEL", "auto")
                cmd = [_cursor_bin, "-p", "--trust"]
                if permission_mode == "elevated":
                    cmd.append("--yolo")
                cmd.extend(["--model", _cursor_model])
                cmd.extend(["--workspace", agent_dir])
                cmd.extend(["--", context_prompt])
            elif runtime == "wee":
                # Wee native runtime - uses standalone script with OpenAI SDK
                _wee_script = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "wee_runtime.py",
                )
                cmd = [
                    sys.executable,
                    _wee_script,
                    "--model",
                    model,
                    "--timeout",
                    str(timeout or 300),
                ]
                # Resolve api_base and api_key from session/env
                _wee_api_base = os.environ.get("WEE_API_BASE", "")
                _wee_api_key = os.environ.get("WEE_API_KEY", "")
                if _wee_api_base:
                    cmd.extend(["--api-base", _wee_api_base])
                if _wee_api_key:
                    cmd.extend(["--api-key", _wee_api_key])
                cmd.extend(["--system-prompt", context_prompt])
                cmd.append(prompt)
            elif runtime == "claude-sdk" or runtime == "copilot-sdk":
                # SDK runtimes (claude-sdk, copilot-sdk) require in-process execution
                # so we invoke agent_manager.py which will handle them internally
                agent_manager_script = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "agent_manager.py"
                )
                cmd = [
                    sys.executable,
                    agent_manager_script,
                    "--runtime", runtime,
                    "--model", model,
                    "--agent", agent,
                    context_prompt,
                    session_id or str(uuid4()),
                ]
            else:
                # Default: copilot runtime
                copilot_bin = (
                    session_mgr.copilot_bin
                    or _which_bin("copilot")
                    or "/home/flipkey/.local/bin/copilot"
                )
                cmd = [
                    copilot_bin,
                    "-p",
                    context_prompt,
                    "--no-color",
                    "--model",
                    model,
                    "--allow-all-tools",
                ]
                if permission_mode == "elevated":
                    cmd.extend(["--allow-all-paths", "--yolo"])

            proc_timeout = (timeout or 900) + 30
            env = {
                **os.environ,
                "COPILOT_AGENT": agent,
                "COPILOT_RUNTIME": runtime,
                "WEE_AGENT_DIR": agent_dir,
                "WEE_SESSION_ID": session_id,
                "WEE_TASK_ID": task_id,
            }

            # Use Popen for incremental output capture
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=agent_dir,
                bufsize=1,
            )

            bg_task_mgr.update_task(task_id, pid=process.pid)

            import threading

            stderr_lines = []

            def _drain_stderr():
                try:
                    for err_line in process.stderr:
                        stderr_lines.append(err_line)
                except Exception:
                    pass

            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            stdout_lines = []
            start_time = time.time()

            for line in process.stdout:
                stdout_lines.append(line)
                line_text = line.rstrip("\n\r")

                # Append to output_lines for live log viewing
                if line_text:
                    bg_task_mgr.append_output(task_id, line_text)

                # Capture [STATUS_UPDATE: ...] markers for mobile channel progress (F004)
                _su_bg_match = _re.search(r"\[STATUS_UPDATE[:\s]*(.+?)\]", line_text)
                if _su_bg_match:
                    session_mgr.set_live_status(
                        session_id, _su_bg_match.group(1).strip()
                    )

                # ── Structured JSON parsing for stream-json runtimes ──
                # Gemini (stream-json) emits {"type":"tool_use",...} and {"type":"tool_result",...}
                # Claude (stream-json) emits nested stream_event objects with tool_use blocks
                tc = None
                if runtime in ("gemini", "claude") and line_text.strip().startswith(
                    "{"
                ):
                    try:
                        _obj = _json.loads(line_text.strip())
                        _otype = _obj.get("type", "")

                        if runtime == "gemini":
                            if _otype == "tool_use":
                                _tool_call_counter += 1
                                tc = {
                                    "id": f"bg_{task_id[:8]}_{_tool_call_counter}",
                                    "name": _obj.get("tool_name", "tool"),
                                    "input": _json.dumps(_obj.get("parameters", {})),
                                    "status": "running",
                                    "runtime": runtime,
                                    "timestamp": _obj.get(
                                        "timestamp",
                                        time.strftime(
                                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                                        ),
                                    ),
                                }
                            elif _otype == "tool_result":
                                _tool_id = _obj.get("tool_id", "")
                                bg_task_mgr.update_tool_call(
                                    task_id,
                                    _tool_id,
                                    status=_obj.get("status", "completed"),
                                    output=_obj.get("output", "")[:500],
                                )

                        elif runtime == "claude":
                            if _otype == "stream_event":
                                _event = _obj.get("event") or {}
                                _inner = _event.get("type", "")
                                if _inner == "content_block_start":
                                    _cb = _event.get("content_block") or {}
                                    if _cb.get("type") == "tool_use":
                                        _tool_call_counter += 1
                                        tc = {
                                            "id": _cb.get(
                                                "id",
                                                f"bg_{task_id[:8]}_{_tool_call_counter}",
                                            ),
                                            "name": _cb.get("name", "tool"),
                                            "input": _json.dumps(_cb.get("input", {})),
                                            "status": "running",
                                            "runtime": runtime,
                                            "timestamp": time.strftime(
                                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                                            ),
                                        }
                    except (ValueError, KeyError, TypeError):
                        pass

                # Fall back to text-based pattern detection
                if not tc:
                    tc = _parse_tool_call_from_line(line_text, runtime)

                if tc:
                    bg_task_mgr.append_tool_call(task_id, tc)

                # Check timeout
                if time.time() - start_time > proc_timeout:
                    process.kill()
                    break

            process.stdout.close()
            stderr_thread.join(timeout=5)
            process.wait()

            output = "".join(stdout_lines).strip()
            if stderr_lines:
                output += "\n[stderr]\n" + "".join(stderr_lines)

            if process.returncode == 0:
                # Strip CLI metadata (tool decoration, stats) from final output
                final_output = (
                    session_mgr.strip_metadata(output, runtime)
                    if output
                    else "Task completed successfully"
                )
                if not final_output.strip():
                    final_output = output or "Task completed successfully"
                session_mgr.clear_live_status(session_id)
                bg_task_mgr.complete_task(task_id, final_output)
                if task_id.startswith("sched_"):
                    try:
                        job_id = task_id.split("_")[1]
                        sched = _get_scheduler()
                        job = sched.get_job(job_id).get("result")
                        if job:
                            sched.save_result(
                                job_id, job.get("name", job_id), True, final_output
                            )
                    except:
                        pass
                _emit_bg_notification(
                    task_id,
                    prompt,
                    "completed",
                    channel,
                    user_identity,
                    output_preview=final_output,
                    error=None,
                    notify=notify,
                )
            else:
                error_msg = f"Task failed with code {process.returncode}: {output}"
                bg_task_mgr.fail_task(task_id, error_msg)
                if task_id.startswith("sched_"):
                    try:
                        job_id = task_id.split("_")[1]
                        sched = _get_scheduler()
                        job = sched.get_job(job_id).get("result")
                        if job:
                            sched.save_result(
                                job_id, job.get("name", job_id), False, "", error_msg
                            )
                    except:
                        pass
                _emit_bg_notification(
                    task_id,
                    prompt,
                    "failed",
                    channel,
                    user_identity,
                    output_preview=None,
                    error=error_msg,
                    notify=notify,
                )

        except subprocess.TimeoutExpired:
            error_msg = f"Task exceeded timeout of {timeout} seconds"
            bg_task_mgr.fail_task(task_id, error_msg)
            _emit_bg_notification(
                task_id,
                prompt,
                "failed",
                channel,
                user_identity,
                output_preview=None,
                error=error_msg,
                notify=notify,
            )
        except Exception as exc:
            error_msg = str(exc)
            bg_task_mgr.fail_task(task_id, error_msg)
            _emit_bg_notification(
                task_id,
                prompt,
                "failed",
                channel,
                user_identity,
                output_preview=None,
                error=error_msg,
                notify=notify,
            )
        finally:
            # Clean up steering file for completed task
            bg_task_mgr.cleanup_steering(task_id)

            # Promote next queued task for this user if a slot just opened
            try:
                next_q = bg_task_mgr.get_next_queued(channel, user_identity, agent)
                if next_q:
                    new_sid = str(uuid4())
                    bg_task_mgr.promote_queued_task(next_q["task_id"], new_sid)
                    print(f"[BG] Promoting queued task {next_q['task_id']} → running")
                    bg_executor.submit(
                        _run_background_task,
                        next_q["task_id"],
                        new_sid,
                        next_q["prompt"],
                        next_q["agent"],
                        next_q["runtime"],
                        next_q["model"],
                        next_q["channel"],
                        next_q["user_identity"],
                        next_q.get("timeout") or 900,
                        next_q.get("notify", True),
                    )
            except Exception as promo_exc:
                print(f"[BG] Error promoting queued task: {promo_exc}")

    @app.post("/api/v1/background-tasks")
    async def create_background_task(body: BackgroundTaskRequest, request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )

        channel = user["channel"]
        identity = user["identity"]

        # Check concurrent limit (used below after resolving params)

        # Resolve agent/runtime/model — default to user's current session config
        # Determine defaults by searching for ANY session for this identity across all channels
        # to inherit preferences (like notification_preference).
        session_map = await asyncio.to_thread(session_mgr.load_session_map)
        # Inherit only safe fields (never 'agent') from prior sessions — see issue #75
        defaults = _compute_bg_task_defaults(session_map, identity, channel)

        agent = body.agent or defaults.get("agent", get_default_agent())
        runtime = body.runtime or defaults.get("runtime", get_default_runtime())
        model = body.model or defaults.get("model", get_default_model())

        task_id = f"bg_{str(uuid4())[:8]}"
        session_id = str(uuid4())  # Must be valid UUID format for Copilot CLI

        # Use agent-specified timeout or fall back to default (15 min)
        bg_timeout = (
            body.timeout if body.timeout is not None else get_bg_command_timeout()
        )

        # Determine notification preference:
        #   body override > global mute > per-identity store > session default > True
        notify_pref = body.notify
        if notify_pref is None:
            if notification_mgr:
                # Global mute takes priority (covers cross-channel identity mismatch)
                if notification_mgr.is_muted("_global"):
                    notify_pref = False
                # Per-identity store is authoritative
                elif notification_mgr.is_muted(identity):
                    notify_pref = False
            if notify_pref is None:
                session_pref = defaults.get("notification_preference", "all")
                notify_pref = session_pref != "off"

        # Memory injection is handled at session creation in build_agent_context_prompt
        # (not here at API time) so queued/promoted tasks get fresh context.
        effective_prompt = body.prompt

        # Check concurrent limit — queue instead of rejecting
        running = await asyncio.to_thread(
            bg_task_mgr.count_running, channel, identity, agent
        )
        agent_config = session_mgr.AGENTS.get(agent, {})
        max_concurrent = agent_config.get(
            "max_concurrent", BackgroundTaskManager.MAX_TASKS_PER_USER
        )
        if running >= max_concurrent:
            # Queue the task — it will be promoted when a running task finishes
            task = await asyncio.to_thread(
                bg_task_mgr.create_task,
                task_id=task_id,
                session_id=session_id,
                user_identity=identity,
                channel=channel,
                agent=agent,
                runtime=runtime,
                model=model,
                prompt=effective_prompt,
                status="queued",
                timeout=bg_timeout,
                notify=notify_pref,
                origin_session_id=body.origin_session_id,
            )
            queue_pos = await asyncio.to_thread(
                bg_task_mgr.count_queued, channel, identity
            )
            print(
                f"[API] Task {task_id} queued (position {queue_pos}, {running}/{BackgroundTaskManager.MAX_TASKS_PER_USER} slots full)"
            )
            return {
                "task_id": task_id,
                "session_id": session_id,
                "agent": agent,
                "runtime": runtime,
                "model": model,
                "permission_mode": body.permission_mode or "restricted",
                "status": "queued",
                "queue_position": queue_pos,
                "timeout": bg_timeout,
            }

        # Create task record (running immediately)
        task = await asyncio.to_thread(
            bg_task_mgr.create_task,
            task_id=task_id,
            session_id=session_id,
            user_identity=identity,
            channel=channel,
            agent=agent,
            runtime=runtime,
            model=model,
            prompt=effective_prompt,
            status="running",
            timeout=bg_timeout,
            notify=notify_pref,
            origin_session_id=body.origin_session_id,
        )

        # Resolve permission mode (default: restricted)
        perm_mode = body.permission_mode or "restricted"
        if perm_mode not in ("elevated", "restricted", "sandboxed"):
            perm_mode = "restricted"

        # Run in background thread using shared executor
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            bg_executor,
            _run_background_task,
            task_id,
            session_id,
            effective_prompt,
            agent,
            runtime,
            model,
            channel,
            identity,
            bg_timeout,
            notify_pref,
            perm_mode,
        )

        return {
            "task_id": task_id,
            "session_id": session_id,
            "agent": agent,
            "runtime": runtime,
            "model": model,
            "permission_mode": perm_mode,
            "status": "running",
            "timeout": bg_timeout,
        }

    @app.get("/api/v1/sessions/{session_id}/bg-events")
    async def get_session_bg_events(session_id: str, request: Request):
        """Return and clear pending BG task completion events."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        events = bg_task_mgr.pop_bg_events(session_id)
        return {"events": events}

    @app.get("/api/v1/background-tasks")
    async def list_background_tasks(
        request: Request,
        limit: int = 50,
        offset: int = 0,
        status: str = None,
    ):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        tasks = bg_task_mgr.list_all_tasks()
        # Check if running tasks are still alive
        for t in tasks:
            if t["status"] == "running" and t.get("pid"):
                try:
                    os.kill(t["pid"], 0)
                except ProcessLookupError:
                    bg_task_mgr.fail_task(
                        t["task_id"], "Process terminated unexpectedly"
                    )
                    t["status"] = "failed"
        # Filter by status if provided
        if status:
            tasks = [t for t in tasks if t["status"] == status]
        # Sort by created_at descending (newest first)
        tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        total = len(tasks)
        # Apply pagination
        tasks = tasks[offset : offset + limit]
        # Return summary (no full output_lines for list)
        result = []
        for t in tasks:
            result.append(
                {
                    "task_id": t["task_id"],
                    "agent": t["agent"],
                    "runtime": t["runtime"],
                    "model": t["model"],
                    "prompt": t["prompt"][:200],
                    "status": t["status"],
                    "created_at": t["created_at"],
                    "completed_at": t.get("completed_at"),
                    "error": t.get("error"),
                }
            )
        return {
            "tasks": result,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/v1/background-tasks/{task_id}")
    async def get_background_task(task_id: str, request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        task = await asyncio.to_thread(bg_task_mgr.get_task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        # Return detail with last 50 output lines
        tool_calls = task.get("tool_calls", [])
        return {
            "task_id": task["task_id"],
            "session_id": task["session_id"],
            "agent": task["agent"],
            "runtime": task["runtime"],
            "model": task["model"],
            "prompt": task["prompt"],
            "status": task["status"],
            "pid": task.get("pid"),
            "created_at": task["created_at"],
            "completed_at": task.get("completed_at"),
            "recent_output": task.get("output_lines", [])[-50:],
            "error": task.get("error"),
            "tool_call_count": len(tool_calls),
            "recent_tool_calls": tool_calls[-20:],
        }

    @app.get("/api/v1/background-tasks/{task_id}/transcript")
    async def get_background_task_transcript(task_id: str, request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        task = await asyncio.to_thread(bg_task_mgr.get_task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "final_response": task.get("final_response"),
            "output_lines": task.get("output_lines", []),
            "error": task.get("error"),
        }

    @app.get("/api/v1/background-tasks/{task_id}/logs")
    async def get_background_task_logs(task_id: str, request: Request):
        """Return all output lines for a background task (for live log streaming)."""
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        task = await asyncio.to_thread(bg_task_mgr.get_task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "created_at": task["created_at"],
            "completed_at": task.get("completed_at"),
            "output_lines": task.get("output_lines", []),
            "error": task.get("error"),
        }

    @app.get("/api/v1/background-tasks/{task_id}/tool-calls")
    async def get_background_task_tool_calls(task_id: str, request: Request):
        """Return all tool calls for a background task."""
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        task = await asyncio.to_thread(bg_task_mgr.get_task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "tool_calls": [
                _sanitize_tool_call_for_display(tc) for tc in task.get("tool_calls", [])
            ],
            "tool_call_count": len(task.get("tool_calls", [])),
        }

    @app.delete("/api/v1/background-tasks/{task_id}")
    async def delete_background_task(task_id: str, request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        task = await asyncio.to_thread(bg_task_mgr.get_task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task["status"] in ("running", "queued"):
            was_running = task["status"] == "running"
            await asyncio.to_thread(bg_task_mgr.kill_task, task_id)
            # If a running task was killed, promote the next queued task
            if was_running:
                next_q = await asyncio.to_thread(
                    bg_task_mgr.get_next_queued, user["channel"], user["identity"]
                )
                if next_q:
                    new_sid = str(uuid4())
                    await asyncio.to_thread(
                        bg_task_mgr.promote_queued_task, next_q["task_id"], new_sid
                    )
                    print(
                        f"[BG] Kill triggered promotion of queued task {next_q['task_id']}"
                    )
                    loop = asyncio.get_running_loop()
                    loop.run_in_executor(
                        bg_executor,
                        _run_background_task,
                        next_q["task_id"],
                        new_sid,
                        next_q["prompt"],
                        next_q["agent"],
                        next_q["runtime"],
                        next_q["model"],
                        next_q["channel"],
                        next_q["user_identity"],
                        next_q.get("timeout") or 900,
                        next_q.get("notify", True),
                        next_q.get("permission_mode", "restricted"),
                    )
            return {"task_id": task_id, "action": "killed"}
        else:
            await asyncio.to_thread(bg_task_mgr.delete_task, task_id)
            return {"task_id": task_id, "action": "deleted"}

    # --- Background Task Steering ---

    class SteerRequest(BaseModel):
        instruction: str

        @field_validator("instruction")
        @classmethod
        def validate_instruction(cls, v):
            if not v or not v.strip():
                raise ValueError("Instruction must not be empty")
            if len(v) > 5000:
                raise ValueError("Instruction must be 5,000 characters or less")
            return v.strip()

    @app.post("/api/v1/background-tasks/{task_id}/steer")
    async def steer_background_task(task_id: str, body: SteerRequest, request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        task = await asyncio.to_thread(bg_task_mgr.get_task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if task["status"] != "running":
            raise HTTPException(
                status_code=409,
                detail=f"Task is {task['status']}, not running -- cannot steer",
            )
        path = await asyncio.to_thread(
            bg_task_mgr.write_steering, task_id, body.instruction
        )
        logger.info(
            "[STEER] Steering written for task %s: %s", task_id, body.instruction[:80]
        )
        return {
            "task_id": task_id,
            "status": "steering_written",
            "steering_file": path,
            "instruction_preview": body.instruction[:200],
        }

    @app.get("/api/v1/background-tasks/{task_id}/steering")
    async def get_steering(task_id: str, request: Request):
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        task = await asyncio.to_thread(bg_task_mgr.get_task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        content = await asyncio.to_thread(bg_task_mgr.read_steering, task_id)
        return {
            "task_id": task_id,
            "has_steering": content is not None,
            "content": content,
        }

    # --- Notifications ---

    @app.get("/api/v1/notifications")
    async def list_notifications(request: Request, unread_only: bool = False):
        """Return background task completion notifications for the authenticated user."""
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        if notification_mgr is None:
            return {"notifications": [], "unread_count": 0}
        user_key = bg_task_mgr._user_key(user["channel"], user["identity"])
        notifications = notification_mgr.list_notifications(
            user_key, unread_only=unread_only
        )
        unread_count = sum(1 for n in notifications if not n.get("read", False))
        return {"notifications": notifications, "unread_count": unread_count}

    @app.post("/api/v1/notifications/{notification_id}/read")
    async def mark_notification_read(notification_id: str, request: Request):
        """Mark a single notification as read."""
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        if notification_mgr is None:
            raise HTTPException(
                status_code=503, detail="Notification manager unavailable"
            )
        user_key = bg_task_mgr._user_key(user["channel"], user["identity"])
        ok = notification_mgr.mark_read(notification_id, user_key)
        if not ok:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"notification_id": notification_id, "read": True}

    @app.post("/api/v1/notifications/read-all")
    async def mark_all_notifications_read(request: Request):
        """Mark all notifications as read for the current user."""
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        if notification_mgr is None:
            raise HTTPException(
                status_code=503, detail="Notification manager unavailable"
            )
        user_key = bg_task_mgr._user_key(user["channel"], user["identity"])
        count = notification_mgr.mark_all_read(user_key)
        return {"marked_read": count}

    @app.delete("/api/v1/notifications/{notification_id}")
    async def delete_notification(notification_id: str, request: Request):
        """Delete a specific notification."""
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        if notification_mgr is None:
            raise HTTPException(
                status_code=503, detail="Notification manager unavailable"
            )
        user_key = bg_task_mgr._user_key(user["channel"], user["identity"])
        ok = notification_mgr.delete_notification(notification_id, user_key)
        if not ok:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"notification_id": notification_id, "deleted": True}

    @app.delete("/api/v1/notifications")
    async def delete_read_notifications(request: Request):
        """Delete all read notifications for the current user."""
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        if notification_mgr is None:
            raise HTTPException(
                status_code=503, detail="Notification manager unavailable"
            )
        user_key = bg_task_mgr._user_key(user["channel"], user["identity"])
        deleted = notification_mgr.delete_all_read(user_key)
        return {"deleted": deleted}

    # --- Task Scheduler ---
    if SCHEDULER_ENABLED:
        # Lazy-load TaskScheduler so the API starts even if the scheduler dirs don't exist yet.
        _task_scheduler = None

        def _get_scheduler():
            nonlocal _task_scheduler
            if _task_scheduler is None:
                try:
                    import sys as _sys

                    _sched_path = str(Path(__file__).parent)
                    if _sched_path not in _sys.path:
                        _sys.path.insert(0, _sched_path)
                    from scheduler.management import TaskScheduler

                    _task_scheduler = TaskScheduler()
                except Exception as _e:
                    raise HTTPException(
                        status_code=503, detail=f"Scheduler unavailable: {_e}"
                    )
            return _task_scheduler

        # ---- Scheduler authorization ----
        # Override with comma-separated env vars to add more users without code changes.
        _sched_allowed_telegram = {
            u.strip().lower().lstrip("@")
            for u in os.environ.get("SCHEDULER_ALLOWED_TELEGRAM", "").split(",")
            if u.strip()
        }
        _sched_allowed_webex = {
            u.strip().lower()
            for u in os.environ.get("SCHEDULER_ALLOWED_WEBEX", "").split(",")
            if u.strip()
        }
        if not _sched_allowed_telegram and not _sched_allowed_webex:
            print(
                "[SECURITY][WARN] No scheduler allowlist configured — set SCHEDULER_ALLOWED_TELEGRAM and/or SCHEDULER_ALLOWED_WEBEX env vars",
                file=sys.stderr,
            )

        async def _require_scheduler_auth(request: Request) -> dict:
            """Authenticate AND verify the user is allowed to manage scheduled tasks.

            Raises HTTP 403 if authenticated but not in the allowlist.
            Returns the user dict on success.
            """
            user = await authenticate(
                request,
                authorization=request.headers.get("authorization"),
                x_user_identity=request.headers.get("x-user-identity"),
                x_auth_channel=request.headers.get("x-auth-channel"),
            )

            # Shared-key callers (internal/admin) are always allowed.
            if user.get("auth_type") == "shared_key":
                return user

            channel = user.get("channel", "")
            identity = user.get("identity", "")

            if channel == "telegram":
                # identity is a numeric chat_id; resolve to username for the allowlist check.
                username = _get_telegram_username(identity) or ""
                if username.lower() in _sched_allowed_telegram:
                    return user
            elif channel == "webex":
                if identity.lower() in _sched_allowed_webex:
                    return user

            raise HTTPException(
                status_code=403,
                detail="You are not authorized to manage scheduled tasks.",
            )

        class ScheduleJobRequest(BaseModel):
            name: str
            schedule: str
            agent: Optional[str] = None
            runtime: Optional[str] = None
            model: Optional[str] = None
            mode: Optional[str] = None  # "ai" (default, uses LLM) or "command" (shell)
            task: str = ""
            notify: bool = False
            fallback_runtime: Optional[str] = None
            fallback_model: Optional[str] = None
            recurring: bool = True
            timeout: Optional[int] = None  # Execution timeout in seconds (default: 300)
            permission_mode: Optional[str] = (
                None  # elevated, restricted (default), sandboxed
            )

        class UpdateJobRequest(BaseModel):
            name: Optional[str] = None
            schedule: Optional[str] = None
            agent: Optional[str] = None
            runtime: Optional[str] = None
            model: Optional[str] = None
            mode: Optional[str] = None  # "ai" (default, uses LLM) or "command" (shell)
            task: Optional[str] = None
            notify: Optional[bool] = None
            fallback_runtime: Optional[str] = None
            fallback_model: Optional[str] = None
            recurring: Optional[bool] = None
            enabled: Optional[bool] = None
            timeout: Optional[int] = None  # Execution timeout in seconds
            permission_mode: Optional[str] = (
                None  # elevated, restricted (default), sandboxed
            )

        class ValidateScheduleRequest(BaseModel):
            schedule: str

        @app.post("/api/v1/scheduler/validate-schedule")
        async def validate_schedule(body: ValidateScheduleRequest, request: Request):
            """Convert natural language schedule to cron format using AI + deterministic fallback."""
            await _require_scheduler_auth(request)
            client_ip = request.client.host if request.client else "unknown"
            if not rate_limiter.check(
                client_ip, "scheduler_write", max_requests=30, window=60
            ):
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            from scheduler.management import convert_schedule

            result = convert_schedule(body.schedule.strip(), use_ai=True)
            return {
                "success": result.get("cron") is not None
                or result.get("next_run") is not None,
                "cron": result.get("cron"),
                "next_run": result.get("next_run"),
                "human_readable": result.get("human_readable", ""),
                "method": result.get("method", "failed"),
                "original": result.get("original", body.schedule),
            }

        @app.get("/api/v1/scheduler/status")
        async def scheduler_status(request: Request):
            await _require_scheduler_auth(request)
            return _get_scheduler().doctor()

        @app.get("/api/v1/scheduler/jobs")
        async def list_scheduler_jobs(request: Request):
            await _require_scheduler_auth(request)
            return _get_scheduler().list_jobs()

        @app.post("/api/v1/scheduler/jobs")
        async def create_scheduler_job(body: ScheduleJobRequest, request: Request):
            user = await _require_scheduler_auth(request)
            client_ip = request.client.host if request.client else "unknown"
            if not rate_limiter.check(
                client_ip, "scheduler_write", max_requests=20, window=60
            ):
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            # Resolve Telegram username for storage so the executor can display it
            username = None
            if user.get("channel") == "telegram":
                username = _get_telegram_username(user["identity"])
            created_by = {
                "identity": user["identity"],
                "channel": user["channel"],
                "username": username,
            }
            result = _get_scheduler().schedule_task(
                name=body.name,
                schedule=body.schedule,
                agent=body.agent,
                runtime=body.runtime,
                model=body.model,
                fallback_runtime=body.fallback_runtime,
                fallback_model=body.fallback_model,
                mode=body.mode,
                task=body.task,
                notify=body.notify,
                recurring=body.recurring,
                created_by=created_by,
                timeout=body.timeout,
                permission_mode=body.permission_mode,
            )
            if not result.get("success"):
                raise HTTPException(
                    status_code=400, detail=result.get("message", "Failed")
                )
            return result

        @app.get("/api/v1/scheduler/jobs/{job_id}")
        async def get_scheduler_job(job_id: str, request: Request):
            await _require_scheduler_auth(request)
            result = _get_scheduler().get_job(job_id)
            if not result.get("success"):
                raise HTTPException(
                    status_code=404, detail=result.get("message", "Not found")
                )
            return result

        @app.put("/api/v1/scheduler/jobs/{job_id}")
        async def update_scheduler_job(
            job_id: str, body: UpdateJobRequest, request: Request
        ):
            await _require_scheduler_auth(request)
            client_ip = request.client.host if request.client else "unknown"
            if not rate_limiter.check(
                client_ip, "scheduler_write", max_requests=20, window=60
            ):
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            updates = body.model_dump(exclude_unset=True)
            if not updates:
                raise HTTPException(status_code=400, detail="No fields to update")
            result = _get_scheduler().update_job(job_id, updates)
            if not result.get("success"):
                raise HTTPException(
                    status_code=404, detail=result.get("message", "Not found")
                )
            return result

        @app.delete("/api/v1/scheduler/jobs/{job_id}")
        async def delete_scheduler_job(job_id: str, request: Request):
            await _require_scheduler_auth(request)
            client_ip = request.client.host if request.client else "unknown"
            if not rate_limiter.check(
                client_ip, "scheduler_write", max_requests=20, window=60
            ):
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            result = _get_scheduler().delete_job(job_id)
            if not result.get("success"):
                raise HTTPException(
                    status_code=404, detail=result.get("message", "Not found")
                )
            return result

        @app.post("/api/v1/scheduler/jobs/{job_id}/pause")
        async def pause_scheduler_job(job_id: str, request: Request):
            await _require_scheduler_auth(request)
            client_ip = request.client.host if request.client else "unknown"
            if not rate_limiter.check(
                client_ip, "scheduler_write", max_requests=20, window=60
            ):
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            result = _get_scheduler().pause_job(job_id)
            if not result.get("success"):
                raise HTTPException(
                    status_code=404, detail=result.get("message", "Not found")
                )
            return result

        @app.post("/api/v1/scheduler/jobs/{job_id}/resume")
        async def resume_scheduler_job(job_id: str, request: Request):
            await _require_scheduler_auth(request)
            client_ip = request.client.host if request.client else "unknown"
            if not rate_limiter.check(
                client_ip, "scheduler_write", max_requests=20, window=60
            ):
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            result = _get_scheduler().resume_job(job_id)
            if not result.get("success"):
                raise HTTPException(
                    status_code=404, detail=result.get("message", "Not found")
                )
            return result

        @app.post("/api/v1/scheduler/jobs/{job_id}/run")
        async def run_scheduler_job_now(job_id: str, request: Request):
            user = await _require_scheduler_auth(request)
            client_ip = request.client.host if request.client else "unknown"
            if not rate_limiter.check(
                client_ip, "scheduler_write", max_requests=20, window=60
            ):
                raise HTTPException(status_code=429, detail="Rate limit exceeded")

            result = _get_scheduler().get_job(job_id)
            if not result.get("success"):
                raise HTTPException(
                    status_code=404, detail=result.get("message", "Not found")
                )
            job = result["result"]

            # Mark last_run immediately so the scheduler doesn't double-fire
            _get_scheduler().run_job(job_id)

            # Determine runtime parameters from the job
            agent = job.get("agent") or get_default_agent()
            runtime = job.get("runtime") or get_default_runtime()
            model = job.get("model") or get_default_model()
            task = job.get("task") or ""
            timeout = int(job.get("timeout") or 300)
            mode = job.get("mode", "ai")

            # Use the triggering user's identity/channel for the bg task
            channel = user.get("channel", "api")
            identity = user.get("identity", "scheduler")

            task_id = f"sched_{job_id}_{str(uuid4())[:6]}"
            session_id = str(uuid4())

            if mode == "command":
                # ---- Command mode: execute shell command directly (no LLM) ----
                working_dir = job.get("working_dir", "/opt")

                bg_task_mgr.create_task(
                    task_id=task_id,
                    session_id=session_id,
                    user_identity=identity,
                    channel=channel,
                    agent="command",
                    runtime="shell",
                    model="n/a",
                    prompt=task,
                    status="running",
                    timeout=timeout,
                    notify=job.get("notify", False),
                )

                loop = asyncio.get_running_loop()
                loop.run_in_executor(
                    bg_executor,
                    _run_command_task,
                    task_id,
                    task,
                    working_dir,
                    timeout,
                    job_id,
                    job.get("name", job_id),
                    job.get("notify", False),
                )

                return {
                    "success": True,
                    "task_id": task_id,
                    "job_id": job_id,
                    "mode": "command",
                    "status": "running",
                    "message": f"Command job '{job.get('name', job_id)}' is now running (direct shell execution)",
                }
            else:
                # ---- AI mode: dispatch to LLM background task ----
                prompt = task or f"Run scheduled job: {job.get('name', job_id)}"

                # Resolve permission mode from job config
                perm_mode = job.get("permission_mode", "restricted")
                if perm_mode not in ("elevated", "restricted", "sandboxed"):
                    perm_mode = "restricted"

                bg_task_mgr.create_task(
                    task_id=task_id,
                    session_id=session_id,
                    user_identity=identity,
                    channel=channel,
                    agent=agent,
                    runtime=runtime,
                    model=model,
                    prompt=prompt,
                    status="running",
                    timeout=timeout,
                    notify=job.get("notify", False),
                )

                loop = asyncio.get_running_loop()
                loop.run_in_executor(
                    bg_executor,
                    _run_background_task,
                    task_id,
                    session_id,
                    prompt,
                    agent,
                    runtime,
                    model,
                    channel,
                    identity,
                    timeout,
                    job.get("notify", False),
                    perm_mode,
                )

                return {
                    "success": True,
                    "task_id": task_id,
                    "job_id": job_id,
                    "agent": agent,
                    "runtime": runtime,
                    "permission_mode": perm_mode,
                    "status": "running",
                    "message": f"Job '{job.get('name', job_id)}' is now running",
                }

        @app.get("/api/v1/scheduler/jobs/{job_id}/results")
        async def get_scheduler_job_results(
            job_id: str, request: Request, limit: int = 20
        ):
            await _require_scheduler_auth(request)
            limit = min(max(1, limit), 100)
            return _get_scheduler().get_results(job_id, limit=limit)

        @app.get("/api/v1/scheduler/jobs/{job_id}/logs")
        async def get_scheduler_job_logs(job_id: str, request: Request):
            await _require_scheduler_auth(request)
            return _get_scheduler().get_logs(job_id)

    # --- Wee Canvas ───────────────────────────────────────────────────────────
    # In-memory canvas session state: session_id → {components, connections, action_watchers, pending_actions, name, created_at, last_activity}
    _canvas_sessions: dict = {}
    _CANVAS_PERSIST_DIR = Path(SCRIPT_BASE_DIR) / ".canvas-sessions"
    _CANVAS_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    _CANVAS_SESSION_TIMEOUT = (
        int(os.environ.get("CANVAS_SESSION_TIMEOUT_MINUTES", "30")) * 60
    )
    _canvas_cleanup_started = False

    def _get_canvas_session(session_id: str) -> dict:
        now = time.time()
        if session_id not in _canvas_sessions:
            _canvas_sessions[session_id] = {
                "components": [],
                "connections": set(),
                "action_watchers": set(),
                "pending_actions": [],
                "name": None,
                "created_at": now,
                "last_activity": now,
            }
        return _canvas_sessions[session_id]

    def _canvas_touch(sess: dict):
        sess["last_activity"] = time.time()

    def _canvas_persist_to_disk(session_id: str, sess: dict, closed_at: float = None):
        """Save session state to disk as JSON."""
        data = {
            "session_id": session_id,
            "name": sess.get("name"),
            "components": sess.get("components", []),
            "created_at": sess.get("created_at", time.time()),
            "last_activity": sess.get("last_activity", time.time()),
            "closed_at": closed_at or time.time(),
        }
        path = _CANVAS_PERSIST_DIR / f"{session_id}.json"
        path.write_text(json.dumps(data, default=str), encoding="utf-8")

    def _canvas_load_from_disk(session_id: str) -> dict | None:
        path = _CANVAS_PERSIST_DIR / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _canvas_list_persisted() -> list[dict]:
        """List all persisted (closed) sessions from disk."""
        results = []
        for f in _CANVAS_PERSIST_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sid = data.get("session_id", f.stem)
                # Skip sessions that are currently active in memory
                if sid not in _canvas_sessions:
                    results.append(data)
            except Exception:
                continue
        return results

    def _canvas_delete_persisted(session_id: str):
        path = _CANVAS_PERSIST_DIR / f"{session_id}.json"
        if path.exists():
            path.unlink()

    async def _canvas_cleanup_loop():
        """Background task: every 5 min, expire idle sessions to disk."""
        while True:
            await asyncio.sleep(300)
            try:
                now = time.time()
                to_expire = []
                for sid, sess in list(_canvas_sessions.items()):
                    if (
                        not sess["connections"]
                        and (now - sess["last_activity"]) > _CANVAS_SESSION_TIMEOUT
                    ):
                        to_expire.append(sid)
                for sid in to_expire:
                    sess = _canvas_sessions.pop(sid, None)
                    if sess and sess.get("components"):
                        _canvas_persist_to_disk(sid, sess)
            except Exception:
                pass

    async def _canvas_broadcast(session: dict, skip_ws, message: dict):
        payload = json.dumps(message)
        for conn in list(session["connections"]):
            if conn is not skip_ws:
                try:
                    await conn.send_text(payload)
                except Exception:
                    session["connections"].discard(conn)
                    session["action_watchers"].discard(conn)

    def _canvas_apply_update(components: list, node_id: str, changes: dict) -> bool:
        for comp in components:
            if isinstance(comp, dict):
                if comp.get("id") == node_id:
                    comp.update(changes)
                    return True
                for key in (
                    "children",
                    "items",
                    "columns",
                    "steps",
                    "rows",
                    "metrics",
                    "fields",
                ):
                    children = comp.get(key, [])
                    if isinstance(children, list) and _canvas_apply_update(
                        children, node_id, changes
                    ):
                        return True
        return False

    @app.websocket("/canvas/ws")
    async def canvas_websocket(websocket: WebSocket, session: str = "default"):
        nonlocal _canvas_cleanup_started
        if not _canvas_cleanup_started:
            _canvas_cleanup_started = True
            asyncio.create_task(_canvas_cleanup_loop())

        await websocket.accept()
        sess = _get_canvas_session(session)
        sess["connections"].add(websocket)
        _canvas_touch(sess)

        # Restore current state for new connections
        if sess["components"]:
            try:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "restore",
                            "components": sess["components"],
                            "session_id": session,
                        }
                    )
                )
            except Exception:
                pass

        try:
            while True:
                raw = await websocket.receive_text()
                _canvas_touch(sess)
                try:
                    data = json.loads(raw)
                except Exception:
                    continue

                msg_type = data.get("type")

                if msg_type == "render":
                    sess["components"] = data.get("components", [])
                    await _canvas_broadcast(
                        sess,
                        websocket,
                        {
                            "type": "render",
                            "components": sess["components"],
                            "session_id": session,
                        },
                    )

                elif msg_type == "update":
                    node_id = data.get("node_id")
                    changes = data.get("changes", {})
                    _canvas_apply_update(sess["components"], node_id, changes)
                    await _canvas_broadcast(
                        sess,
                        websocket,
                        {
                            "type": "update",
                            "node_id": node_id,
                            "changes": changes,
                        },
                    )

                elif msg_type == "clear":
                    sess["components"] = []
                    await _canvas_broadcast(
                        sess,
                        websocket,
                        {
                            "type": "clear",
                            "session_id": session,
                        },
                    )

                elif msg_type == "action":
                    sess["pending_actions"].append(data)
                    for watcher in list(sess["action_watchers"]):
                        try:
                            await watcher.send_text(json.dumps(data))
                        except Exception:
                            sess["action_watchers"].discard(watcher)

                elif msg_type == "subscribe_actions":
                    sess["action_watchers"].add(websocket)
                    for action in list(sess["pending_actions"]):
                        try:
                            await websocket.send_text(json.dumps(action))
                        except Exception:
                            break
                    sess["pending_actions"].clear()

        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            sess["connections"].discard(websocket)
            sess["action_watchers"].discard(websocket)
            _canvas_touch(sess)
            # Clean up empty sessions with no components
            if not sess["connections"] and not sess["components"]:
                _canvas_sessions.pop(session, None)

    @app.get("/api/v1/canvas/sessions")
    async def get_canvas_sessions():
        """Return list of active and closed canvas sessions."""
        result = []
        for sid, sess in _canvas_sessions.items():
            result.append(
                {
                    "session_id": sid,
                    "name": sess.get("name"),
                    "component_count": len(sess["components"]),
                    "connection_count": len(sess["connections"]),
                    "created_at": sess.get("created_at"),
                    "last_activity": sess.get("last_activity"),
                    "status": "active",
                }
            )
        # Include persisted (closed) sessions from disk
        for data in _canvas_list_persisted():
            result.append(
                {
                    "session_id": data["session_id"],
                    "name": data.get("name"),
                    "component_count": len(data.get("components", [])),
                    "connection_count": 0,
                    "created_at": data.get("created_at"),
                    "last_activity": data.get("last_activity"),
                    "closed_at": data.get("closed_at"),
                    "status": "closed",
                }
            )
        return {"sessions": result}

    @app.patch("/api/v1/canvas/sessions/{session_id}/name")
    async def set_canvas_session_name(session_id: str, request: Request):
        """Set or update the name of a canvas session."""
        body = await request.json()
        name = body.get("name", "").strip() or None
        if session_id in _canvas_sessions:
            _canvas_sessions[session_id]["name"] = name
            return {"success": True, "session_id": session_id, "name": name}
        # Check if it's a persisted session
        data = _canvas_load_from_disk(session_id)
        if data:
            data["name"] = name
            (_CANVAS_PERSIST_DIR / f"{session_id}.json").write_text(
                json.dumps(data, default=str), encoding="utf-8"
            )
            return {"success": True, "session_id": session_id, "name": name}
        raise HTTPException(status_code=404, detail="Session not found")

    @app.post("/api/v1/canvas/sessions/{session_id}/restore")
    async def restore_canvas_session(session_id: str):
        """Restore a persisted session back into memory."""
        data = _canvas_load_from_disk(session_id)
        if not data:
            if session_id in _canvas_sessions:
                return {
                    "success": True,
                    "session_id": session_id,
                    "status": "already_active",
                }
            raise HTTPException(status_code=404, detail="Session not found on disk")
        # Re-create in-memory session
        _canvas_sessions[session_id] = {
            "components": data.get("components", []),
            "connections": set(),
            "action_watchers": set(),
            "pending_actions": [],
            "name": data.get("name"),
            "created_at": data.get("created_at", time.time()),
            "last_activity": time.time(),
        }
        _canvas_delete_persisted(session_id)
        return {
            "success": True,
            "session_id": session_id,
            "name": data.get("name"),
            "status": "restored",
        }

    @app.post("/api/v1/canvas/sessions/{session_id}/close")
    async def close_canvas_session(session_id: str):
        """Explicitly close a session — persist to disk and remove from memory."""
        sess = _canvas_sessions.pop(session_id, None)
        if not sess:
            raise HTTPException(status_code=404, detail="Session not found")
        if sess.get("components"):
            _canvas_persist_to_disk(session_id, sess)
        # Close all WS connections
        for conn in list(sess.get("connections", set())):
            try:
                await conn.close()
            except Exception:
                pass
        return {"success": True, "session_id": session_id, "status": "closed"}

    @app.get("/api/v1/canvas")
    async def get_canvas_summary():
        """Return summary of all canvas sessions."""
        sessions = {}
        for sid, sess in _canvas_sessions.items():
            sessions[sid] = {
                "components": sess["components"],
                "connection_count": len(sess["connections"]),
                "name": sess.get("name"),
            }
        return {"sessions": sessions, "count": len(_canvas_sessions)}

    # ── Skills Panel API ──────────────────────────────────────────────────────

    from skill_manager import (
        apply_update,
        check_update,
        delete_origin,
        delete_skill,
        get_origin,
        get_skill,
        scan_agent_skills,
        scan_skills,
        set_origin,
    )

    @app.get("/api/v1/skills")
    async def list_skills(agent: Optional[str] = None):
        """Return installed skills, optionally scoped to an agent.

        If ``?agent=name`` is provided, returns only skills found under
        that agent's ``.github/skills/`` and ``.claude/skills/`` directories.
        Otherwise returns all globally installed skills.
        """
        if agent:
            agent_info = session_mgr.AGENTS.get(agent)
            if not agent_info:
                raise HTTPException(
                    status_code=404,
                    detail=f"Agent '{agent}' not found",
                )
            agent_path = agent_info.get("path", "")
            if not agent_path:
                return {"skills": [], "count": 0, "agent": agent}
            skills = scan_agent_skills(agent_path)
            return {"skills": skills, "count": len(skills), "agent": agent}
        skills = scan_skills()
        return {"skills": skills, "count": len(skills)}

    @app.get("/api/v1/skills/{skill_key:path}")
    async def get_skill_detail(skill_key: str):
        """Return detailed info for a single skill."""
        skill = get_skill(skill_key)
        if not skill:
            raise HTTPException(
                status_code=404, detail=f"Skill '{skill_key}' not found"
            )
        return skill

    @app.put("/api/v1/skills/{skill_key:path}/origin")
    async def update_skill_origin(skill_key: str, request: Request):
        """Set or update origin metadata for a skill."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        body = await request.json()
        # Validate required fields
        origin_type = body.get("origin_type", "")
        if origin_type not in ("git_repo", "website", "local", "unknown"):
            raise HTTPException(
                status_code=400,
                detail="origin_type must be one of: git_repo, website, local, unknown",
            )
        result = set_origin(skill_key, body)
        return {"success": True, "origin": result}

    @app.delete("/api/v1/skills/{skill_key:path}/origin")
    async def remove_skill_origin(skill_key: str, request: Request):
        """Remove origin metadata for a skill."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        if delete_origin(skill_key):
            return {"success": True}
        raise HTTPException(
            status_code=404, detail="No origin metadata found for this skill"
        )

    @app.delete("/api/v1/skills/{skill_key:path}")
    async def remove_skill(skill_key: str, request: Request):
        """Delete a skill from disk (symlink or directory)."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        result = delete_skill(skill_key)
        if not result["success"]:
            raise HTTPException(status_code=404, detail=result["message"])
        return result

    @app.post("/api/v1/skills/{skill_key:path}/check-update")
    async def check_skill_update(skill_key: str, request: Request):
        """Check if updates are available for a skill from its origin."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        result = check_update(skill_key)
        return result

    @app.post("/api/v1/skills/{skill_key:path}/update")
    async def trigger_skill_update(skill_key: str, request: Request):
        """Dispatch a background task to update a skill from its origin.

        This uses the orchestrator background task system so the update
        is visible in the WebUI Tasks tab.
        """
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )

        origin = get_origin(skill_key)
        if not origin:
            raise HTTPException(
                status_code=400, detail="No origin metadata — cannot update"
            )

        origin_type = origin.get("origin_type", "")
        if origin_type == "website":
            raise HTTPException(
                status_code=400,
                detail=f"Website-sourced skills must be updated manually. Visit: {origin.get('origin_url', '')}",
            )

        # Build a prompt for the background task agent
        skill = get_skill(skill_key)
        skill_name = skill["name"] if skill else skill_key
        skill_path = skill["path"] if skill else "unknown"

        prompt = (
            f"Update the skill '{skill_name}' (key: {skill_key}) from its git origin.\n"
            f"Local path: {skill_path}\n"
            f"Origin URL: {origin.get('origin_url', '')}\n"
            f"Origin path in repo: {origin.get('origin_path', '')}\n\n"
            f"Steps:\n"
            f"1. Run: python3 -c \"import sys; sys.path.insert(0, '/opt/n8n-copilot-shim-dev'); "
            f"from skill_manager import apply_update; import json; "
            f"r = apply_update('{skill_key}'); print(json.dumps(r, indent=2))\"\n"
            f"2. Report the result — files changed, any errors, and whether a backup was created.\n"
            f"3. If the update succeeded, confirm the skill is still valid."
        )

        # Dispatch as background task
        bg_body = {
            "prompt": prompt,
            "agent": "orchestrator",
            "runtime": "copilot",
            "model": "claude-haiku-4.5",
            "timeout": 600,
        }

        # Use internal background task creation
        channel = user.get("channel", "api")
        identity = user.get("identity", "")

        if bg_task_mgr:
            import threading
            from uuid import uuid4

            task_id = f"skill_update_{str(uuid4())[:8]}"
            session_id = f"skill_{str(uuid4())[:8]}"

            task_record = bg_task_mgr.create_task(
                task_id=task_id,
                session_id=session_id,
                user_identity=identity,
                channel=channel,
                agent="orchestrator",
                runtime="copilot",
                model="claude-haiku-4.5",
                prompt=prompt,
                timeout=600,
            )

            def _run_update():
                try:
                    result = apply_update(skill_key)
                    bg_task_mgr.complete_task(task_id, json.dumps(result))
                except Exception as e:
                    bg_task_mgr.fail_task(task_id, str(e))

            t = threading.Thread(target=_run_update, daemon=True)
            t.start()

            return {
                "success": True,
                "task_id": task_id,
                "message": f"Update task dispatched for '{skill_name}'",
            }
        else:
            # Fallback: run synchronously
            result = apply_update(skill_key)
            return {"success": result.get("success", False), "result": result}

    # ═══════════════════════════════════════════════════════════════════════════
    # ─── Secrets Manager API (F019) ──────────────────────────────────────────
    # ═══════════════════════════════════════════════════════════════════════════

    _SECRET_TOOL_PATH = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "secret_tool", "secret_tool.py"
    )

    @app.get("/api/v1/secrets")
    async def list_secrets(request: Request):
        """Return stored secret names (never values). Authenticated."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                _SECRET_TOOL_PATH,
                "list",
                "--backend",
                "pass",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                detail = (
                    stdout.decode().strip()
                    or stderr.decode().strip()
                    or "secret-tool list failed"
                )
                try:
                    err = json.loads(detail)
                    detail = err.get("message", detail)
                except Exception:
                    pass
                raise HTTPException(status_code=500, detail=detail)
            output = stdout.decode().strip()
            if not output:
                names = []
            else:
                try:
                    data = json.loads(output)
                    names = data if isinstance(data, list) else data.get("names", [])
                except json.JSONDecodeError:
                    names = [ln.strip() for ln in output.splitlines() if ln.strip()]
            return {"secrets": names, "count": len(names)}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/v1/secrets")
    async def store_secret(request: Request):
        """Store a secret (name + value). Authenticated. Never returns the value."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        body = await request.json()
        name = (body.get("name") or "").strip()
        value = body.get("value", "")
        if not name:
            raise HTTPException(status_code=400, detail="Secret name is required")
        if not value:
            raise HTTPException(status_code=400, detail="Secret value is required")
        # Validate name: alphanumeric, hyphens, underscores, dots only

        if not re.match(r"^[A-Za-z0-9._-]+$", name):
            raise HTTPException(
                status_code=400,
                detail="Secret name may only contain letters, digits, hyphens, underscores, and dots",
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                _SECRET_TOOL_PATH,
                "set",
                "--name",
                name,
                "--value-stdin",
                "--backend",
                "pass",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate(input=f"{value}\n".encode())
            if proc.returncode != 0:
                detail = (
                    stdout.decode().strip()
                    or stderr.decode().strip()
                    or "secret-tool set failed"
                )
                try:
                    err = json.loads(detail)
                    detail = err.get("message", detail)
                except Exception:
                    pass
                raise HTTPException(status_code=500, detail=detail)
            result = json.loads(stdout.decode())
            # Never return the value
            return {
                "status": result.get("status", "success"),
                "action": result.get("action", "created"),
                "name": name,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/v1/secrets/{name}")
    async def delete_secret(name: str, request: Request):
        """Delete a secret by name. Authenticated."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        if not re.match(r"^[A-Za-z0-9._-]+$", name):
            raise HTTPException(
                status_code=400,
                detail="Secret name may only contain letters, digits, hyphens, underscores, and dots",
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                _SECRET_TOOL_PATH,
                "delete",
                "--name",
                name,
                "--backend",
                "pass",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            output = stdout.decode().strip()
            if proc.returncode != 0:
                detail = (
                    output or stderr.decode().strip() or "secret-tool delete failed"
                )
                try:
                    err = json.loads(detail)
                    detail = err.get("message", detail)
                except Exception:
                    pass
                raise HTTPException(status_code=404, detail=detail)
            result = json.loads(output)
            return {
                "status": result.get("status", "success"),
                "action": result.get("action", "deleted"),
                "name": name,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Keyring Status & Unlock API (Issue #93) ---

    @app.get("/api/v1/secrets/keyring-status")
    async def keyring_status(request: Request):
        """Return the current keyring / secret-store lock status."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                _SECRET_TOOL_PATH,
                "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            output = stdout.decode().strip()
            if output:
                try:
                    return json.loads(output)
                except json.JSONDecodeError:
                    pass
            return {
                "status": "unavailable",
                "message": output or stderr.decode().strip() or "Unknown error",
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @app.post("/api/v1/secrets/keyring-unlock")
    async def keyring_unlock(request: Request):
        """Attempt to unlock the keyring with a user-supplied password.

        The password is passed to the secret_tool subprocess via stdin
        and is never logged or stored.
        """
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        body = await request.json()
        password = body.get("password", "")
        if not password:
            raise HTTPException(status_code=400, detail="password is required")
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                _SECRET_TOOL_PATH,
                "unlock",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate(input=f"{password}\n".encode())
            output = stdout.decode().strip()
            if proc.returncode == 0 and output:
                try:
                    result = json.loads(output)
                    return result
                except json.JSONDecodeError:
                    pass
                return {"status": "success"}
            detail = output or stderr.decode().strip() or "Unlock failed"
            try:
                err = json.loads(detail)
                detail = err.get("message", detail)
            except Exception:
                pass
            raise HTTPException(status_code=422, detail=detail)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Session Permissions API ---
    @app.patch("/api/v1/sessions/{session_id}/settings")
    async def update_session_settings(session_id: str, request: Request):
        """F027: Update session settings (e.g. silent_mode toggle)."""
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )

        data = session_mgr.load_session_data(session_id)
        if not data:
            raise HTTPException(status_code=404, detail="Session not found")

        body = await request.json()
        _allowed = {"silent_mode"}
        updated = {}
        for field in _allowed:
            if field in body:
                val = body[field]
                if field == "silent_mode" and not isinstance(val, bool):
                    raise HTTPException(
                        status_code=422,
                        detail="silent_mode must be boolean",
                    )
                session_mgr.update_session_field(session_id, field, val)
                updated[field] = val

        if not updated:
            raise HTTPException(
                status_code=422,
                detail="No valid settings fields provided",
            )

        return {"updated": updated, "session_id": session_id}

    @app.get("/api/v1/sessions/{session_id}/permissions")
    async def get_session_permissions(session_id: str, request: Request):
        """Return current session permissions (inherited from agent or overridden)."""
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        data = session_mgr.load_session_data(session_id)
        if not data:
            raise HTTPException(status_code=404, detail="Session not found")

        perms = data.get("permissions")
        agent_name = data.get("agent", get_default_agent())
        agent_cfg = session_mgr.AGENTS.get(agent_name, {})
        agent_default_perms = agent_cfg.get(
            "permissions",
            {
                "mode": "restricted",
                "directories": {"allow_read": [], "allow_write": [], "deny": []},
                "tools": {"allow": ["*"], "deny": []},
                "network": {"allow_urls": ["*"], "deny_urls": []},
                "mcp": {"allow": ["*"], "deny": []},
            },
        )

        return {
            "session_id": session_id,
            "permissions": perms or agent_default_perms,
            "agent_default_permissions": agent_default_perms,
            "is_overridden": perms is not None and perms != agent_default_perms,
        }

    @app.put("/api/v1/sessions/{session_id}/permissions")
    async def set_session_permissions(session_id: str, request: Request):
        """Override session-level permissions."""
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        data = session_mgr.load_session_data(session_id)
        if not data:
            raise HTTPException(status_code=404, detail="Session not found")

        body = await request.json()
        valid_modes = ("elevated", "restricted", "sandboxed")

        # Accept either a full permissions object or just a mode string
        if isinstance(body, dict) and "mode" in body:
            # If just mode is provided, build full permissions from agent default + new mode
            new_mode = body["mode"]
            if new_mode not in valid_modes:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid mode. Must be one of: {valid_modes}",
                )

            # Get current permissions and update mode
            current_perms = data.get("permissions") or {}
            if not current_perms:
                agent_name = data.get("agent", get_default_agent())
                agent_cfg = session_mgr.AGENTS.get(agent_name, {})
                current_perms = agent_cfg.get(
                    "permissions",
                    {
                        "mode": "restricted",
                        "directories": {
                            "allow_read": [],
                            "allow_write": [],
                            "deny": [],
                        },
                        "tools": {"allow": ["*"], "deny": []},
                        "network": {"allow_urls": ["*"], "deny_urls": []},
                        "mcp": {"allow": ["*"], "deny": []},
                    },
                )
            current_perms["mode"] = new_mode
            session_mgr.update_session_field(session_id, "permissions", current_perms)
            return {"updated": True, "permissions": current_perms}

        elif isinstance(body, dict) and "permissions" in body:
            # Full permissions object provided
            perms = body["permissions"]
            if not isinstance(perms, dict) or "mode" not in perms:
                raise HTTPException(
                    status_code=400,
                    detail="permissions must be an object with at least a 'mode' key",
                )
            if perms["mode"] not in valid_modes:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid mode. Must be one of: {valid_modes}",
                )
            session_mgr.update_session_field(session_id, "permissions", perms)
            return {"updated": True, "permissions": perms}

        else:
            raise HTTPException(
                status_code=400,
                detail="Request body must include 'mode' or 'permissions'",
            )

    @app.get("/api/v1/permissions/templates")
    async def get_permission_templates(request: Request):
        """Return available permission templates."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        return {
            "templates": [
                {
                    "mode": "elevated",
                    "label": "Full Access",
                    "description": "Agent has unrestricted access to all tools, directories, and network",
                    "icon": "⚡",
                },
                {
                    "mode": "restricted",
                    "label": "Restricted",
                    "description": "Agent uses curated tool and directory allowlists only",
                    "icon": "🔒",
                },
                {
                    "mode": "sandboxed",
                    "label": "Sandboxed",
                    "description": "Agent has no external access — fully isolated environment",
                    "icon": "🏖️",
                },
            ]
        }

    # --- .env File Editor API ---
    _env_file_path = Path(SCRIPT_BASE_DIR) / ".env"

    @app.get("/api/v1/settings/env")
    async def get_env_file(request: Request):
        """Return .env file contents for editing."""
        auth = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        try:
            content = _env_file_path.read_text() if _env_file_path.exists() else ""
            return {
                "content": content,
                "path": str(_env_file_path),
                "exists": _env_file_path.exists(),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read .env: {e}")

    @app.put("/api/v1/settings/env")
    async def put_env_file(request: Request):
        """Save updated .env file contents."""
        auth = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        body = await request.json()
        content = body.get("content", "")
        if not isinstance(content, str):
            raise HTTPException(status_code=400, detail="content must be a string")

        try:
            # Create backup before overwrite
            if _env_file_path.exists():
                backup_path = Path(str(_env_file_path) + ".bak")
                shutil.copy2(_env_file_path, backup_path)

            _env_file_path.write_text(content)
            return {
                "saved": True,
                "path": str(_env_file_path),
                "warning": "Changes require a service restart to take effect.",
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to save .env: {e}")

    @app.post("/api/v1/settings/restart-services")
    async def restart_services(request: Request):
        """Restart dev services (agent-manager-api-dev, etc.)."""
        auth = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        import subprocess

        services = [
            "agent-manager-api-dev.service",
            "task-scheduler-executor-dev.service",
            "webex-connector-dev.service",
            "telegram-bot-listener-dev.service",
        ]
        results = {}
        for svc in services:
            try:
                proc = subprocess.run(
                    ["systemctl", "restart", svc],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                results[svc] = (
                    "restarted"
                    if proc.returncode == 0
                    else f"failed: {proc.stderr.strip()}"
                )
            except Exception as e:
                results[svc] = f"error: {e}"
        return {
            "results": results,
            "note": "Services are restarting. The API will briefly disconnect.",
        }

    # --- Settings & Logs API ──────────────────────────────────────────────────

    _agents_json_path = Path(SCRIPT_BASE_DIR) / "agents.json"

    @app.get("/api/v1/agents-config")
    async def get_agents_config(request: Request):
        """Return current agents.json content."""
        auth = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        try:
            content = _agents_json_path.read_text()
            return JSONResponse(content=json.loads(content))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="agents.json not found")
        except json.JSONDecodeError as exc:
            return JSONResponse(
                status_code=200,
                content={
                    "_raw": _agents_json_path.read_text(),
                    "_parse_error": str(exc),
                },
            )

    @app.put("/api/v1/agents-config")
    async def put_agents_config(request: Request):
        """Save updated agents.json after validation."""
        auth = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        body = await request.body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")
        if not isinstance(data, dict) or "agents" not in data:
            raise HTTPException(
                status_code=400, detail="Payload must have an 'agents' key"
            )
        if not isinstance(data["agents"], list):
            raise HTTPException(status_code=400, detail="'agents' must be an array")
        for idx, ag in enumerate(data["agents"]):
            if not isinstance(ag, dict):
                raise HTTPException(
                    status_code=400, detail=f"agents[{idx}] must be an object"
                )
            if "name" not in ag or "path" not in ag:
                raise HTTPException(
                    status_code=400, detail=f"agents[{idx}] requires 'name' and 'path'"
                )
            mc = ag.get("max_concurrent")
            if mc is not None:
                if not isinstance(mc, int) or isinstance(mc, bool) or mc < 1:
                    raise HTTPException(
                        status_code=400,
                        detail=f"agents[{idx}].max_concurrent must be an integer >= 1",
                    )
        # Backup existing file
        backup = _agents_json_path.with_suffix(".json.bak")
        if _agents_json_path.exists():
            shutil.copy2(str(_agents_json_path), str(backup))
        _agents_json_path.write_text(json.dumps(data, indent=2) + "\n")
        print(
            f"[API] agents.json updated by {auth.get('identity', 'unknown')}",
            file=sys.stderr,
        )
        # Auto-reload in-memory agent config after writing to disk
        ok, msg = session_mgr.reload_agents_from_disk()
        if ok:
            print(f"[API] Auto-reloaded agents after save — {msg}", file=sys.stderr)
        else:
            print(
                f"[API] Warning: saved to disk but reload failed: {msg}",
                file=sys.stderr,
            )
        return {
            "status": "saved",
            "agent_count": len(data["agents"]),
            "reloaded": ok,
            "reload_message": msg,
        }

    @app.post("/api/v1/reload-agents")
    async def reload_agents_config(request: Request):
        """Hot-reload the in-memory agents cache from agents.json on disk."""
        auth = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        ok, msg = session_mgr.reload_agents_from_disk()
        if ok:
            count = len(session_mgr.AGENTS)
            print(
                f"[API] agents.json hot-reloaded by {auth.get('identity', 'unknown')} — {count} agents",
                file=sys.stderr,
            )
            return {"status": "reloaded", "message": msg}
        else:
            print(
                f"[API] Hot-reload failed ({auth.get('identity', 'unknown')}): {msg}",
                file=sys.stderr,
            )
            raise HTTPException(status_code=500, detail=f"Reload failed: {msg}")

    @app.get("/api/v1/logs")
    async def get_logs(
        request: Request,
        service: str = Query("agent-manager-api-dev"),
        lines: int = Query(200, ge=1, le=5000),
        search: str = Query(""),
        since: str = Query(""),
    ):
        """Fetch recent journalctl logs for a systemd service."""
        auth = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        allowed_services = {
            "agent-manager-api-dev",
            "telegram-bot-listener-dev",
            "webex-connector-dev",
            "task-scheduler-executor-dev",
        }
        if service not in allowed_services:
            raise HTTPException(
                status_code=400,
                detail=f"Service not allowed. Choose from: {', '.join(sorted(allowed_services))}",
            )
        cmd = [
            "journalctl",
            "-u",
            service,
            "--no-pager",
            "-n",
            str(lines),
            "-o",
            "short-iso",
        ]
        if since:
            cmd += ["--since", since]
        if search:
            cmd += ["--grep", search]
        try:
            proc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=15),
            )
            raw = proc.stdout or ""
            return {
                "lines": raw.strip().split("\n") if raw.strip() else [],
                "service": service,
            }
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="journalctl timed out")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/v1/logs/stream")
    async def stream_logs(
        service: str = Query("agent-manager-api-dev"),
        token: str = Query(""),
        request: Request = None,
    ):
        """SSE endpoint streaming live journalctl -f output."""
        # Auth via query param for EventSource (can't set headers)
        if not token:
            raise HTTPException(status_code=401, detail="Missing token query parameter")
        if token.startswith("shared_"):
            if not auth_mgr.validate_shared_key(token):
                raise HTTPException(status_code=401, detail="Invalid shared key")
        elif token.startswith("session_"):
            if not auth_mgr.validate_session_token(token):
                raise HTTPException(
                    status_code=401, detail="Invalid or expired session token"
                )
        else:
            raise HTTPException(status_code=401, detail="Unrecognized token type")

        allowed_services = {
            "agent-manager-api-dev",
            "telegram-bot-listener-dev",
            "webex-connector-dev",
            "task-scheduler-executor-dev",
        }
        if service not in allowed_services:
            raise HTTPException(status_code=400, detail=f"Service not allowed")

        async def _event_generator():
            proc = await asyncio.create_subprocess_exec(
                "journalctl",
                "-u",
                service,
                "-f",
                "--no-pager",
                "-o",
                "short-iso",
                "-n",
                "0",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        line = await asyncio.wait_for(
                            proc.stdout.readline(), timeout=30
                        )
                        if line:
                            text = line.decode("utf-8", errors="replace").rstrip()
                            yield f"data: {json.dumps({'line': text})}\n\n"
                        else:
                            break
                    except asyncio.TimeoutError:
                        yield f": keepalive\n\n"
            finally:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except asyncio.TimeoutError:
                    proc.kill()

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- Text-to-Speech ───────────────────────────────────────────────────────
    _tts_cache_dir = Path("/tmp/webui_tts_cache")
    _tts_cache_dir.mkdir(parents=True, exist_ok=True)

    # TTS character limit — edge-tts handles long texts fine, but we cap at
    # a reasonable length to avoid abuse and very long synthesis times.
    _TTS_MAX_CHARS = 10_000
    _TTS_VOICE = "en-US-AriaNeural"  # default voice

    @app.post("/api/v1/tts")
    async def text_to_speech(
        request: Request,
        authorization: Optional[str] = Header(None),
        x_user_identity: Optional[str] = Header(None),
        x_auth_channel: Optional[str] = Header(None),
    ):
        """Generate speech audio from text using edge-tts.

        Request body: { "text": "...", "voice": "en-US-AriaNeural" (optional) }
        Returns: audio/mpeg stream
        """
        auth = await authenticate(
            request,
            authorization=authorization,
            x_user_identity=x_user_identity,
            x_auth_channel=x_auth_channel,
        )

        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="Text is required")

        if len(text) > _TTS_MAX_CHARS:
            text = text[:_TTS_MAX_CHARS]

        voice = body.get("voice") or _TTS_VOICE

        # Strip markdown formatting for cleaner speech
        clean = text
        # Remove code blocks
        clean = re.sub(r"```[\s\S]*?```", " code block omitted ", clean)
        clean = re.sub(r"`[^`]+`", "", clean)
        # Remove markdown links — keep link text
        clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)
        # Remove markdown emphasis markers
        clean = re.sub(r"[*_]{1,3}", "", clean)
        # Remove headings markers
        clean = re.sub(r"^#{1,6}\s*", "", clean, flags=re.MULTILINE)
        # Remove horizontal rules
        clean = re.sub(r"^[-*_]{3,}\s*$", "", clean, flags=re.MULTILINE)
        # Remove HTML tags
        clean = re.sub(r"<[^>]+>", "", clean)
        # Collapse whitespace
        clean = re.sub(r"\n{3,}", "\n\n", clean)
        clean = clean.strip()

        if not clean:
            raise HTTPException(
                status_code=400, detail="Text has no speakable content after cleanup"
            )

        # Cache key based on text + voice
        cache_key = hashlib.sha256(f"{voice}:{clean}".encode()).hexdigest()
        cache_path = _tts_cache_dir / f"{cache_key}.mp3"

        if not cache_path.exists():
            try:
                import edge_tts

                communicate = edge_tts.Communicate(clean, voice)
                await communicate.save(str(cache_path))
            except Exception as exc:
                # Clean up partial file
                cache_path.unlink(missing_ok=True)
                logging.error(
                    "[TTS] edge-tts failed for %s: %s",
                    auth.get("identity", "?"),
                    exc,
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"TTS generation failed: {str(exc)[:200]}",
                )

        return FileResponse(
            str(cache_path),
            media_type="audio/mpeg",
            filename="speech.mp3",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/api/v1/tts/voices")
    async def list_tts_voices(
        request: Request,
        authorization: Optional[str] = Header(None),
        x_user_identity: Optional[str] = Header(None),
        x_auth_channel: Optional[str] = Header(None),
    ):
        """List available TTS voices."""
        _ = await authenticate(
            request,
            authorization=authorization,
            x_user_identity=x_user_identity,
            x_auth_channel=x_auth_channel,
        )
        try:
            import edge_tts

            voices = await edge_tts.list_voices()
            # Return a simplified list — just English voices by default
            en_voices = [
                {"name": v["ShortName"], "gender": v["Gender"], "locale": v["Locale"]}
                for v in voices
                if v["Locale"].startswith("en-")
            ]
            return {"voices": en_voices, "default": _TTS_VOICE}
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Failed to list voices: {str(exc)[:200]}"
            )

    # --- Per-Agent Memory ─────────────────────────────────────────────────

    class DailyNoteRequest(BaseModel):
        content: str
        agent: Optional[str] = None

        @field_validator("content")
        @classmethod
        def validate_content(cls, v):
            if not v or not v.strip():
                raise ValueError("Content must not be empty")
            if len(v) > 10000:
                raise ValueError("Content must be 10,000 characters or less")
            return v

    @app.post("/api/v1/memory/daily")
    async def append_daily_note_api(body: DailyNoteRequest, request: Request):
        """Append a timestamped entry to an agent's daily notes."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        from memory.daily import append_daily_note as _append_note

        agent_path = None
        if body.agent:
            agent_cfg = session_mgr.AGENTS.get(body.agent)
            if not agent_cfg:
                raise HTTPException(
                    status_code=404,
                    detail=f"Unknown agent: {body.agent}",
                )
            agent_path = agent_cfg.get("path", "")
        else:
            orch = session_mgr.AGENTS.get("orchestrator", {})
            agent_path = orch.get("path", "")

        note_file = _append_note(body.content, agent_path=agent_path)
        return {
            "status": "ok",
            "file": str(note_file),
            "agent": body.agent or "orchestrator",
        }

    # --- Memory Promotion ─────────────────────────────────────────────────────

    MEMORY_PROMOTER_SCRIPT = Path(
        "/opt/foster-skills/memory-promoter/memory_promoter.py"
    )

    class PromoteMemoryRequest(BaseModel):
        agent: Optional[str] = None

    @app.post("/api/v1/memory/promote")
    async def promote_memory(body: PromoteMemoryRequest, request: Request):
        """Trigger memory promotion for a single agent (or orchestrator).

        Spawns memory_promoter.py with the appropriate WEE_AGENT_DIR env var.
        When agent is omitted, promotes orchestrator memory (/opt/memories/).
        When agent is provided, looks up the agent path from agents.json.
        """
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )

        agent_name = body.agent or "orchestrator"
        if body.agent:
            agent_cfg = session_mgr.AGENTS.get(body.agent)
            if not agent_cfg:
                raise HTTPException(
                    status_code=404,
                    detail=f"Unknown agent: {body.agent}",
                )
            agent_path = agent_cfg.get("path", "")
        else:
            orch = session_mgr.AGENTS.get("orchestrator", {})
            agent_path = orch.get("path", "")

        if not MEMORY_PROMOTER_SCRIPT.exists():
            raise HTTPException(
                status_code=503,
                detail=f"Memory promoter script not found: {MEMORY_PROMOTER_SCRIPT}",
            )

        env = os.environ.copy()
        if agent_path:
            env["WEE_AGENT_DIR"] = agent_path

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    [sys.executable, str(MEMORY_PROMOTER_SCRIPT)],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=env,
                ),
            )
            return {
                "status": "ok",
                "agent": agent_name,
                "agent_path": agent_path,
                "stdout": result.stdout[-2000:] if result.stdout else "",
                "stderr": result.stderr[-1000:] if result.stderr else "",
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            raise HTTPException(
                status_code=504,
                detail=f"Memory promotion timed out for agent: {agent_name}",
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Memory promotion failed for {agent_name}: {exc}",
            )

    @app.post("/api/v1/memory/promote-all")
    async def promote_all_agents_memory(request: Request):
        """Trigger memory promotion for ALL agents defined in agents.json.

        Iterates over every agent in the config and runs memory_promoter.py
        for each one, including the orchestrator.
        """
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )

        if not MEMORY_PROMOTER_SCRIPT.exists():
            raise HTTPException(
                status_code=503,
                detail=f"Memory promoter script not found: {MEMORY_PROMOTER_SCRIPT}",
            )

        agents = session_mgr.AGENTS
        if not agents:
            return {"status": "ok", "results": [], "message": "No agents configured"}

        results = []
        for agent_name, agent_cfg in agents.items():
            agent_path = agent_cfg.get("path", "")
            env = os.environ.copy()
            if agent_path:
                env["WEE_AGENT_DIR"] = agent_path

            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda env=env: subprocess.run(
                        [sys.executable, str(MEMORY_PROMOTER_SCRIPT)],
                        capture_output=True,
                        text=True,
                        timeout=120,
                        env=env,
                    ),
                )
                results.append(
                    {
                        "agent": agent_name,
                        "agent_path": agent_path,
                        "status": "ok" if result.returncode == 0 else "error",
                        "returncode": result.returncode,
                        "stdout": result.stdout[-500:] if result.stdout else "",
                        "stderr": result.stderr[-500:] if result.stderr else "",
                    }
                )
            except subprocess.TimeoutExpired:
                results.append(
                    {
                        "agent": agent_name,
                        "agent_path": agent_path,
                        "status": "timeout",
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "agent": agent_name,
                        "agent_path": agent_path,
                        "status": "error",
                        "error": str(exc),
                    }
                )

        ok_count = sum(1 for r in results if r["status"] == "ok")
        return {
            "status": "ok",
            "total": len(results),
            "succeeded": ok_count,
            "failed": len(results) - ok_count,
            "results": results,
        }

    # ── Themes API (F025) ─────────────────────────────────────────────

    @app.get("/api/v1/themes")
    async def list_themes(request: Request):
        """List built-in and custom themes."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        themes = list(_BUILTIN_THEMES)
        if _themes_dir.exists():
            for css_file in sorted(_themes_dir.glob("*.css")):
                name = css_file.stem
                if name == "custom" or name.startswith("."):
                    continue
                if any(t["name"] == name for t in themes):
                    continue
                themes.append(
                    {
                        "name": name,
                        "label": name.replace("-", " ").replace("_", " ").title(),
                        "description": "Custom theme",
                        "builtin": False,
                        "css": css_file.read_text(encoding="utf-8"),
                    }
                )
        return {"themes": themes, "count": len(themes)}

        # --- AI Media ─────────────────────────────────────────────────────────────

    _ai_media_dir = Path("/tmp/webui_ai_media")
    _ai_media_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/ai-media", StaticFiles(directory=str(_ai_media_dir)), name="ai_media")

    # --- Static WebUI — MUST BE LAST ---
    _webui_dist = Path(__file__).parent / "webui" / "dist"
    if _webui_dist.exists():
        app.mount(
            "/ui", StaticFiles(directory=str(_webui_dist), html=True), name="webui"
        )

    _static_dir = Path(__file__).parent / "static"
    if _static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

    return app


def start_api_server():
    """Load dotenv, create the FastAPI app, and run uvicorn."""
    try:
        from dotenv import load_dotenv

        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        load_dotenv(env_path)
    except ImportError:
        pass

    import uvicorn

    app = create_api_app()
    port = int(
        os.environ.get("API_PORT", "8001")
    )  # DEV: default 8001 to avoid collision with prod
    host = os.environ.get("API_HOST", "127.0.0.1")

    # SSL support — set SSL_CERTFILE and SSL_KEYFILE env vars to enable HTTPS
    # In development (APP_ENV=DEV) prefer HTTP even if cert files exist to avoid surprising TLS-only bindings.
    ssl_certfile = os.environ.get("SSL_CERTFILE")
    ssl_keyfile = os.environ.get("SSL_KEYFILE")
    ssl_kwargs = {}
    proto = "http"
    if (
        ssl_certfile
        and ssl_keyfile
        and os.path.isfile(ssl_certfile)
        and os.path.isfile(ssl_keyfile)
    ):
        # Only enable HTTPS when not in DEV, unless FORCE_SSL is explicitly set.
        app_env = os.environ.get("APP_ENV", "").upper()
        force_ssl = os.environ.get("FORCE_SSL", "") in ("1", "true", "True", "TRUE")
        if app_env != "DEV" or force_ssl:
            ssl_kwargs = {"ssl_certfile": ssl_certfile, "ssl_keyfile": ssl_keyfile}
            proto = "https"
        else:
            print(
                "[API] SSL cert/key found but APP_ENV=DEV — serving HTTP for development. Set FORCE_SSL=1 to force HTTPS.",
                file=sys.stderr,
            )

    # Support comma-separated hosts (e.g. "127.0.0.1,100.x.x.x" for Tailscale + localhost).
    # When multiple hosts are specified, run each in a background thread and block on the last.
    hosts = [h.strip() for h in host.split(",") if h.strip()]
    if len(hosts) > 1:
        import threading

        threads = []
        for h in hosts[:-1]:
            print(f"[API] Listening on {proto}://{h}:{port}", file=sys.stderr)
            t = threading.Thread(
                target=uvicorn.run,
                kwargs={"app": app, "host": h, "port": port, **ssl_kwargs},
                daemon=True,
            )
            t.start()
            threads.append(t)
        print(f"[API] Listening on {proto}://{hosts[-1]}:{port}", file=sys.stderr)
        uvicorn.run(app, host=hosts[-1], port=port, **ssl_kwargs)
    else:
        print(f"[API] Listening on {proto}://{host}:{port}", file=sys.stderr)
        uvicorn.run(app, host=host, port=port, **ssl_kwargs)


def main():
    parser = argparse.ArgumentParser(
        description="AI Session Wrapper for N8N Integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Execute a prompt with default settings
  %(prog)s "What is the status of the cluster?"
  
  # Set agent via CLI
  %(prog)s --agent devops "Check server status"
  
  # Set model and runtime via CLI
  %(prog)s --runtime gemini --model gemini-1.5-pro "Analyze this code"
  
  # Use custom configuration file
  %(prog)s --config my-agents.json "What can you do?"
  
  # List available agents
  %(prog)s --list-agents
  
  # List available agents with custom config
  %(prog)s --list-agents --config my-agents.json
  
  # List available models for current runtime
  %(prog)s --list-models
  
  # List available runtimes
  %(prog)s --list-runtimes
  
  # Combine multiple options
  %(prog)s --agent family --runtime claude --model sonnet "Find recipes"
  
  # Backwards compatible: positional arguments
  %(prog)s "What's the weather?" my_session my-config.json
""",
    )

    # Positional arguments (for backwards compatibility)
    parser.add_argument(
        "prompt",
        nargs="?",
        help="The prompt to execute (required unless using --list-* options)",
    )
    parser.add_argument(
        "session_id",
        nargs="?",
        default="default",
        help="N8N session ID (default: 'default')",
    )

    # Configuration file - can be positional or named
    parser.add_argument(
        "config_file_positional",
        nargs="?",
        help=argparse.SUPPRESS,  # Hide from help as we have --config below
    )
    parser.add_argument(
        "-c",
        "--config",
        dest="config_file",
        help="Path to agents.json configuration file",
    )

    # Agent options
    agent_group = parser.add_argument_group("agent options")
    agent_group.add_argument(
        "--agent",
        metavar="NAME",
        help="Set the agent to use (e.g., devops, family, projects)",
    )
    agent_group.add_argument(
        "--list-agents", action="store_true", help="List all available agents and exit"
    )

    # Model options
    model_group = parser.add_argument_group("model options")
    model_group.add_argument(
        "--model", metavar="NAME", help="Set the model to use (e.g., gpt-5, sonnet)"
    )
    model_group.add_argument(
        "--list-models",
        action="store_true",
        help="List all available models for current runtime and exit",
    )

    # Runtime options
    runtime_group = parser.add_argument_group("runtime options")
    runtime_group.add_argument(
        "--runtime",
        metavar="NAME",
        choices=[
            "copilot",
            "copilot-sdk",
            "opencode",
            "claude",
            "claude-sdk",
            "gemini",
            "codex",
            "devin",
            "cursor",
        ],
        help="Set the runtime to use (choices: copilot, copilot-sdk, opencode, claude, claude-sdk, gemini, codex, devin, cursor)",
    )
    runtime_group.add_argument(
        "--list-runtimes",
        action="store_true",
        help="List all available runtimes and exit",
    )

    # Claude mode options
    claude_group = parser.add_argument_group("claude options")
    claude_group.add_argument(
        "--mode",
        metavar="MODE",
        choices=["elevated", "restricted", "sandboxed"],
        help="Set permission mode: elevated (auto-approve), restricted (default), or sandboxed (read-only)",
    )

    args = parser.parse_args()

    # Handle backwards compatibility: if config_file_positional is provided, use it
    if args.config_file_positional and not args.config_file:
        args.config_file = args.config_file_positional

    # Initialize manager
    manager = SessionManager(
        args.config_file, app_env=os.environ.get("APP_ENV", "PROD").upper()
    )

    # Apply runtime setting first if provided (so list commands use the correct runtime)
    if args.runtime:
        result = manager.execute(f"/runtime set {args.runtime}", args.session_id)
        _check_command_result(result, ["Unknown runtime", "Error"])

    # Apply agent setting if provided (so list commands use the correct agent context)
    if args.agent:
        result = manager.execute(f'/agent set "{args.agent}"', args.session_id)
        _check_command_result(result, ["Unknown agent", "Error"])

    # Handle list commands (these don't require a prompt but may use runtime/agent settings)
    if args.list_agents:
        output = manager.execute("/agent list", args.session_id)
        print(output)
        sys.exit(0)

    if args.list_models:
        output = manager.execute("/model list", args.session_id)
        print(output)
        sys.exit(0)

    if args.list_runtimes:
        output = manager.execute("/runtime list", args.session_id)
        print(output)
        sys.exit(0)

    # If no prompt provided and no list command, show error
    if not args.prompt:
        parser.error("prompt is required unless using --list-* options")

    # Apply model setting if provided (after list commands since we don't need it for lists)
    if args.model:
        result = manager.execute(f'/model set "{args.model}"', args.session_id)
        _check_command_result(result, ["Unknown model", "Error"])

    # Set mode if provided (will be used by run_claude to set --permission-mode)
    if args.mode:
        manager.mode = args.mode

    # Execute the main prompt
    output = manager.execute(args.prompt, args.session_id)
    print(output)


if __name__ == "__main__":
    if "--api" in sys.argv:
        start_api_server()
    else:
        main()
