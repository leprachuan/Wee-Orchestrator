#!/usr/bin/env python3
"""
Unified AI Session Wrapper for N8N Integration
Wraps GitHub Copilot CLI and OpenCode CLI
Manages session ID mapping between N8N chat sessions and AI backend sessions
"""

import sys
import os
import json
import subprocess
import re
import signal
import time
import argparse
import shutil
import logging
from pathlib import Path
from uuid import uuid4
from typing import Optional, Tuple, Dict, List
import secrets as _secrets
import threading


# Dynamically determine the repo base directory (works regardless of where repo is cloned)
SCRIPT_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


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
        self.sessions_file = sessions_file or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".task-scheduler", "sessions.json")
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
                            "absolute_expires_at", created_at + self.session_token_absolute_ttl
                        )
                        entry["absolute_expires_at"] = absolute_expires_at
                        if entry.get("expires_at", 0) > now and absolute_expires_at > now:
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

    def create_task(self, task_id: str, session_id: str, user_identity: str,
                    channel: str, agent: str, runtime: str, model: str,
                    prompt: str, pid: int = 0, status: str = "running",
                    timeout: int = None, notify: bool = True) -> dict:
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
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if status == "running" else None,
            "completed_at": None,
            "output_lines": [],
            "final_response": None,
            "error": None,
            "timeout": timeout,
            "notify": notify,
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
        """Check if a task belongs to this user, matching across channels.
        Uses user_identity (raw identity without channel prefix) for cross-channel
        visibility so tasks created from the webui channel are visible to users
        authenticated via webex/telegram and vice versa.
        Falls back to user_key comparison for backward compatibility.
        """
        stored_identity = task.get("user_identity")
        if stored_identity is not None:
            return stored_identity == identity
        # Fallback: check user_key with any channel prefix
        return task.get("user_key") == self._user_key(channel, identity)

    def list_tasks(self, channel: str, identity: str) -> list:
        with self._lock:
            return [t for t in self._load() if self._identity_matches(t, channel, identity)]

    def count_running(self, channel: str, identity: str) -> int:
        return sum(1 for t in self.list_tasks(channel, identity) if t["status"] == "running")

    def count_queued(self, channel: str, identity: str) -> int:
        return sum(1 for t in self.list_tasks(channel, identity) if t["status"] == "queued")

    def get_next_queued(self, channel: str, identity: str) -> Optional[dict]:
        """Return the oldest queued task for this user, or None."""
        queued = [t for t in self.list_tasks(channel, identity) if t["status"] == "queued"]
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
                        t["output_lines"] = t["output_lines"][-self.MAX_OUTPUT_LINES:]
                    break
            self._save(tasks)

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
        Path("/usr/local/bin") / name,      # Homebrew Intel / manual installs
        Path("/usr/bin") / name,            # Standard system location
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
        "backup", "full scan", "batch process", "process all", "index all",
        "crawl all", "generate report for all", "migrate all", "import all",
        "export all", "sync all", "reindex",
    ]
    # Long tasks (~20 min)
    long_keywords = [
        "deploy", "migrate", "migration", "build", "compile", "install",
        "process", "analyze all", "scan", "crawl", "index", "reprocess",
        "refactor", "generate report", "summarize all", "bulk",
    ]
    # Quick tasks (~2 min)
    quick_keywords = [
        "status", "check", "list", "show", "get", "fetch", "ping",
        "is running", "are running", "what is", "what are", "tell me",
        "how many", "count", "current", "latest", "recent",
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

    def __init__(self):
        home = os.path.expanduser("~")
        copilot_dir = os.path.join(home, ".copilot")
        os.makedirs(copilot_dir, exist_ok=True)
        self._path = os.path.join(copilot_dir, "chat-history.json")
        self._lock = threading.Lock()
        # Read MAX_SESSIONS from environment or use default
        try:
            self.MAX_SESSIONS_PER_USER = int(os.environ.get("MAX_SESSIONS", "100"))
        except (ValueError, TypeError):
            self.MAX_SESSIONS_PER_USER = 100
        self.MAX_MESSAGES_PER_SESSION = 500
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
            for s in sorted(sessions, key=lambda x: x.get("updated_at", 0), reverse=True):
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
                sessions = sessions[-(self.MAX_SESSIONS_PER_USER - 1):]
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
                    # Auto-set preview from first assistant message
                    if role == "assistant" and not s.get("preview"):
                        s["preview"] = content[:120]
                    # Prune if too many messages
                    if len(messages) > self.MAX_MESSAGES_PER_SESSION:
                        s["messages"] = messages[-self.MAX_MESSAGES_PER_SESSION:]
                    s["updated_at"] = time.time()
                    self._save(data)
                    return True
        return False

    def rename_session(self, channel: str, identity: str, session_id: str, title: str) -> bool:
        """Rename a session. Returns False if not found."""
        with self._lock:
            data = self._load()
            key = self._user_key(channel, identity)
            for s in data.get(key, {}).get("sessions", []):
                if s["session_id"] == session_id:
                    s["title"] = title[:120]
                    s["updated_at"] = time.time()
                    self._save(data)
                    return True
        return False

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
        "free": 50, "pro": 300, "pro+": 1500,
        "business": 300, "enterprise": 1000,
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
                capture_output=True, text=True, timeout=10,
            )
            gh_user = user_raw.stdout.strip()
            if not gh_user:
                return {}

            billing_raw = subprocess.run(
                [gh_bin, "api", f"/users/{gh_user}/settings/billing/usage"],
                capture_output=True, text=True, timeout=15,
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
            print(f"[RuntimeUsage] Copilot billing fetch failed: {exc}", file=sys.stderr)
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
                ["claude-sonnet", "claude-sonnet-4-6", "claude-sonnet-4.6", "claude-sonnet-4.5", "sonnet-4.6"],
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
            ("claude-3-5-sonnet-latest", "Claude 3.5 Sonnet (V2)", ["claude-3-5-sonnet-20241022"]),
            ("claude-3-5-haiku-latest", "Claude 3.5 Haiku", ["claude-3-5-haiku-20241022"]),
            ("claude-3-opus-latest", "Claude 3 Opus", ["claude-3-opus-20240229"]),
        ]
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
        ]
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
        ]
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
        ]
    }

    # DEVIN models configuration
    DEVIN_MODELS = {
        "Anthropic Models": [
            ("claude-sonnet-4", "Claude Sonnet 4", ["sonnet-4", "sonnet"]),
            ("claude-sonnet-4.5", "Claude Sonnet 4.5", ["sonnet-4.5"]),
            ("claude-sonnet-4.5-thinking", "Claude Sonnet 4.5 Thinking", ["sonnet-thinking"]),
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
        self.running_queries_file = self.copilot_home / f"running-queries{_env_suffix}.json"

        # OpenCode Paths
        self.opencode_home = Path.home() / ".opencode"
        # Resolve OpenCode executable like other runtimes; keep legacy path as fallback.
        self.opencode_bin = Path(
            find_executable("opencode")
            or str(self.opencode_home / "bin" / "opencode")
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

        # CLI mode setting for claude (yolo or restricted)
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

        # Load agents from config file
        self.AGENTS = self._load_agents_config(config_file)

        # Load skill repositories from configuration
        self.skill_repositories = self._load_skill_repositories()

        # Cache env-loaded model configurations for runtime description lookup
        self._env_claude_models = None
        self._env_gemini_models = None
        self._env_codex_models = None
        self._env_devin_models = None

        # Load command timeout from environment
        self.command_timeout = get_command_timeout()

        # Session idle timeout — sessions inactive longer than this are
        # candidates for cleanup.  Defaults to 30 min; override via env var.
        self.session_idle_timeout = int(
            os.environ.get("SESSION_IDLE_TIMEOUT", "1800")
        )

        # Lock for session map file read-modify-write to prevent TOCTOU races
        self._session_map_lock = threading.Lock()

        # Per-session streaming queues: session_id -> (asyncio.Queue, event_loop)
        # Populated by the /stream API endpoint; read by _execute_subprocess_with_tracking.
        self._stream_queues: Dict[str, tuple] = {}

        # Last subprocess exit code per n8n_session_id (for debugging/monitoring)
        self._last_exit_codes: Dict[str, int] = {}



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

        if not config_path.exists():
            print(
                f"[Warning] Agents config file not found at {config_path}. Using empty agents.",
                file=sys.stderr,
            )
            return {}

        try:
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
                    }
                return agents
        except json.JSONDecodeError as e:
            print(f"[Error] Failed to parse agents config: {e}", file=sys.stderr)
            return {}
        except Exception as e:
            print(f"[Error] Failed to load agents config: {e}", file=sys.stderr)
            return {}

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
                            repo for repo in config.get("repositories", [])
                            if repo.get("enabled", False)
                        ]
                        if repositories:
                            print(
                                f"[Info] Loaded {len(repositories)} skill repositories from {config_path}",
                                file=sys.stderr
                            )
                            return repositories
                except Exception as e:
                    print(
                        f"[Warning] Failed to load skill repositories from {config_path}: {e}",
                        file=sys.stderr
                    )
                    continue

        # Return default Anthropic repository if no config found
        print(
            "[Info] No skill_repositories.json found, using default Anthropic repository",
            file=sys.stderr
        )
        return [{
            "name": "Anthropic Official",
            "url": "https://github.com/anthropics/skills.git",
            "description": "Official Anthropic skills repository",
            "enabled": True
        }]

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

    def fetch_copilot_models(self) -> Dict:
        """Fetch available models from copilot CLI help text"""
        if not self.copilot_bin:
            print("Copilot executable not found in any search paths", file=sys.stderr)
            return {}

        try:
            # Use --no-color to ensure clean text
            cmd = [self.copilot_bin, "--help", "--no-color"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"Copilot help command failed: {result.stderr}", file=sys.stderr)
                return {}

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
                    "gpt-5.3-codex",
                    "gpt-5.2-codex",
                    "gpt-5.2",
                    "gpt-5.1-codex-max",
                    "gpt-5.1-codex",
                    "gpt-5.1",
                    "gpt-5.1-codex-mini",
                    "gpt-5-mini",
                    "gpt-4.1"
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
                return {}

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
            return {}

    def fetch_opencode_models(self) -> Dict:
        """Fetch available models from opencode CLI, falling back to static list on failure."""
        try:
            cmd = [str(self.opencode_bin), "models"]
            # Use configured command timeout (may be set via COMMAND_TIMEOUT)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.command_timeout)

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
                    provider, model = parts
                else:
                    provider = "other"
                    model = line

                if provider not in models_by_provider:
                    models_by_provider[provider] = []
                models_by_provider[provider].append(line)

            return models_by_provider
        except subprocess.TimeoutExpired:
            print(
                f"[Error] opencode models command timed out after {self.command_timeout}s", file=sys.stderr
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

    def _get_model_description(self, model_id: str, runtime: str) -> Optional[str]:
        """Look up a human-readable description for a model from static metadata."""
        # First check env-loaded models (if cached)
        env_models_map = {
            "claude": self._env_claude_models,
            "gemini": self._env_gemini_models,
            "codex": self._env_codex_models,
            "devin": self._env_devin_models,
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
            "gemini": self.GEMINI_MODELS,
            "codex": self.CODEX_MODELS,
            "opencode": self.OPENCODE_MODELS,
            "devin": self.DEVIN_MODELS,
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
                print(f"Warning: Failed to parse CLAUDE_MODELS_JSON: {e}", file=sys.stderr)
        
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
                print(f"Warning: Failed to parse GEMINI_MODELS_JSON: {e}", file=sys.stderr)
        
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
                print(f"Warning: Failed to parse CODEX_MODELS_JSON: {e}", file=sys.stderr)
        
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
                            "Available Models": [
                                (mid, mid, []) for mid in model_ids
                            ]
                        }
                        print(
                            f"[devin] Auto-discovered {len(model_ids)} models",
                            file=sys.stderr,
                        )
                        return self._static_models_to_dict(discovered)
        except Exception as e:
            print(f"[devin] Model discovery failed, using static list: {e}", file=sys.stderr)

        return self._static_models_to_dict(self.DEVIN_MODELS)

    def get_models_for_runtime(self, runtime: str) -> Dict:
        """Fetch available models for a runtime, using CLI discovery where possible.

        Returns {category: [model_id, ...]} with CLI-discovered models preferred
        and a static list used as fallback when the runtime CLI is unavailable
        or does not support model listing.
        """
        dispatch = {
            "copilot": self.fetch_copilot_models,
            "opencode": self.fetch_opencode_models,
            "claude": self.fetch_claude_models,
            "gemini": self.fetch_gemini_models,
            "codex": self.fetch_codex_models,
            "devin": self.fetch_devin_models,
        }
        fetcher = dispatch.get(runtime)
        if fetcher is None:
            print(f"[Warning] Unknown runtime for model listing: {runtime}", file=sys.stderr)
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

    def get_or_create_session_data(self, n8n_session_id: str, identity: Optional[str] = None) -> Dict:
        """
        Get existing session data or create new default
        Returns dict with keys: session_id, model, agent, runtime, bot_id, channel
        """
        with self._session_map_lock:
            return self._get_or_create_session_data_unlocked(n8n_session_id, identity)

    def _get_or_create_session_data_unlocked(self, n8n_session_id: str, identity: Optional[str] = None) -> Dict:
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
                if (
                    not merged.get("model")
                    or "gpt" in merged.get("model", "").lower()
                ):
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
                if (
                    not current_model
                    or not self.get_model_from_name(current_model, "codex")
                ):
                    merged["model"] = "gpt-5.4"
            elif runtime == "devin":
                current_model = merged.get("model", "")
                if (
                    not current_model
                    or not self.get_model_from_name(current_model, "devin")
                ):
                    merged["model"] = os.getenv("DEVIN_DEFAULT_MODEL", "claude-sonnet-4")

            # Validate and fix session_id if corrupted
            session_id = merged.get("session_id", "")
            if runtime in ["claude", "gemini", "codex", "copilot", "devin"]:
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

    def validate_telegram_html(self, text: str)  -> Tuple[bool, str]:
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
        if runtime in ("claude", "gemini", "codex", "devin"):
            self.get_models_for_runtime(runtime)

        # Step 1: check env-loaded or static alias tables for all runtimes that have them.
        env_alias_map = {
            "claude": self._env_claude_models,
            "gemini": self._env_gemini_models,
            "codex": self._env_codex_models,
            "devin": self._env_devin_models,
        }
        static_alias_map = {
            "claude": self.CLAUDE_MODELS,
            "gemini": self.GEMINI_MODELS,
            "codex": self.CODEX_MODELS,
            "opencode": self.OPENCODE_MODELS,
            "devin": self.DEVIN_MODELS,
        }
        
        # Try env-loaded models first, fall back to static
        models_to_check = env_alias_map.get(runtime) or static_alias_map.get(runtime)
        
        if runtime in static_alias_map and models_to_check:
            for _category, entries in models_to_check.items():
                for model_id, desc, aliases in entries:
                    aliases_lower = [a.lower() for a in aliases]
                    if (name_lower == model_id.lower() or 
                        name_lower == desc.lower() or
                        name_lower in aliases_lower):
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
        - 'restricted': Keep bounded to agent directory (default)
        - 'yolo': Allow all paths (--allow-all-paths)
        """
        # Check for /mode command at start or after newline
        mode_pattern = r'(?:^|\n)\s*/mode\s+(restricted|yolo)\s*(?:\n|$)'
        match = re.search(mode_pattern, prompt, re.IGNORECASE)
        
        if match:
            mode = match.group(1).lower()
            # Remove the command from prompt
            cleaned = re.sub(mode_pattern, '\n', prompt, flags=re.IGNORECASE).strip()
            return cleaned, mode
        
        return prompt, "restricted"  # default to restricted

    def strip_metadata(self, text: str, runtime: str) -> str:
        """Remove CLI metadata from output"""
        # First, strip thinking tags from the raw output
        text = self.strip_thinking_tags(text)

        lines = text.split("\n")
        result = []

        if runtime == "copilot":
            in_metadata = False
            for line in lines:
                # Strip ANSI escape codes (may leak despite --no-color)
                line = re.sub(r"\x1b\[[0-9;]*m", "", line)
                if re.match(r"^Total usage est:|^Total duration", line):
                    in_metadata = True
                    continue
                if not in_metadata:
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
                            error_result = f"API Error: {err_type} - {err_msg}" if err_type else f"API Error: {err_msg}"
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
            for line in lines:
                # Skip Gemini CLI metadata patterns - be very specific to avoid false positives
                line_lower = line.lower()

                # Skip lines that are clearly debug/startup output
                # Must match Gemini's specific debug format to avoid filtering user content
                if any(
                    pattern in line_lower
                    for pattern in [
                        "[startup]",  # Gemini startup profiler messages
                        "recording metric for phase:",  # Startup metrics
                        "loaded cached credentials",  # Authentication logs
                        "session:",
                        "model:",
                        "tokens:",
                        "usage:",  # Standard metadata
                    ]
                ):
                    continue
                result.append(line)

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
            skip_next_n_lines = 0

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
            agent_path_obj / ".claude" / "skills"
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
                                    name = line.replace("name:", "").strip().strip("'\"")
                                elif line.startswith("description:"):
                                    description = line.replace("description:", "").strip().strip("'\"")

                            if name:
                                available_skills.append({
                                    "name": name,
                                    "description": description or "No description",
                                    "path": str(skill_file.parent)
                                })
                except Exception as e:
                    print(f"[WARN] Error loading skill {skill_file}: {e}", file=sys.stderr)

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
                repos_to_search = [r for r in self.skill_repositories if r.get("name") == repository]
                if not repos_to_search:
                    return f"Error: Repository '{repository}' not found. Available: {', '.join(r.get('name') for r in self.skill_repositories)}"
            else:
                # Search all repositories
                repos_to_search = self.skill_repositories

            all_skills = []

            for repo in repos_to_search:
                repo_url = repo.get("url")
                repo_name = repo.get("name")
                temp_dir = f"/tmp/skills-discovery-{repo_name.lower().replace(' ', '-')}"

                try:
                    # Clean up old temp directory
                    subprocess.run(["rm", "-rf", temp_dir], capture_output=True, timeout=5)

                    # Clone the repository (shallow clone for speed)
                    result = subprocess.run(
                        ["git", "clone", "--depth", "1", repo_url, temp_dir],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )

                    if result.returncode != 0:
                        print(f"[Warn] Could not access {repo_name} repository", file=sys.stderr)
                        continue

                    # List available skills in this repository
                    skills_dir = Path(temp_dir)

                    for skill_dir in skills_dir.iterdir():
                        if skill_dir.is_dir() and not skill_dir.name.startswith('.'):
                            readme = skill_dir / "README.md"
                            if readme.exists():
                                try:
                                    content = readme.read_text()
                                    # Extract first line as description
                                    lines = content.split('\n')
                                    desc = next((line.strip('# ').strip() for line in lines if line.strip()), "No description")

                                    if not query or query.lower() in skill_dir.name.lower() or query.lower() in desc.lower():
                                        all_skills.append({
                                            "name": skill_dir.name,
                                            "description": desc[:100],
                                            "repository": repo_name
                                        })
                                except Exception:
                                    pass

                    # Clean up
                    subprocess.run(["rm", "-rf", temp_dir], capture_output=True, timeout=5)

                except Exception as e:
                    print(f"[Warn] Error searching {repo_name}: {e}", file=sys.stderr)
                    continue

            if not all_skills:
                return f"No skills found matching '{query}'."

            # Format results grouped by repository
            result_text = f"Available skills {f'(matching \"{query}\")' if query else ''}:\n\n"
            current_repo = None
            for skill in sorted(all_skills, key=lambda x: (x.get("repository"), x.get("name"))):
                if skill.get("repository") != current_repo:
                    current_repo = skill.get("repository")
                    result_text += f"\n**{current_repo}:**\n"
                result_text += f"  • {skill['name']} - {skill['description']}\n"

            result_text += f"\nTo load any skill, use: /load-skill <skill-name> [repository-name]"
            return result_text

        except Exception as e:
            return f"Error discovering skills: {str(e)}"

    def load_skill(self, skill_name: str, agent: str = "orchestrator", repository: Optional[str] = None) -> str:
        """Load a skill from configured repositories into the agent's .github/skills directory.

        Args:
            skill_name: Name of the skill to load (e.g., "helm-deploy")
            agent: Agent to load the skill into (default: orchestrator)
            repository: Optional repository name to search in (if None, searches all repositories)

        Returns:
            Status message indicating success or failure
        """
        try:
            import subprocess
            import shutil

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
                repos_to_search = [r for r in self.skill_repositories if r.get("name") == repository]
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
                    subprocess.run(["rm", "-rf", temp_dir], capture_output=True, timeout=5)

                    # Clone repository (shallow clone)
                    result = subprocess.run(
                        ["git", "clone", "--depth", "1", repo_url, temp_dir],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )

                    if result.returncode != 0:
                        print(f"[Warn] Could not access {repo_name} repository", file=sys.stderr)
                        continue

                    # Find the skill
                    source_skill = Path(temp_dir) / skill_name
                    if not source_skill.exists():
                        print(f"[Info] Skill '{skill_name}' not found in {repo_name}", file=sys.stderr)
                        subprocess.run(["rm", "-rf", temp_dir], capture_output=True, timeout=5)
                        continue

                    # Copy skill to agent's skills directory
                    shutil.copytree(source_skill, skill_target)

                    # Clean up temp directory
                    subprocess.run(["rm", "-rf", temp_dir], capture_output=True, timeout=5)

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
                    print(f"[Warn] Error loading from {repo_name}: {e}", file=sys.stderr)
                    continue

            # If we get here, skill wasn't found in any repository
            return f"Error: Skill '{skill_name}' not found in any configured repository."

        except Exception as e:
            return f"Error loading skill: {str(e)}"

    def _execute_with_context(
        self, prompt: str, delegation_data: dict, n8n_session_id: str
    ) -> str:
        """Execute a prompt with specific agent context (for sub-agent delegation)"""
        session_id = delegation_data.get("session_id")
        model = delegation_data.get("model", "gpt-5-mini")
        agent = delegation_data.get("agent", "orchestrator")
        runtime = delegation_data.get("runtime", "copilot")

        output = self._dispatch_single_runtime(
            runtime, prompt, model, agent, session_id if runtime == "claude" else None,
            False, n8n_session_id, self.command_timeout, "text",
        )

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
            _api_port = os.environ.get("API_PORT", "8001")
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
        if "{channel" in render_instruction or "{script_base_dir" in render_instruction or "{n8n_session_id" in render_instruction:
            channel_upper = channel.upper()
            render_instruction = render_instruction.format(
                channel=channel,
                channel_upper=channel_upper,
                script_base_dir=SCRIPT_BASE_DIR,
                n8n_session_id=n8n_session_id
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
        _user_identity = self._bg_identity or "unknown"
        _api_scheme = "https" if os.environ.get("SSL_CERTFILE") else "http"
        _curl_insecure = " -k" if _api_scheme == "https" else ""
        bg_task_instruction = ""
        if _shared_key:
            bg_task_instruction = f"""
[Background Tasks — IMPORTANT]
When the user asks you to run something "in the background", you MUST create a background task via the orchestrator API. Do NOT use your own internal background/async task mechanism (e.g. task tool with mode:"background") — those are invisible to the user.

To create a background task, run this curl command:
```
curl -s{_curl_insecure} -X POST {_api_scheme}://127.0.0.1:{_api_port_bg}/api/v1/background-tasks \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer shared_{_shared_key}" \\
  -H "X-User-Identity: {_user_identity}" \\
  -H "X-Auth-Channel: {channel}" \\
  -d '{{"prompt": "<the task prompt here>", "agent": "{agent}", "timeout": <seconds>}}'
```
The API returns a task_id. Tell the user the task was started and they can monitor it in the ⚡ Tasks tab (WebUI) or use `/background status <task_id>`.
Do NOT run the actual work yourself when backgrounding — the API spawns a separate agent to handle it.
The default agent for background tasks is `{agent}` (inherited from your current session). Only override `"agent"` in the JSON body if the user explicitly requests a different agent.

**IMPORTANT — You must set the `timeout` field.** Estimate how long the task will realistically take and set an appropriate timeout in seconds. Examples:
- Quick status check or simple query: 120 (2 min)
- Standard task (summarize, analyze, write): 600 (10 min)
- Multi-step or research task: 900 (15 min)
- Complex build, deploy, or batch job: 1200–1800 (20–30 min)
Default if truly uncertain: 900 (15 min). Do not omit the `timeout` field."""

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
            print(f"[Handoff] Warning: failed to load handoff context: {_handoff_err}", file=sys.stderr)

        # Channel-specific injected context files
        injection_dir = Path(os.environ.get("INJECTION_DIR", Path(SCRIPT_BASE_DIR) / "injections"))
        injection_text = ""
        try:
            injection_file = injection_dir / f"{channel}.md"
            if injection_file.exists():
                injection_content = injection_file.read_text()
                injection_text = f"\n\n[Injected context file: {injection_file}]\n{injection_content}\n"
        except Exception:
            injection_text = ""

        context = f"""{handoff_prefix}[Session ID: {n8n_session_id}]
{runtime_instruction}{injection_text}{agent_desc}{files_context}{render_instruction}{bg_task_instruction}{timeout_instruction}

User Request:
{prompt}"""
        return context

    # ------------------------------------------------------------------ streaming

    def _register_stream(
        self, session_id: str, queue, loop  # asyncio.Queue, asyncio.AbstractEventLoop
    ) -> None:
        """Register an asyncio queue for the /stream endpoint to receive chunks."""
        self._stream_queues[session_id] = (queue, loop)

    def _unregister_stream(self, session_id: str) -> None:
        """Remove the streaming queue for a session."""
        self._stream_queues.pop(session_id, None)

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
    ) -> str:
        """Execute a subprocess with PID tracking.

        When a streaming queue is registered for *n8n_session_id* (via
        _register_stream), stdout chunks are pushed to the queue in real-time
        so the /stream SSE endpoint can forward them to the browser.  A
        ``('done', '')`` sentinel is pushed when the process exits.

        Without a queue the behaviour is identical to the original
        communicate()-based approach (blocking, full-output return).
        """
        import threading as _threading

        stream_info = self._stream_queues.get(n8n_session_id)

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd,
                bufsize=1,  # line-buffered for faster streaming chunk delivery
            )

            self.track_running_query(
                n8n_session_id, process.pid, runtime, agent, prompt
            )

            if stream_info:
                # ── Streaming path ──────────────────────────────────────────
                # Read stdout line-by-line and push each line to the async
                # queue so the SSE generator can forward it immediately.
                # stderr is drained in a background thread to avoid blocking.
                queue, loop = stream_info
                stderr_buf: list = []

                def _drain_stderr() -> None:
                    try:
                        stderr_buf.append(process.stderr.read())
                    except Exception:
                        pass

                stderr_thread = _threading.Thread(target=_drain_stderr, daemon=True)
                stderr_thread.start()

                import json as _json

                stdout_chunks: list = []
                _claude_text_block_count = 0  # track text blocks for separators
                try:
                    for line in process.stdout:
                        stdout_chunks.append(line)
                        if runtime == "claude":
                            # Parse stream-json output and push text deltas
                            try:
                                obj = _json.loads(line.strip())
                                evt_type = obj.get("type")
                                if evt_type == "stream_event":
                                    event = obj.get("event") or {}
                                    inner_type = event.get("type", "")
                                    if inner_type == "content_block_start":
                                        cb = event.get("content_block") or {}
                                        if cb.get("type") == "text":
                                            # Push newline separator between text blocks
                                            # (e.g. text before tool call vs text after)
                                            if _claude_text_block_count > 0:
                                                loop.call_soon_threadsafe(
                                                    queue.put_nowait,
                                                    ("chunk", {"text": "\n\n"}),
                                                )
                                            _claude_text_block_count += 1
                                    elif inner_type == "content_block_delta":
                                        delta = event.get("delta") or {}
                                        if delta.get("type") == "text_delta":
                                            text = delta.get("text", "")
                                            if text:
                                                loop.call_soon_threadsafe(
                                                    queue.put_nowait,
                                                    ("chunk", {"text": text}),
                                                )
                            except (ValueError, KeyError, AttributeError):
                                pass
                        else:
                            loop.call_soon_threadsafe(queue.put_nowait, ("chunk", line))
                except Exception:
                    pass
                finally:
                    process.stdout.close()
                    stderr_thread.join(timeout=5)
                    process.wait()

                output = "".join(stdout_chunks) + ("".join(stderr_buf) if stderr_buf else "")
                self.update_query_output(n8n_session_id, output)
                # Record exit code for debugging subprocess errors.
                # successful responses that discuss rate-limit topics (false positives).
                self._last_exit_codes[n8n_session_id] = process.returncode if process.returncode is not None else 0
                # Signal the SSE generator that the subprocess is finished
                loop.call_soon_threadsafe(queue.put_nowait, ("done", ""))
                return output

            else:
                # ── Original blocking path ───────────────────────────────────
                try:
                    stdout, stderr = process.communicate(timeout=timeout)
                    output = stdout + (stderr if stderr else "")
                    self.update_query_output(n8n_session_id, output)
                    # Record exit code for debugging subprocess errors.
                    # rate-limit detection on successful AI responses.
                    self._last_exit_codes[n8n_session_id] = process.returncode if process.returncode is not None else 0
                    return output
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    timeout_min = timeout / 60
                    return f"Error: Command timed out (exceeded {timeout}s / {timeout_min:.1f}min)"
                finally:
                    self.clear_running_query(n8n_session_id)

        except Exception as e:
            self.clear_running_query(n8n_session_id)
            return f"Error: Failed to execute command: {e}"
        finally:
            # clear_running_query is idempotent; ensure it runs for streaming path too
            if stream_info:
                self.clear_running_query(n8n_session_id)

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
        - /mode yolo: Dangerous bypass with --yolo (auto-approve all operations)
        """
        if not self.copilot_bin:
            return "Error: Copilot executable not found. Please install copilot or ensure it's in PATH, /opt/homebrew/bin/, /usr/local/bin/, or /usr/bin/"

        # Parse /mode command from prompt, fall back to session setting
        prompt, mode = self._parse_mode_command(prompt)
        
        # Get session data once - reuse for mode and channel
        session_data = self.get_or_create_session_data(n8n_session_id)
        
        # If mode not specified in prompt, check session setting
        if mode == "restricted":
            session_mode = session_data.get("yolo_mode", "restricted")
            if session_mode == "on":
                mode = "yolo"

        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout
        
        # Get channel for file handling instructions
        channel = session_data.get("channel", "webui")

        # Only inject full context on new sessions; resumed sessions already have it
        if resume and session_id:
            context_prompt = prompt
        else:
            context_prompt = self.build_agent_context_prompt(
                agent, prompt, n8n_session_id, render_type, effective_timeout, "copilot", model, channel
            )
        
        # Add yolo mode instructions for unrestricted privileged access
        if mode == "yolo":
            yolo_instruction = (
                "\n\n[YOLO MODE ENABLED]\n"
                "Full permissions granted. ALL commands requiring elevated privileges MUST automatically "
                "prefix with 'sudo' — no exceptions. This includes:\n"
                "• Service management: sudo systemctl restart/start/stop/reload/enable/disable <service>\n"
                "• Network commands: sudo ping, sudo ssh, sudo iptables, sudo ip, etc.\n"
                "• System administration: sudo journalctl, sudo systemd-*, sudo chmod/chown on system paths\n"
                "• Any command that would fail due to insufficient permissions\n"
                "Sudo is configured without password prompt (NOPASSWD:ALL). "
                "Never ask for confirmation — execute privileged commands immediately with sudo."
            )
            context_prompt = context_prompt + yolo_instruction

        cmd = [
            self.copilot_bin,
            "-p",
            context_prompt,
            "--allow-all-tools",
            "--no-color",
            "--silent",
            "--model",
            model,
        ]

        # Add yolo flags if mode is yolo
        if mode == "yolo":
            cmd.insert(4, "--allow-all-paths")
            cmd.append("--yolo")

        if resume and session_id:
            cmd.extend(["--resume", session_id])
            print(f"[Session] Resuming Copilot session: {session_id}", file=sys.stderr)
        else:
            print(f"[Session] Starting new Copilot session in {mode} mode", file=sys.stderr)

        output = self._execute_subprocess_with_tracking(
            cmd, agent_dir, effective_timeout, "copilot", agent, prompt, n8n_session_id
        )
        return self.strip_metadata(output, "copilot")

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
        /mode yolo enables full path access via configuration.
        """
        # Parse /mode command from prompt
        prompt, mode = self._parse_mode_command(prompt)

        # Get session data once - reuse for mode and channel
        session_data = self.get_or_create_session_data(n8n_session_id)

        # If mode not specified in prompt, check session setting
        if mode == "restricted":
            session_mode = session_data.get("yolo_mode", "restricted")
            if session_mode == "on":
                mode = "yolo"

        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout
        
        # Get channel for file handling instructions
        channel = session_data.get("channel", "webui")

        # Only inject full context on new sessions; resumed sessions already have it
        if resume and session_id:
            context_prompt = prompt
        else:
            context_prompt = self.build_agent_context_prompt(
                agent, prompt, n8n_session_id, render_type, effective_timeout, "opencode", model, channel
            )

        cmd = [str(self.opencode_bin), "run", "--model", model]

        if resume and session_id:
            cmd.extend(["--session", session_id])
            print(f"[Session] Resuming OpenCode session: {session_id}", file=sys.stderr)
        else:
            print(f"[Session] Starting new OpenCode session in {mode} mode", file=sys.stderr)

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

        Uses --permission-mode restricted by default (bounded to agent directory).
        /mode yolo uses bypassPermissions for full file/shell access without prompts.
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
                agent, prompt, n8n_session_id, render_type, effective_timeout, "claude", model, channel
            )

        # Set permission mode: use bypassPermissions for yolo mode, otherwise default
        permission_mode = "bypassPermissions" if mode == "yolo" else "default"

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
            print(f"[Session] Starting new Claude session: {session_id} in {mode} mode", file=sys.stderr)
        else:
            print(f"[Session] Starting new Claude session (auto-ID) in {mode} mode", file=sys.stderr)

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
                    print(f"[Session] Captured claude session_id: {_sid}", file=sys.stderr)
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
        /mode yolo enables --yolo flag for auto-approval.
        - Read/write file operations without confirmation

        """
        # Parse /mode command from prompt
        prompt, mode = self._parse_mode_command(prompt)

        # Get session data once - reuse for mode and channel
        session_data = self.get_or_create_session_data(n8n_session_id)

        # If mode not specified in prompt, check session setting
        if mode == "restricted":
            session_mode = session_data.get("yolo_mode", "restricted")
            if session_mode == "on":
                mode = "yolo"

        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout
        
        # Get channel for file handling instructions
        channel = session_data.get("channel", "webui")

        # Only inject full context on new sessions; resumed sessions already have it
        if resume and session_id:
            context_prompt = prompt
        else:
            context_prompt = self.build_agent_context_prompt(
                agent, prompt, n8n_session_id, render_type, effective_timeout, "gemini", model, channel
            )

        cmd = ["gemini"]
        if mode == "yolo":
            cmd.append("--yolo")
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
            print(f"[Session] Starting new Gemini session in {mode} mode", file=sys.stderr)

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

        # If mode not specified in prompt, check session setting
        if mode == "restricted":
            session_mode = session_data.get("yolo_mode", "restricted")
            if session_mode == "on":
                mode = "yolo"

        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout
        
        # Get channel for file handling instructions
        channel = session_data.get("channel", "webui")

        # Only inject full context on new sessions; resumed sessions already have it
        if resume and session_id:
            context_prompt = prompt
        else:
            context_prompt = self.build_agent_context_prompt(
                agent, prompt, n8n_session_id, render_type, effective_timeout, "codex", model, channel
            )

        # Add yolo mode instructions for unrestricted privileged access (consistent with copilot runtime)
        if mode == "yolo":
            yolo_instruction = (
                "\n\n[YOLO MODE ENABLED]\n"
                "Full permissions granted. Sandbox is fully bypassed — localhost APIs are accessible. "
                "ALL commands requiring elevated privileges MUST automatically prefix with 'sudo' — no exceptions. "
                "This includes:\n"
                "• Service management: sudo systemctl restart/start/stop/reload/enable/disable <service>\n"
                "• Network commands: sudo ping, sudo ssh, sudo iptables, sudo ip, etc.\n"
                "• System administration: sudo journalctl, sudo systemd-*, sudo chmod/chown on system paths\n"
                "• API calls: curl -sk https://127.0.0.1:8001/... works — localhost is fully accessible\n"
                "• Any command that would fail due to insufficient permissions\n"
                "Sudo is configured without password prompt (NOPASSWD:ALL). "
                "Never ask for confirmation — execute privileged commands immediately with sudo."
            )
            context_prompt = context_prompt + yolo_instruction

        if resume and session_id:
            # Resume existing session - flags must come before session_id positional arg
            # codex exec resume supports --dangerously-bypass-approvals-and-sandbox
            cmd = ["codex", "exec", "resume"]
            if mode == "yolo":
                # Apply sandbox bypass and environment inheritance for resumed yolo sessions
                cmd.append("--dangerously-bypass-approvals-and-sandbox")
                cmd += ["-c", "shell_environment_policy.inherit=all"]
            if model:
                cmd += ["-m", model]
            cmd += [session_id, context_prompt]
            print(f"[Session] Resuming CODEX session: {session_id} with model {model} in {mode} mode", file=sys.stderr)
        else:
            # Start new session - flags must come BEFORE the prompt positional arg
            cmd = ["codex", "exec"]
            if mode == "yolo":
                # Bypass all sandbox restrictions (sudo, DNS, network, filesystem)
                cmd.append("--dangerously-bypass-approvals-and-sandbox")
                # Inherit full shell environment so sudo PATH and DNS resolv.conf are available
                cmd += ["-c", "shell_environment_policy.inherit=all"]
            if model:
                cmd += ["-m", model]
            cmd.append(context_prompt)
            print(f"[Session] Starting new CODEX session with model {model} in {mode} mode", file=sys.stderr)

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
    ) -> str:
        """Execute Devin CLI in non-interactive mode.

        Uses --permission-mode dangerous to bypass all approval prompts
        and allow full system access.
        """
        # Parse /mode command from prompt
        prompt, mode = self._parse_mode_command(prompt)

        # Get session data once - reuse for mode and channel
        session_data = self.get_or_create_session_data(n8n_session_id)

        # If mode not specified in prompt, check session setting
        if mode == "restricted":
            session_mode = session_data.get("yolo_mode", "restricted")
            if session_mode == "on":
                mode = "yolo"

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
                agent, prompt, n8n_session_id, render_type, effective_timeout, "devin", model, channel
            )

        # Add yolo mode instructions for unrestricted privileged access
        if mode == "yolo":
            yolo_instruction = (
                "\n\n[YOLO MODE ENABLED]\n"
                "Full permissions granted. "
                "ALL commands requiring elevated privileges MUST automatically prefix with 'sudo' — no exceptions. "
                "This includes:\n"
                "• Service management: sudo systemctl restart/start/stop/reload/enable/disable <service>\n"
                "• Network commands: sudo ping, sudo ssh, sudo iptables, sudo ip, etc.\n"
                "• System administration: sudo journalctl, sudo systemd-*, sudo chmod/chown on system paths\n"
                "• API calls: curl -sk https://127.0.0.1:8001/... works — localhost is fully accessible\n"
                "• Any command that would fail due to insufficient permissions\n"
                "Sudo is configured without password prompt (NOPASSWD:ALL). "
                "Never ask for confirmation — execute privileged commands immediately with sudo."
            )
            context_prompt = context_prompt + yolo_instruction

        # -p is a boolean flag (print/non-interactive mode); prompt goes after --
        # Permission mode: dangerous (auto-approve all) for yolo, auto (read-only) otherwise
        permission_mode = "dangerous" if mode == "yolo" else "auto"
        cmd = [devin_bin, "-p"]
        if model:
            cmd += ["--model", model]
        cmd += ["--permission-mode", permission_mode]

        if actually_resuming:
            cmd += ["-r", devin_sid]
            print(f"[Session] Resuming Devin session {devin_sid[:8]}... with model {model} in {mode} mode", file=sys.stderr)
        else:
            print(f"[Session] Starting new Devin session with model {model} in {mode} mode", file=sys.stderr)

        cmd += ["--", context_prompt]

        output = self._execute_subprocess_with_tracking(
            cmd, agent_dir, effective_timeout, "devin", agent, prompt, n8n_session_id
        )

        # After each run, capture and persist the most recent devin session UUID
        # so subsequent messages in this n8n session can resume it.
        self._save_devin_session_id(n8n_session_id, devin_bin, agent_dir)

        if "Error: Devin command failed" in output:
            return output

        return self.strip_metadata(output, "devin")

    def _get_devin_session_id(self, n8n_session_id: str) -> Optional[str]:
        """Return the stored devin session UUID for this n8n session, or None."""
        mapping_file = self.devin_session_dir / f"{n8n_session_id}.json"
        try:
            with open(mapping_file) as f:
                data = json.load(f)
                return data.get("devin_session_id")
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _save_devin_session_id(self, n8n_session_id: str, devin_bin: str, cwd: Optional[str] = None):
        """Capture the most recently active devin session UUID and store it."""
        try:
            result = subprocess.run(
                [devin_bin, "list", "--format", "json"],
                capture_output=True, text=True, timeout=10,
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
            print(f"[Session] Stored devin session mapping: {n8n_session_id[:8]}... → {devin_sid[:8]}...", file=sys.stderr)
        except Exception as e:
            print(f"[Session] Warning: could not save devin session ID: {e}", file=sys.stderr)

    def session_exists(self, session_id: str, runtime: str, n8n_session_id: Optional[str] = None) -> bool:
        """Check if session state exists for runtime"""
        if runtime == "copilot":
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
        elif runtime == "claude":
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
        return False

    def get_most_recent_session_id(
        self, runtime: str, agent: str = "devops"
    ) -> Optional[str]:
        """Get most recent session ID from storage or CLI"""
        try:
            if runtime == "copilot":
                # Modern Copilot: sessions are directories with events.jsonl inside
                session_dirs = [
                    d for d in self.session_state_dir.iterdir()
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
    _bg_identity = None   # Set per-call for slash command context
    _notification_mgr = None  # Set by create_api_app

    def _execute_background_task(self, task_id, session_id, prompt, agent, runtime, model, channel, timeout=None):
        """Run a background task in the current thread (called from thread pool)."""
        self.get_or_create_session_data(session_id)
        self.update_session_field(session_id, "agent", agent)
        self.update_session_field(session_id, "model", model)
        self.update_session_field(session_id, "runtime", runtime)
        self.update_session_field(session_id, "channel", channel)
        self.update_session_field(session_id, "render_type", "text")
        if timeout is not None:
            self.update_session_field(session_id, "timeout", timeout)
        try:
            result = self.execute(prompt, session_id)
            if self._bg_task_mgr:
                self._bg_task_mgr.complete_task(task_id, result)
        except Exception as exc:
            if self._bg_task_mgr:
                self._bg_task_mgr.fail_task(task_id, str(exc))

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

        if runtime == "copilot":
            result = self.run_copilot(
                prompt, model, agent,
                session_id if can_resume else None,
                can_resume, n8n_session_id,
                effective_timeout, render_type,
            )
        elif runtime == "opencode":
            result = self.run_opencode(
                prompt, model, agent,
                session_id if can_resume else None,
                can_resume, n8n_session_id,
                effective_timeout, render_type,
            )
        elif runtime == "claude":
            result = self.run_claude(
                prompt, model, agent,
                session_id if can_resume else None,
                can_resume, n8n_session_id,
                effective_timeout, render_type, mode,
            )
        elif runtime == "gemini":
            result = self.run_gemini(
                prompt, model, agent,
                session_id if can_resume else None,
                can_resume, n8n_session_id,
                effective_timeout, render_type,
            )
        elif runtime == "codex":
            result = self.run_codex(
                prompt, model, agent,
                session_id if can_resume else None,
                can_resume, n8n_session_id,
                effective_timeout, render_type,
            )
        elif runtime == "devin":
            result = self.run_devin(
                prompt, model, agent,
                session_id if can_resume else None,
                can_resume, n8n_session_id,
                effective_timeout, render_type,
            )
        else:
            return f"Error: Unknown runtime '{runtime}'"

        # Touch after dispatch to record completion activity
        self.touch_session(n8n_session_id)
        return result

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

        if command == "/help":
            return """🆘 **Available Commands**

**Orchestrator:**
   • /capabilities - Show what the orchestrator can help with

**Bash Commands:**
   • !command - Execute bash command directly (e.g., !pwd, !ls -la)
   • Commands run in current agent's directory with 10s timeout

**Runtime Management:**
   • /runtime list - Show available runtimes
   • /runtime set (auto|copilot|opencode|claude|gemini|codex|devin) - Switch runtime
   • /runtime current - Show current runtime

**Model Management:**
   • /model list - Show available models for current runtime
   • /model set "model_name" - Switch model
   • /model current - Show current model

**Mode Management:**
   • /mode current - Show current mode
   • /mode yolo - Enable auto-approval (no prompts)
   • /mode restricted - Disable auto-approval (restricted mode)
   • /mode list - Show available modes

**Agent Management:**
   • /agent list - Show all available agents and their locations
   • /agent set <agent_name> — switch to an agent and work with it.
   • /agent current - Show current agent
   • /agent invoke "agent_name" "prompt" - Delegate to sub-agent

**Session:**
   • /session reset - Reset current session
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
   /mode yolo
   /runtime set gemini
   /model set "gpt-5.2"
   /agent set "family"
   /agent invoke family "Find Christmas ideas for Parker"
   ask the family agent what are Parkers Christmas ideas
   have the devops agent check the server status
"""

        elif command == "/status":
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

        elif command == "/cancel":
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

        elif command == "/capabilities":
            return self.get_capabilities()

        elif command == "/runtime":
            if not argument:
                return "Usage: /runtime [list|set|current]"
            if argument == "list":
                return "🤖 **Available Runtimes**\n\n• `copilot` (GitHub Copilot)\n• `opencode` (OpenCode CLI)\n• `claude` (Claude Code CLI)\n• `gemini` (Google Gemini CLI)\n• `codex` (Codex CLI)\n• `devin` (Devin CLI)"
            elif argument == "current":
                return f"🤖 **Current Runtime:** `{current_runtime}`"
            elif argument.startswith("set "):
                new_runtime = argument[4:].strip().lower()
                if new_runtime not in [
                    "copilot",
                    "opencode",
                    "claude",
                    "gemini",
                    "codex",
                    "devin",
                ]:
                    return f"Unknown runtime: '{new_runtime}'. Use 'copilot', 'opencode', 'claude', 'gemini', 'codex', or 'devin'."

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

                self.update_session_field(n8n_session_id, "model", default_model)
                return f"✓ Switched runtime to **{new_runtime}**. Model set to `{default_model}`. Session reset."

        elif command == "/agent":
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

        elif command == "/model":
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
                return (
                    f"Current Model: `{session_data.get('model')}` ({current_runtime})"
                )
            elif argument.startswith("set "):
                model_name = argument[4:].strip().strip('"')
                model_id = self.get_model_from_name(model_name, effective_rt)
                if not model_id:
                    return f"Unknown model '{model_name}' for runtime {effective_rt}"
                self.update_session_field(n8n_session_id, "model", model_id)
                return f"✓ Switched to model `{model_id}`"

        elif command == "/session":
            if argument == "reset":
                with self._session_map_lock:
                    session_map = self.load_session_map()
                    if n8n_session_id in session_map:
                        del session_map[n8n_session_id]
                        self.save_session_map(session_map)
                return "✓ Session reset. Next message starts fresh."

        elif command == "/timeout":
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
                    return (
                        f"✓ Timeout set to `{timeout_seconds}` seconds for this session"
                    )
                except ValueError:
                    return f"❌ Invalid timeout value '{timeout_str}'. Please provide a number (30-600 seconds)"
            else:
                return "Usage: `/timeout` or `/timeout current` to show current timeout\n       `/timeout set [seconds]` to set a new timeout (30-3600 seconds)"

        elif command == "/render":
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
                return "Usage: `/render` or `/render current` to show current render type\n       `/render set [text|markdown|html|telegram_html]` to set render type"

        elif command == "/notifications":
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
                        self._notification_mgr.set_user_pref(_notif_identity, _notif_channel, "all")
                    # Always store global preference so it applies across all channels
                    self._notification_mgr.set_user_pref("_global", _notif_channel, "all")
                return "✓ Background task notifications enabled for Telegram/WebEx."

            elif argument in ["off", "mute"]:
                self.update_session_field(n8n_session_id, "notification_preference", "off")
                if self._notification_mgr:
                    if _notif_identity:
                        self._notification_mgr.set_user_pref(_notif_identity, _notif_channel, "off")
                    # Always store global preference so it applies across all channels
                    self._notification_mgr.set_user_pref("_global", _notif_channel, "off")
                return "✓ Background task notifications muted for Telegram/WebEx (WebUI only)."
            else:
                return "Usage: `/notifications [on|off]` to toggle background task notifications."

        elif command == "/mode":
            if not argument:
                argument = "current"  # Default to showing current mode

            if argument == "current":
                mode = session_data.get("yolo_mode", "restricted")
                return f"⚡ **Current Mode:** `{mode}`"

            elif argument == "list":
                return (
                    "📋 **Available Modes:**\n\n"
                    "• `yolo` - Enable auto-approval (no prompts)\n"
                    "• `restricted` - Disable auto-approval (restricted prompts)"
                )

            elif argument == "yolo":
                self.update_session_field(n8n_session_id, "yolo_mode", "on")
                return "✓ YOLO mode enabled - auto-approving actions without prompts"

            elif argument == "restricted":
                self.update_session_field(n8n_session_id, "yolo_mode", "restricted")
                return "✓ Restricted mode enabled - normal prompts enabled"

            else:
                return (
                    "Usage: `/mode current` - Show current mode\n       `/mode list` - Show available modes\n       `/mode yolo` - Enable auto-approval mode\n       `/mode restricted` - Disable auto-approval mode"
                )

        elif command == "/schedule":
            if not SCHEDULER_ENABLED:
                return "⚠️ Scheduler is not enabled on this instance."

            try:
                scheduler = _get_scheduler()
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
                return (
                    f"📋 **Job: {j['name']}**\n\n"
                    f"• **ID:** `{j['id']}`\n"
                    f"• **Schedule:** `{j['schedule']}`\n"
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
                    lines.append(f"{status} `{r.get('timestamp','?')}` — {r.get('summary','')[:100]}")
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
                    return (
                        f"✅ **Job scheduled!**\n\n"
                        f"• **ID:** `{j['id']}`\n"
                        f"• **Name:** {j['name']}\n"
                        f"• **Schedule:** `{j['schedule']}`\n"
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

        elif command == "/background":
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
                    "• `/background kill <task_id>` — Kill a running task\n\n"
                    "Background tasks run in separate sessions and don't block your chat.\n"
                    "Monitor them in the ⚡ Tasks tab in the sidebar."
                )

            sub_lower = sub.lower()

            if sub_lower == "list":
                channel = session_data.get("channel", "webui")
                identity = self._bg_identity or "unknown"
                tasks = self._bg_task_mgr.list_tasks(channel, identity)
                if not tasks:
                    return "⚡ **Background Tasks**\n\nNo background tasks."
                icons = {"running": "🟢", "completed": "✅", "failed": "❌", "killed": "🛑"}
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
                icons = {"running": "🟢", "completed": "✅", "failed": "❌", "killed": "🛑"}
                icon = icons.get(task["status"], "❓")
                elapsed = ""
                if task["status"] == "running":
                    try:
                        ct = time.mktime(time.strptime(task["created_at"], "%Y-%m-%dT%H:%M:%SZ"))
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

            bg_timeout = bg_timeout_override if bg_timeout_override is not None else get_bg_command_timeout()
            task_id = f"bg_{str(uuid4())[:8]}"
            bg_session_id = f"bg_{str(uuid4())[:8]}"
            self._bg_task_mgr.create_task(
                task_id=task_id, session_id=bg_session_id,
                user_identity=identity, channel=channel,
                agent=bg_agent, runtime=bg_runtime, model=bg_model,
                prompt=bg_prompt,
            )
            # Launch in background thread
            import concurrent.futures as _cf
            _cf.ThreadPoolExecutor(max_workers=1).submit(
                self._execute_background_task,
                task_id, bg_session_id, bg_prompt, bg_agent, bg_runtime, bg_model, channel, bg_timeout,
            )

            return (
                f"⚡ **Background task started!**\n\n"
                f"• **Task ID:** `{task_id}`\n"
                f"• **Agent:** `{bg_agent}` | Runtime: `{bg_runtime}` | Model: `{bg_model}`\n"
                f"• **Timeout:** `{bg_timeout}s` ({bg_timeout // 60}m)\n"
                f"• **Prompt:** {bg_prompt[:150]}\n\n"
                f"Check the ⚡ Tasks tab or use `/background status {task_id}` to monitor."
            )

        # --- Execution ---

        # Prepare for execution
        session_id = session_data.get("session_id")
        model = session_data.get("model", "gpt-5-mini")
        agent = session_data.get("agent", "orchestrator")
        effective_timeout = self.get_effective_timeout(session_data)
        render_type = self.get_render_type(session_data)
        # Get mode for Claude runtime
        mode = "yolo" if session_data.get("yolo_mode") == "on" else "restricted"

        # --- Direct Single-Runtime Dispatch (simplified from auto-runtime logic) ---

        # Check if we can resume
        # For Gemini, we always try to resume the latest session for context retention
        if current_runtime == "gemini":
            can_resume = True  # Always attempt to resume latest Gemini session
        elif current_runtime == "devin":
            # Devin handles its own session resumption internally via
            # _get_devin_session_id(); pass n8n_session_id for correct lookup
            can_resume = self.session_exists(
                session_id, current_runtime, n8n_session_id=n8n_session_id
            ) if session_id else self.session_exists(
                "", current_runtime, n8n_session_id=n8n_session_id
            )
        else:
            can_resume = (
                self.session_exists(session_id, current_runtime) if session_id else False
            )

        output = self._dispatch_single_runtime(
            current_runtime, prompt, model, agent, session_id, can_resume,
            n8n_session_id, effective_timeout, render_type, mode,
        )

        # Handle session ID mapping for runtimes that auto-generate IDs
        if not can_resume and current_runtime in ("copilot", "opencode", "gemini", "codex"):
            new_id = self.get_most_recent_session_id(current_runtime, agent)
            if new_id:
                self.update_session_field(n8n_session_id, "session_id", new_id)

        # Handle opencode session loss
        if current_runtime == "opencode" and can_resume and (
            "Resource not found" in output or "NotFoundError" in output
        ):
            print(
                f"[Session] Session {session_id} lost/corrupted. Starting new session.",
                file=sys.stderr,
            )
            output = self._dispatch_single_runtime(
                "opencode", prompt, model, agent, None, False,
                n8n_session_id, effective_timeout, render_type, mode,
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


def _send_pairing_code(channel: str, identity: str, code: str) -> None:
    """Best-effort delivery of a pairing code via the appropriate connector."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        if channel == "telegram":
            from telegram_connector import TelegramConnector

            config_path = os.path.join(script_dir, "telegram_config.json")
            with open(config_path) as f:
                cfg = json.load(f)
            token = cfg.get("token") or os.getenv("TELEGRAM_BOT_TOKEN", "")
            connector = TelegramConnector(token, config_file=config_path)
            connector.send_message(
                int(identity),
                f"Your pairing code is: {code}\nIt expires in 5 minutes.",
            )
        elif channel == "webex":
            config_path = os.path.join(script_dir, "webex_config.json")
            with open(config_path) as f:
                cfg = json.load(f)
            token = cfg.get("bot_token") or cfg.get("token") or os.getenv("WEBEX_BOT_TOKEN", "")
            msg = f"Your pairing code is: **{code}**\nIt expires in 5 minutes."
            # If identity looks like an email, use toPersonEmail; otherwise treat as roomId
            import re as _re
            if _re.match(r"[^@]+@[^@]+\.[^@]+", identity):
                payload = {"toPersonEmail": identity, "text": msg, "markdown": msg}
            else:
                payload = {"roomId": identity, "text": msg, "markdown": msg}
            import requests as _req
            resp = _req.post(
                "https://webexapis.com/v1/messages",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=10,
            )
            if resp.status_code != 200:
                print(f"[API] WebEX send failed ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[API] Warning: could not send pairing code via {channel}: {exc}")


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
                "q": query, "o": "json", "l": "us-en",
                "s": "0", "f": ",,,,,", "p": "1", "vqd": vqd,
            },
            headers=headers,
            timeout=8,
        )
        out = []
        for item in r2.json().get("results", [])[:max_results]:
            if item.get("image"):
                out.append({
                    "url":       item["image"],
                    "thumbnail": item.get("thumbnail", item["image"]),
                    "title":     item.get("title", ""),
                    "source":    item.get("source", ""),
                })
        return out
    except Exception:
        return []


def _resolve_telegram_identity(username: str):
    """Reverse-lookup @username in telegram_config.json user_pairings.
    Returns numeric user_id string, or None if not found (user must message bot first)."""
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


def create_api_app():  # noqa: C901 – factory kept in one place intentionally
    """Factory that builds and returns the FastAPI application."""
    import asyncio
    import concurrent.futures
    from enum import Enum

    from fastapi import FastAPI, Header, HTTPException, Request, UploadFile, File
    from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, field_validator
    import mimetypes

    global _api_auth_manager

    # ---- configuration from environment ----
    APP_ENV = os.environ.get("APP_ENV", "PROD").upper()
    IS_PRODUCTION = APP_ENV != "DEV"
    SHARED_KEY = os.environ.get("API_SHARED_KEY", "")
    if not SHARED_KEY:
        print("[SECURITY][WARN] API shared key is empty — authentication is effectively disabled. Set API_SHARED_KEY env var.", file=sys.stderr)
    PAIRING_CODE_LENGTH = int(os.environ.get("PAIRING_CODE_LENGTH", "6"))
    PAIRING_CODE_TTL = int(os.environ.get("PAIRING_CODE_TTL", "300"))
    SESSION_TOKEN_TTL = int(os.environ.get("SESSION_TOKEN_TTL", "3600"))
    SESSION_TOKEN_ABSOLUTE_TTL = int(os.environ.get("SESSION_TOKEN_ABSOLUTE_TTL", "86400"))
    CONFIG_FILE = os.environ.get("AGENT_CONFIG_FILE")
    SCHEDULER_JOBS_FILE = os.environ.get("SCHEDULER_JOBS_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".task-scheduler", "jobs.json"))
    SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "true").strip().lower() not in ("false", "0", "no")

    # ---- shared instances ----
    auth_mgr = AuthManager(
        shared_key=SHARED_KEY,
        pairing_code_length=PAIRING_CODE_LENGTH,
        pairing_code_ttl=PAIRING_CODE_TTL,
        session_token_ttl=SESSION_TOKEN_TTL,
        session_token_absolute_ttl=SESSION_TOKEN_ABSOLUTE_TTL,
        sessions_file=os.path.join(os.path.dirname(SCHEDULER_JOBS_FILE), "sessions.json"),
    )
    _api_auth_manager = auth_mgr
    rate_limiter = RateLimiter()
    session_mgr = SessionManager(config_file=CONFIG_FILE, app_env=APP_ENV)
    history_mgr = HistoryManager()
    bg_task_mgr = BackgroundTaskManager()
    session_mgr._bg_task_mgr = bg_task_mgr
    usage_tracker = RuntimeUsageTracker()

    # Shared thread pool executor for background tasks
    # Allow up to MAX_TASKS_PER_USER concurrent background tasks
    bg_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=BackgroundTaskManager.MAX_TASKS_PER_USER,
        thread_name_prefix="bg_task_"
    )

    # Notification manager for background task completion notifications
    try:
        from notification_manager import NotificationManager
        notification_mgr = NotificationManager()
    except ImportError:
        notification_mgr = None
        print("[API] NotificationManager not available — notifications disabled", file=sys.stderr)

    session_mgr._notification_mgr = notification_mgr
    
    _start_time = time.time()

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
        session_id: Optional[str] = None  # Optional: if provided, use this session_id instead of generating
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

    # ---- authentication dependency ----
    async def authenticate(
        request: Request,
        authorization: Optional[str] = Header(None),
        x_user_identity: Optional[str] = Header(None),
        x_auth_channel: Optional[str] = Header(None),
    ) -> dict:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid authorization header")

        token = authorization[7:]

        if token.startswith("shared_"):
            if not auth_mgr.validate_shared_key(token):
                raise HTTPException(status_code=401, detail="Invalid shared key")
            # Only trust X-User-Identity from loopback (where our connectors run)
            client_ip = request.client.host if request.client else ""
            is_local = client_ip in ("127.0.0.1", "::1", "localhost")
            identity = x_user_identity if (is_local and x_user_identity) else "shared_key_user"
            return {
                "identity": identity,
                "channel": x_auth_channel or "api",
                "auth_type": "shared_key",
            }

        if token.startswith("session_"):
            token_data = auth_mgr.validate_session_token(token)
            if not token_data:
                raise HTTPException(status_code=401, detail="Invalid or expired session token")
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
                bg_task_mgr.cleanup_old()

        task = asyncio.ensure_future(_periodic_cleanup())
        yield
        task.cancel()

    # ---- FastAPI app ----
    app = FastAPI(
        title="Wee-Orchestrator API",
        version="1.0.0",
        docs_url="/api/v1/docs" if not IS_PRODUCTION else None,
        redoc_url="/api/v1/redoc" if not IS_PRODUCTION else None,
        lifespan=_lifespan,
    )

    # ---- CORS middleware ----
    cors_origins = [o.strip() for o in os.environ.get("API_CORS_ORIGINS", "").split(",") if o.strip()]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-User-Identity", "X-Auth-Channel"],
        )

    # ---- generic exception handler ----
    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception):
        if IS_PRODUCTION:
            return JSONResponse(status_code=500, content={"detail": "Internal server error"})
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    # ---- endpoints ----

    @app.get("/api/v1/agents")
    async def get_agents():
        """Return list of configured agents for WebUI."""
        try:
            agents = []
            for name, info in session_mgr.AGENTS.items():
                agents.append({
                    "name": name,
                    "description": info.get("description", ""),
                    "path": info.get("path", ""),
                })
            return {"agents": agents}
        except Exception as e:
            return {"agents": [], "error": str(e)}

    @app.get("/api/v1/health")
    async def health():
        return {
            "status": "ok",
            "uptime_seconds": time.time() - _start_time,
            "version": "1.0.0",
            "agents_loaded": len(session_mgr.AGENTS),
            "scheduler_enabled": SCHEDULER_ENABLED,
            "active_sessions": len(session_mgr.load_session_map()),
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
            agents.append({
                "name": name,
                "description": info.get("description", ""),
                "path": info.get("path", ""),
            })
        return {"agents": agents}

    @app.get("/api/v1/runtimes")
    async def get_runtimes():
        """Return list of available runtimes."""
        runtimes = [
            {"id": "auto", "label": "auto"},
            {"id": "copilot", "label": "copilot"},
            {"id": "opencode", "label": "opencode"},
            {"id": "claude", "label": "claude"},
            {"id": "gemini", "label": "gemini"},
            {"id": "codex", "label": "codex"},
            {"id": "devin", "label": "devin"},
        ]
        return {"runtimes": runtimes}

    @app.get("/api/v1/models")
    async def get_models(runtime: str = "copilot"):
        """Return available models for the specified runtime.

        Uses CLI discovery for all runtimes where possible; falls back to the
        built-in static model list when the runtime CLI is unavailable or does
        not expose a model-listing command.
        """
        runtime = runtime.lower().strip()
        known_runtimes = {"copilot", "opencode", "claude", "gemini", "codex", "devin"}
        if runtime not in known_runtimes:
            return {"runtime": runtime, "models": [], "error": f"Unknown runtime: {runtime}"}

        try:
            raw = session_mgr.get_models_for_runtime(runtime)
            models = []
            for _group, model_ids in raw.items():
                for model_id in model_ids:
                    label = session_mgr._get_model_description(model_id, runtime) or model_id
                    models.append({"id": model_id, "label": label})
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
        _send_pairing_code(body.channel.value, identity, code)
        return {
            "message": f"Pairing code sent via {body.channel.value}",
            "expires_in": PAIRING_CODE_TTL,
            "identity_resolved": identity,
        }

    @app.post("/api/v1/auth/verify-pairing")
    async def verify_pairing(body: PairingVerification):
        token = auth_mgr.verify_pairing_code(body.code, body.identity)
        if not token:
            raise HTTPException(status_code=400, detail="Invalid or expired pairing code")
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
            history_sessions = history_mgr.get_sessions(user["channel"], user["identity"])
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
            existing = session_mgr.get_or_create_session_data(session_id, identity=user["identity"])
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
        if body.agent:
            session_mgr.update_session_field(session_id, "agent", body.agent)

        session_mgr._bg_identity = user["identity"]
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(
                pool, session_mgr.execute, body.query, session_id
            )

        history_mgr.append_message(user["channel"], user["identity"], session_id, "user", body.query)
        history_mgr.append_message(user["channel"], user["identity"], session_id, "assistant", result)

        session_data = session_mgr.get_or_create_session_data(session_id, identity=user["identity"])
        runtime = session_data.get("runtime", "copilot")
        return {
            "session_id": session_id,
            "response": result,
            "runtime": runtime,
            "model": session_data.get("model"),
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
            history_sessions = history_mgr.get_sessions(user["channel"], user["identity"])
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
            existing = session_mgr.get_or_create_session_data(session_id, identity=user["identity"])
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
        if body.agent:
            session_mgr.update_session_field(session_id, "agent", body.agent)

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

            # IMPORTANT: Do NOT use `with ThreadPoolExecutor(...) as pool:` here.
            # Its __exit__ calls shutdown(wait=True) which blocks the asyncio
            # event loop when the client disconnects (e.g. /cancel aborts the
            # SSE stream).  That freeze prevents ALL requests (including the
            # cancel endpoint) from being processed until execute() finishes.
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = loop.run_in_executor(pool, session_mgr.execute, body.query, session_id)

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
                                kind, data = await asyncio.wait_for(queue.get(), timeout=1.0)
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

                session_data = session_mgr.get_or_create_session_data(session_id)
                runtime = session_data.get("runtime", "copilot")
                done_payload = _json.dumps({
                    "type": "done",
                    "response": result,
                    "runtime": runtime,
                    "model": session_data.get("model"),
                })
                yield f"data: {done_payload}\n\n"
            finally:
                if not is_command:
                    session_mgr._unregister_stream(session_id)
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
            history_sessions = history_mgr.get_sessions(user["channel"], user["identity"])
            session_ids_in_history = {s["session_id"] for s in history_sessions}
            if session_id not in session_ids_in_history:
                raise HTTPException(status_code=404, detail="Session not found")
            data = session_mgr.get_or_create_session_data(session_id, identity=user["identity"])
            session_mgr.update_session_field(session_id, "channel", user["channel"])
            session_mgr.update_session_field(session_id, "render_type", "markdown")

        result = {
            "session_id": session_id,
            "agent": data.get("agent"),
            "runtime": data.get("runtime"),
            "model": data.get("model"),
            "yolo_mode": data.get("yolo_mode", "restricted"),
        }
        return result

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
            return {"cancelled": True, "message": f"Cancelled running query (PID: {pid}, Runtime: {runtime})"}
        else:
            return {"cancelled": False, "message": f"Failed to cancel query (PID: {pid})"}

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
        return usage_tracker.get_usage()

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
    async def get_history_messages(session_id: str, request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        messages = history_mgr.get_session_messages(user["channel"], user["identity"], session_id)
        if messages is None:
            raise HTTPException(status_code=404, detail="Session not found in history")
        return {"session_id": session_id, "messages": messages}

    @app.delete("/api/v1/history/sessions/{session_id}")
    async def delete_history_session(session_id: str, request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        if not history_mgr.delete_session(user["channel"], user["identity"], session_id):
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
        if not history_mgr.rename_session(user["channel"], user["identity"], session_id, title):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"renamed": True, "session_id": session_id, "title": title[:120]}

    # --- File upload ---

    @app.post("/api/v1/sessions/{session_id}/upload")
    async def upload_file(session_id: str, request: Request, file: UploadFile = File(...)):
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
        "/opt/", "/tmp/", "/home/",
    ]
    _FILE_VIEWER_MAX_SIZE = 5 * 1024 * 1024  # 5MB text limit
    _FILE_VIEWER_BINARY_EXTS = {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp",
        ".pdf",
    }
    _FILE_VIEWER_TEXT_EXTS = {
        ".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
        ".toml", ".cfg", ".ini", ".conf", ".sh", ".bash", ".zsh", ".fish",
        ".html", ".htm", ".css", ".scss", ".less", ".xml",
        ".sql", ".graphql", ".gql",
        ".rs", ".go", ".java", ".kt", ".c", ".cpp", ".h", ".hpp",
        ".rb", ".php", ".pl", ".lua", ".r", ".swift", ".scala",
        ".env", ".env.example", ".gitignore", ".dockerignore",
        ".csv", ".log", ".diff", ".patch",
        "", # extensionless files (Makefile, Dockerfile, etc.)
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
            raise HTTPException(status_code=403, detail="Path not in allowed directories")
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
            raise HTTPException(status_code=413, detail=f"File too large ({file_size} bytes, max {_FILE_VIEWER_MAX_SIZE})")

        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Cannot read file: {e}")

        # Determine language hint for syntax highlighting
        lang_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".tsx": "typescript", ".jsx": "javascript", ".json": "json",
            ".yaml": "yaml", ".yml": "yaml", ".sh": "bash", ".bash": "bash",
            ".html": "html", ".htm": "html", ".css": "css", ".sql": "sql",
            ".md": "markdown", ".xml": "xml", ".go": "go", ".rs": "rust",
            ".java": "java", ".rb": "ruby", ".php": "php", ".c": "c",
            ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
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
            raise HTTPException(status_code=403, detail="Path not in allowed directories")
        if not resolved.exists():
            raise HTTPException(status_code=404, detail="File not found")
        if not resolved.is_file():
            raise HTTPException(status_code=400, detail="Path is a directory")

        mime, _ = mimetypes.guess_type(str(resolved))
        return FileResponse(str(resolved), media_type=mime or "application/octet-stream")

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
            results = await loop.run_in_executor(pool, _ddg_image_search, q.strip(), max_r)
        return {"query": q, "results": results}

    # --- Audio Transcription ---

    @app.post("/api/v1/sessions/{session_id}/transcribe")
    async def transcribe_audio(session_id: str, request: Request, file: UploadFile = File(...)):
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
            raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

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
    async def save_scratch_notes(session_id: str, request: Request, body: ScratchNotesRequest):
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

        @field_validator("prompt")
        @classmethod
        def validate_prompt(cls, v):
            if len(v) > 10000:
                raise ValueError("Prompt must be 10,000 characters or less")
            return v

    def _emit_bg_notification(task_id: str, prompt: str, status: str, channel: str,
                               user_identity: str, output_preview=None, error=None, notify: bool = True):
        """Emit a background task completion notification via notification_mgr."""
        if notification_mgr is None:
            return
        try:
            # Re-check per-identity AND global mute preference at emit time
            # (user may have muted after the task was created, or muted from
            # a different channel whose identity doesn't match).
            if notify:
                if notification_mgr.is_muted(user_identity) or notification_mgr.is_muted("_global"):
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
        except Exception as exc:
            print(f"[API] Notification emit failed for {task_id}: {exc}", file=sys.stderr)

    def _run_background_task(task_id: str, session_id: str, prompt: str,
                             agent: str, runtime: str, model: str,
                             channel: str, user_identity: str, timeout: int = None,
                             notify: bool = True):
        """Blocking function that runs a background task in a subprocess.
        Called from a thread pool executor.

        NOTE: The entire function body is wrapped in a single try/except to
        guarantee that any unexpected exception (e.g. file-lock race on the
        session-map JSON) always transitions the task to 'failed' instead of
        leaving it stuck in 'running' forever.
        """
        import subprocess

        try:
            # Build full context prompt with agent/runtime/channel metadata
            # This mirrors what run_copilot() does for interactive sessions.
            context_prompt = session_mgr.build_agent_context_prompt(
                agent, prompt, session_id,
                render_type="text",
                timeout=timeout,
                runtime=runtime,
                model=model,
                channel=channel,
            )

            copilot_bin = session_mgr.copilot_bin or "/home/flipkey/.local/bin/copilot"

            cmd = [
                copilot_bin,
                "-p", context_prompt,
                "--silent",
                "--no-color",
                "--model", model,
                "--allow-all-tools",
                "--allow-all-paths",
            ]

            # Set timeout for subprocess (30s longer than task timeout to allow graceful shutdown)
            proc_timeout = (timeout or 900) + 30

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=proc_timeout,
                env={**os.environ, "COPILOT_AGENT": agent, "COPILOT_RUNTIME": runtime}
            )

            # Extract output - combine stdout and stderr for full result
            output = result.stdout.strip() if result.stdout else ""
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}"

            if result.returncode == 0:
                final_output = output or "Task completed successfully"
                bg_task_mgr.complete_task(task_id, final_output)
                _emit_bg_notification(task_id, prompt, "completed", channel, user_identity,
                                      output_preview=final_output, error=None, notify=notify)
            else:
                error_msg = f"Task failed with code {result.returncode}: {output}"
                bg_task_mgr.fail_task(task_id, error_msg)
                _emit_bg_notification(task_id, prompt, "failed", channel, user_identity,
                                      output_preview=None, error=error_msg, notify=notify)

        except subprocess.TimeoutExpired:
            error_msg = f"Task exceeded timeout of {timeout} seconds"
            bg_task_mgr.fail_task(task_id, error_msg)
            _emit_bg_notification(task_id, prompt, "failed", channel, user_identity,
                                  output_preview=None, error=error_msg, notify=notify)
        except Exception as exc:
            error_msg = str(exc)
            bg_task_mgr.fail_task(task_id, error_msg)
            _emit_bg_notification(task_id, prompt, "failed", channel, user_identity,
                                  output_preview=None, error=error_msg, notify=notify)
        finally:
            # Promote next queued task for this user if a slot just opened
            try:
                next_q = bg_task_mgr.get_next_queued(channel, user_identity)
                if next_q:
                    new_sid = str(uuid4())
                    bg_task_mgr.promote_queued_task(next_q["task_id"], new_sid)
                    print(f"[BG] Promoting queued task {next_q['task_id']} → running")
                    bg_executor.submit(
                        _run_background_task,
                        next_q["task_id"], new_sid, next_q["prompt"],
                        next_q["agent"], next_q["runtime"], next_q["model"],
                        next_q["channel"], next_q["user_identity"],
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
        defaults = {}
        session_map = session_mgr.load_session_map()
        
        # Search session_map for matching n8n_session_ids by identity (highest priority)
        # Then by channel, always respecting notification_preference if set
        matching_sessions_same_channel = []
        matching_sessions_other_channel = []
        
        for n8n_sid, data in session_map.items():
            # Handle legacy string format
            if isinstance(data, str):
                data = {"session_id": data}
            
            # Check if this session belongs to this user (by identity if stored)
            sid_identity = data.get("identity")
            sid_channel = data.get("channel")
            
            # Match by identity first (most reliable)
            if sid_identity and sid_identity == identity:
                if sid_channel == channel:
                    matching_sessions_same_channel.append((n8n_sid, data))
                else:
                    matching_sessions_other_channel.append((n8n_sid, data))
        
        # Use sessions from the same channel first
        for n8n_sid, data in matching_sessions_same_channel:
            if not defaults:
                defaults = dict(data)
                print(f"[API] Found matching session '{n8n_sid}' for {channel}/{identity}")
            
            # Always check for notification_preference (highest priority)
            pref = data.get("notification_preference")
            if pref:
                defaults["notification_preference"] = pref
                print(f"[API] Inherited notification_preference '{pref}' from session '{n8n_sid}'")
        
        # Fall back to other channels for the same user
        for n8n_sid, data in matching_sessions_other_channel:
            if not defaults:
                defaults = dict(data)
                print(f"[API] Found matching cross-channel session '{n8n_sid}' for {identity}")
            
            # Always prioritize notification_preference across channels
            pref = data.get("notification_preference")
            if pref:
                # If "off" is set anywhere, respect it
                if pref == "off" or not defaults.get("notification_preference"):
                    defaults["notification_preference"] = pref
                    print(f"[API] Inherited notification_preference '{pref}' from cross-channel session '{n8n_sid}'")

        agent = body.agent or defaults.get("agent", get_default_agent())
        runtime = body.runtime or defaults.get("runtime", get_default_runtime())
        model = body.model or defaults.get("model", get_default_model())

        task_id = f"bg_{str(uuid4())[:8]}"
        session_id = str(uuid4())  # Must be valid UUID format for Copilot CLI

        # Use agent-specified timeout or fall back to default (15 min)
        bg_timeout = body.timeout if body.timeout is not None else get_bg_command_timeout()

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
                notify_pref = (session_pref != "off")

        # Check concurrent limit — queue instead of rejecting
        running = bg_task_mgr.count_running(channel, identity)
        if running >= BackgroundTaskManager.MAX_TASKS_PER_USER:
            # Queue the task — it will be promoted when a running task finishes
            task = bg_task_mgr.create_task(
                task_id=task_id,
                session_id=session_id,
                user_identity=identity,
                channel=channel,
                agent=agent,
                runtime=runtime,
                model=model,
                prompt=body.prompt,
                status="queued",
                timeout=bg_timeout,
                notify=notify_pref,
            )
            queue_pos = bg_task_mgr.count_queued(channel, identity)
            print(f"[API] Task {task_id} queued (position {queue_pos}, {running}/{BackgroundTaskManager.MAX_TASKS_PER_USER} slots full)")
            return {
                "task_id": task_id,
                "session_id": session_id,
                "agent": agent,
                "runtime": runtime,
                "model": model,
                "status": "queued",
                "queue_position": queue_pos,
                "timeout": bg_timeout,
            }

        # Create task record (running immediately)
        task = bg_task_mgr.create_task(
            task_id=task_id,
            session_id=session_id,
            user_identity=identity,
            channel=channel,
            agent=agent,
            runtime=runtime,
            model=model,
            prompt=body.prompt,
            status="running",
            timeout=bg_timeout,
            notify=notify_pref,
        )

        # Run in background thread using shared executor
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            bg_executor,  # Use shared executor instead of creating new one
            _run_background_task,
            task_id, session_id, body.prompt, agent, runtime, model, channel, identity, bg_timeout,
            notify_pref
        )

        return {
            "task_id": task_id,
            "session_id": session_id,
            "agent": agent,
            "runtime": runtime,
            "model": model,
            "status": "running",
            "timeout": bg_timeout,
        }

    @app.get("/api/v1/background-tasks")
    async def list_background_tasks(request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        tasks = bg_task_mgr.list_tasks(user["channel"], user["identity"])
        # Check if running tasks are still alive
        for t in tasks:
            if t["status"] == "running" and t.get("pid"):
                try:
                    os.kill(t["pid"], 0)
                except ProcessLookupError:
                    bg_task_mgr.fail_task(t["task_id"], "Process terminated unexpectedly")
                    t["status"] = "failed"
        # Return summary (no full output_lines for list)
        result = []
        for t in tasks:
            result.append({
                "task_id": t["task_id"],
                "agent": t["agent"],
                "runtime": t["runtime"],
                "model": t["model"],
                "prompt": t["prompt"][:200],
                "status": t["status"],
                "created_at": t["created_at"],
                "completed_at": t.get("completed_at"),
                "error": t.get("error"),
            })
        return {"tasks": result}

    @app.get("/api/v1/background-tasks/{task_id}")
    async def get_background_task(task_id: str, request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        task = bg_task_mgr.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if not bg_task_mgr._identity_matches(task, user["channel"], user["identity"]):
            raise HTTPException(status_code=403, detail="Not your task")
        # Return detail with last 50 output lines
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
        }

    @app.get("/api/v1/background-tasks/{task_id}/transcript")
    async def get_background_task_transcript(task_id: str, request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        task = bg_task_mgr.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if not bg_task_mgr._identity_matches(task, user["channel"], user["identity"]):
            raise HTTPException(status_code=403, detail="Not your task")
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "final_response": task.get("final_response"),
            "output_lines": task.get("output_lines", []),
            "error": task.get("error"),
        }

    @app.delete("/api/v1/background-tasks/{task_id}")
    async def delete_background_task(task_id: str, request: Request):
        user = await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        task = bg_task_mgr.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if not bg_task_mgr._identity_matches(task, user["channel"], user["identity"]):
            raise HTTPException(status_code=403, detail="Not your task")

        if task["status"] in ("running", "queued"):
            was_running = task["status"] == "running"
            bg_task_mgr.kill_task(task_id)
            # If a running task was killed, promote the next queued task
            if was_running:
                next_q = bg_task_mgr.get_next_queued(user["channel"], user["identity"])
                if next_q:
                    new_sid = str(uuid4())
                    bg_task_mgr.promote_queued_task(next_q["task_id"], new_sid)
                    print(f"[BG] Kill triggered promotion of queued task {next_q['task_id']}")
                    loop = asyncio.get_running_loop()
                    loop.run_in_executor(
                        bg_executor,
                        _run_background_task,
                        next_q["task_id"], new_sid, next_q["prompt"],
                        next_q["agent"], next_q["runtime"], next_q["model"],
                        next_q["channel"], next_q["user_identity"],
                        next_q.get("timeout") or 900,
                        next_q.get("notify", True),
                    )
            return {"task_id": task_id, "action": "killed"}
        else:
            bg_task_mgr.delete_task(task_id)
            return {"task_id": task_id, "action": "deleted"}

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
        notifications = notification_mgr.list_notifications(user_key, unread_only=unread_only)
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
            raise HTTPException(status_code=503, detail="Notification manager unavailable")
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
            raise HTTPException(status_code=503, detail="Notification manager unavailable")
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
            raise HTTPException(status_code=503, detail="Notification manager unavailable")
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
            raise HTTPException(status_code=503, detail="Notification manager unavailable")
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
                    raise HTTPException(status_code=503, detail=f"Scheduler unavailable: {_e}")
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
            print("[SECURITY][WARN] No scheduler allowlist configured — set SCHEDULER_ALLOWED_TELEGRAM and/or SCHEDULER_ALLOWED_WEBEX env vars", file=sys.stderr)
    
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
            recurring: bool = True
    
        class UpdateJobRequest(BaseModel):
            name: Optional[str] = None
            schedule: Optional[str] = None
            agent: Optional[str] = None
            runtime: Optional[str] = None
            model: Optional[str] = None
            mode: Optional[str] = None  # "ai" (default, uses LLM) or "command" (shell)
            task: Optional[str] = None
            notify: Optional[bool] = None
            recurring: Optional[bool] = None
            enabled: Optional[bool] = None
    
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
            if not rate_limiter.check(client_ip, "scheduler_write", max_requests=20, window=60):
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
                mode=body.mode,
                task=body.task,
                notify=body.notify,
                recurring=body.recurring,
                created_by=created_by,
            )
            if not result.get("success"):
                raise HTTPException(status_code=400, detail=result.get("message", "Failed"))
            return result
    
        @app.get("/api/v1/scheduler/jobs/{job_id}")
        async def get_scheduler_job(job_id: str, request: Request):
            await _require_scheduler_auth(request)
            result = _get_scheduler().get_job(job_id)
            if not result.get("success"):
                raise HTTPException(status_code=404, detail=result.get("message", "Not found"))
            return result
    
        @app.put("/api/v1/scheduler/jobs/{job_id}")
        async def update_scheduler_job(job_id: str, body: UpdateJobRequest, request: Request):
            await _require_scheduler_auth(request)
            client_ip = request.client.host if request.client else "unknown"
            if not rate_limiter.check(client_ip, "scheduler_write", max_requests=20, window=60):
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            updates = {k: v for k, v in body.model_dump().items() if v is not None}
            if not updates:
                raise HTTPException(status_code=400, detail="No fields to update")
            result = _get_scheduler().update_job(job_id, updates)
            if not result.get("success"):
                raise HTTPException(status_code=404, detail=result.get("message", "Not found"))
            return result
    
        @app.delete("/api/v1/scheduler/jobs/{job_id}")
        async def delete_scheduler_job(job_id: str, request: Request):
            await _require_scheduler_auth(request)
            client_ip = request.client.host if request.client else "unknown"
            if not rate_limiter.check(client_ip, "scheduler_write", max_requests=20, window=60):
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            result = _get_scheduler().delete_job(job_id)
            if not result.get("success"):
                raise HTTPException(status_code=404, detail=result.get("message", "Not found"))
            return result
    
        @app.post("/api/v1/scheduler/jobs/{job_id}/pause")
        async def pause_scheduler_job(job_id: str, request: Request):
            await _require_scheduler_auth(request)
            client_ip = request.client.host if request.client else "unknown"
            if not rate_limiter.check(client_ip, "scheduler_write", max_requests=20, window=60):
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            result = _get_scheduler().pause_job(job_id)
            if not result.get("success"):
                raise HTTPException(status_code=404, detail=result.get("message", "Not found"))
            return result
    
        @app.post("/api/v1/scheduler/jobs/{job_id}/resume")
        async def resume_scheduler_job(job_id: str, request: Request):
            await _require_scheduler_auth(request)
            client_ip = request.client.host if request.client else "unknown"
            if not rate_limiter.check(client_ip, "scheduler_write", max_requests=20, window=60):
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            result = _get_scheduler().resume_job(job_id)
            if not result.get("success"):
                raise HTTPException(status_code=404, detail=result.get("message", "Not found"))
            return result
    
        @app.get("/api/v1/scheduler/jobs/{job_id}/results")
        async def get_scheduler_job_results(job_id: str, request: Request, limit: int = 20):
            await _require_scheduler_auth(request)
            limit = min(max(1, limit), 100)
            return _get_scheduler().get_results(job_id, limit=limit)
    
        @app.get("/api/v1/scheduler/jobs/{job_id}/logs")
        async def get_scheduler_job_logs(job_id: str, request: Request):
            await _require_scheduler_auth(request)
            return _get_scheduler().get_logs(job_id)
    
    # --- TODO list API ---------------------------------------------------
    def _resolve_todo_dir(agent_name: str | None) -> Path | None:
        """Resolve the TODOs/ folder for a given agent.

        Returns the agent-specific TODOs/ directory if it exists, else None.
        Checks both the new folder-based structure (TODOs/) and falls back to
        the legacy TODOs.md flat file (returned as None to signal legacy mode).
        """
        import json as _json
        default_dir = Path("/opt/fosterbot-home/TODOs")
        default_md = Path("/opt/fosterbot-home/TODOs.md")

        def _pick(base: Path):
            """Return folder path or md path for a given base directory."""
            d = base / "TODOs"
            if d.is_dir():
                return d
            md = base / "TODOs.md"
            if md.exists():
                return md
            return None

        if not agent_name:
            if default_dir.is_dir():
                return default_dir
            return default_md if default_md.exists() else None

        try:
            agents_file = Path(__file__).parent / "agents.json"
            agents_data = _json.loads(agents_file.read_text())
            for a in agents_data.get("agents", []):
                if a.get("name", "").lower() == agent_name.lower():
                    agent_path = Path(a.get("path", "/opt"))
                    result = _pick(agent_path)
                    if result:
                        return result
                    # Also check <agent_path>/fosterbot-home/
                    result = _pick(agent_path / "fosterbot-home")
                    if result:
                        return result
                    # No TODOs found — return non-existent path so UI shows empty
                    return agent_path / "TODOs"
        except Exception:
            pass
        return Path(f"/opt/{agent_name}/TODOs")

    def _resolve_todo_file(agent_name: str | None) -> Path:
        """Legacy resolver — returns a Path (may be dir or md file).

        Kept for backwards compatibility. New code should use _resolve_todo_dir.
        """
        result = _resolve_todo_dir(agent_name)
        if result is None:
            return Path("/opt/fosterbot-home/TODOs.md")
        return result

    def _parse_todo_file(todo_path: Path) -> dict | None:
        """Parse a single TODO file with DUE:/LABELS: header format.

        Returns a dict with description, due, labels, details, notes keys,
        or None if the file cannot be parsed.
        """
        import re as _re
        try:
            lines = todo_path.read_text().splitlines()
        except Exception:
            return None

        due = None
        labels = []
        detail_start = 0

        for idx, line in enumerate(lines):
            if line.startswith("DUE:"):
                due = line[4:].strip()
                detail_start = idx + 1
            elif line.startswith("LABELS:"):
                raw = line[7:].strip()
                # Parse {LABEL1},{LABEL2} format
                labels = [l.strip().strip("{}") for l in _re.split(r"[,\s]+", raw) if l.strip().strip("{}")]
                detail_start = idx + 1
            else:
                break  # headers must be contiguous at top of file

        # Everything after the headers is the details body
        details_lines = lines[detail_start:]
        # Strip leading blank line if present
        while details_lines and not details_lines[0].strip():
            details_lines = details_lines[1:]
        details = "\n".join(details_lines).strip()

        return {
            "description": todo_path.name,
            "due": due,
            "labels": labels,
            "notes": [],
            "details": details,
        }

    def _parse_todos_from_dir(todo_dir: Path, limit: int = 10) -> list:
        """Read TODOs from the folder-based structure (ACTIVE/ subfolder)."""
        active_dir = todo_dir / "ACTIVE"
        if not active_dir.is_dir():
            return []
        todos = []
        for entry in sorted(active_dir.iterdir()):
            if entry.is_file():
                parsed = _parse_todo_file(entry)
                if parsed:
                    todos.append(parsed)
                    if len(todos) >= limit:
                        break
        return todos

    def _parse_todos_from_md(todo_file: Path, limit: int = 10) -> list:
        """Parse active TODOs from the markdown file, return up to `limit`.

        Supports both the legacy flat TODOs.md format and the new folder-based
        structure (when todo_file is actually a directory).
        """
        # New folder-based structure
        if todo_file.is_dir():
            return _parse_todos_from_dir(todo_file, limit)

        if not todo_file.exists():
            return []
        todos = []
        import re
        current_todo = None
        in_details = False
        details_lines = []
        
        with open(todo_file) as f:
            for line in f:
                raw_line = line.rstrip("\n")
                stripped = raw_line.strip()
                
                # Check for TODO line
                todo_line = stripped
                if todo_line.startswith("- "):
                    todo_line = todo_line[2:]
                
                if todo_line.startswith("[ ]") or todo_line.startswith("[X]") or todo_line.startswith("[x]"):
                    # Save previous todo with details if exists
                    if current_todo:
                        if details_lines:
                            current_todo["details"] = '\n'.join(details_lines).strip()
                        todos.append(current_todo)
                        if len(todos) >= limit:
                            current_todo = None
                            break
                    
                    # Only keep active (non-completed) todos for this list
                    completed = todo_line.startswith("[X]") or todo_line.startswith("[x]")
                    
                    if not completed:
                        content = re.sub(r"^\[.\]\s+", "", todo_line)
                        due = None
                        due_match = re.search(r"\(due ([^)]+)\)", content)
                        if due_match:
                            due = due_match.group(1)
                        labels = []
                        label_match = re.search(r"\{([^}]+)\}", content)
                        if label_match:
                            labels = [l.strip() for l in label_match.group(1).split(",")]
                        # Remove due and label markers from description
                        content = re.sub(r"\s*\(due [^)]+\)", "", content)
                        content = re.sub(r"\s*\{[^}]+\}", "", content).strip()
                        current_todo = {"description": content, "due": due, "labels": labels, "notes": [], "details": ""}
                        in_details = False
                        details_lines = []
                    else:
                        # Completed todo — clear state without adding to active list
                        current_todo = None
                        in_details = False
                        details_lines = []
                        
                elif current_todo:
                    # Check for Details section (inline bold format: **Details**)
                    if stripped == "**Details**":
                        in_details = True
                        details_lines = []
                    elif in_details and stripped and not stripped.startswith("## ") and not stripped.startswith("- ["):
                        # Collect details until we hit a top-level section (## ) or a todo item
                        details_lines.append(raw_line)
                    elif raw_line.startswith("    ") and not in_details:
                        # This is a note for the current TODO
                        current_todo["notes"].append(raw_line.strip())
        
        if current_todo:
            if details_lines:
                current_todo["details"] = '\n'.join(details_lines).strip()
            todos.append(current_todo)
            
        return todos[:limit]

    @app.get("/api/v1/todos")
    async def get_todos(request: Request, agent: str = None, limit: int = 10):
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        limit = min(max(1, limit), 50)
        todo_path = _resolve_todo_file(agent)
        todos = _parse_todos_from_md(todo_path, limit)
        return {"todos": todos, "count": len(todos), "agent": agent or "default", "file": str(todo_path)}

    @app.post("/api/v1/todos/{todo_title}/complete")
    async def complete_todo(request: Request, todo_title: str):
        """Mark a TODO as complete by moving it from ACTIVE/ to COMPLETED/."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        try:
            body = await request.json()
            agent = body.get("agent")
        except Exception:
            agent = None

        todo_path = _resolve_todo_file(agent)
        if not todo_path.is_dir():
            return {"success": False, "error": "Folder-based TODO structure not found"}

        active_dir = todo_path / "ACTIVE"
        completed_dir = todo_path / "COMPLETED"
        completed_dir.mkdir(parents=True, exist_ok=True)

        # Find the file (exact match or partial)
        match = None
        for entry in active_dir.iterdir():
            if entry.is_file() and (entry.name == todo_title or todo_title in entry.name):
                match = entry
                break

        if not match:
            return {"success": False, "error": f"TODO '{todo_title}' not found in ACTIVE/"}

        dest = completed_dir / match.name
        match.rename(dest)
        return {"success": True, "moved": str(dest), "todo": match.name}

    @app.patch("/api/v1/todos/{todo_title}")
    async def update_todo_details(request: Request, todo_title: str):
        """Update a TODO's details (markdown content)."""
        await authenticate(
            request,
            authorization=request.headers.get("authorization"),
            x_user_identity=request.headers.get("x-user-identity"),
            x_auth_channel=request.headers.get("x-auth-channel"),
        )
        
        try:
            body = await request.json()
            details = body.get("details", "")
            agent = body.get("agent")

            todo_path = _resolve_todo_file(agent)

            # --- New folder-based structure ---
            if todo_path.is_dir():
                active_dir = todo_path / "ACTIVE"
                match = None
                for entry in active_dir.iterdir():
                    if entry.is_file() and (entry.name == todo_title or todo_title in entry.name):
                        match = entry
                        break
                if not match:
                    return {"success": False, "error": f"TODO '{todo_title}' not found in ACTIVE/"}

                # Preserve existing DUE:/LABELS: header lines
                existing = match.read_text().splitlines()
                header_lines = []
                for line in existing:
                    if line.startswith("DUE:") or line.startswith("LABELS:"):
                        header_lines.append(line)
                    else:
                        break

                new_content = "\n".join(header_lines)
                if details and details.strip():
                    new_content += "\n\n" + details.strip()
                match.write_text(new_content + "\n")
                return {"success": True, "updated": True, "todo": match.name}

            # --- Legacy flat-file structure ---
            todo_file = todo_path
            if not todo_file.exists():
                return {"success": False, "error": "TODO file not found"}
            
            content = todo_file.read_text()
            lines = content.split('\n')
            
            updated_lines = []
            i = 0
            while i < len(lines):
                line = lines[i]
                updated_lines.append(line)
                
                stripped = line.strip()
                if stripped.startswith('- '):
                    stripped = stripped[2:]
                
                if (stripped.startswith('[ ]') or stripped.startswith('[X]')) and todo_title in line:
                    i += 1
                    
                    while i < len(lines) and lines[i].startswith('    '):
                        updated_lines.append(lines[i])
                        i += 1
                    
                    if i < len(lines) and (lines[i].strip() == '### Details' or lines[i].strip() == '## Details'):
                        i += 1
                        while i < len(lines) and lines[i] and not lines[i].startswith('#'):
                            i += 1
                    
                    if details and details.strip():
                        updated_lines.append('')
                        updated_lines.append('### Details')
                        updated_lines.append('')
                        updated_lines.extend(details.split('\n'))
                    
                    continue
                
                i += 1
            
            todo_file.write_text('\n'.join(updated_lines))
            return {"success": True, "updated": True, "todo": todo_title}
        
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}
    
    # --- AI Media ─────────────────────────────────────────────────────────────
    _ai_media_dir = Path("/tmp/webui_ai_media")
    _ai_media_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/ai-media", StaticFiles(directory=str(_ai_media_dir)), name="ai_media")

    # --- Static WebUI — MUST BE LAST ---
    _webui_dist = Path(__file__).parent / "webui" / "dist"
    if _webui_dist.exists():
        app.mount("/ui", StaticFiles(directory=str(_webui_dist), html=True), name="webui")

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
    port = int(os.environ.get("API_PORT", "8001"))  # DEV: default 8001 to avoid collision with prod
    host = os.environ.get("API_HOST", "127.0.0.1")

    # SSL support — set SSL_CERTFILE and SSL_KEYFILE env vars to enable HTTPS
    ssl_certfile = os.environ.get("SSL_CERTFILE")
    ssl_keyfile = os.environ.get("SSL_KEYFILE")
    ssl_kwargs = {}
    if ssl_certfile and ssl_keyfile and os.path.isfile(ssl_certfile) and os.path.isfile(ssl_keyfile):
        ssl_kwargs = {"ssl_certfile": ssl_certfile, "ssl_keyfile": ssl_keyfile}
        proto = "https"
    else:
        proto = "http"

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
        choices=["copilot", "opencode", "claude", "gemini", "codex", "devin"],
        help="Set the runtime to use (choices: copilot, opencode, claude, gemini, codex, devin)",
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
        choices=["yolo", "restricted"],
        help="Set Claude permission mode: yolo (auto-approve) or restricted (ask for permissions)",
    )

    args = parser.parse_args()

    # Handle backwards compatibility: if config_file_positional is provided, use it
    if args.config_file_positional and not args.config_file:
        args.config_file = args.config_file_positional

    # Initialize manager
    manager = SessionManager(args.config_file, app_env=os.environ.get("APP_ENV", "PROD").upper())

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
