#!/usr/bin/env python3
"""
Shared base classes for bot connectors (Telegram and WebEX).

Provides :class:`BaseConfig` for shared configuration management and
:class:`BaseConnector` for shared connector infrastructure so that
platform-specific connectors only implement the parts that differ.
"""

import json
import logging
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class BaseConfig:
    """Shared configuration management for bot connectors.

    Subclasses must implement :meth:`_default_config` to return
    platform-specific default settings.
    """

    def __init__(self, config_file: str):
        self.config_file = Path(config_file)
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        """Load configuration from file or create defaults."""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r") as f:
                    data = json.load(f)
            except Exception as e:
                print(f"Error loading config: {e}", file=sys.stderr)
                return self._default_config()
            try:
                from config_schemas import CONNECTOR_VALIDATORS

                validator = CONNECTOR_VALIDATORS.get(self.config_file.name)
                if validator:
                    validator(data)
            except ImportError:
                pass
            # ValidationError from validator() is not swallowed —
            # it propagates to caller
            return data
        return self._default_config()

    def _default_config(self) -> Dict:
        """Return default configuration. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement _default_config()")

    def save(self):
        """Save configuration to file."""
        try:
            with open(self.config_file, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}", file=sys.stderr)

    def _user_dict_key(self, user_id) -> str:
        """Convert user_id to string key for dict-based config fields.

        Telegram stores user IDs as ints but serialises them as string keys;
        WebEX person IDs are already strings.  Both are handled by str().
        """
        return str(user_id)

    def is_user_allowed(self, user_id) -> bool:
        """Check if user is allowed to chat."""
        if not self.config["allowed_users"]:
            return True  # No restrictions if list is empty
        return user_id in self.config["allowed_users"]

    def get_user_session(self, user_id) -> Optional[Dict]:
        """Get session info for a user."""
        return self.config["user_pairings"].get(self._user_dict_key(user_id))

    def set_user_session(self, user_id, session_info: Dict):
        """Store session info for a user."""
        self.config["user_pairings"][self._user_dict_key(user_id)] = session_info
        self.save()

    def get_user_timeout(self, user_id) -> int:
        """Get timeout for user (default 300s)."""
        session = self.get_user_session(user_id)
        if session:
            return session.get("timeout", 300)
        return 300

    def set_user_timeout(self, user_id, timeout: int):
        """Set timeout for user (clamped 30–3600s)."""
        session = self.get_user_session(user_id)
        if session:
            session["timeout"] = max(30, min(timeout, 3600))
            self.set_user_session(user_id, session)

    def allow_user(self, user_id):
        """Add user to allowed list."""
        if user_id not in self.config["allowed_users"]:
            self.config["allowed_users"].append(user_id)
            self.save()

    def deny_user(self, user_id):
        """Remove user from allowed list."""
        if user_id in self.config["allowed_users"]:
            self.config["allowed_users"].remove(user_id)
            self.save()

    def is_user_pinned(self, user_id) -> bool:
        """Check if user is pinned to a specific agent."""
        return self._user_dict_key(user_id) in self.config.get("pinned_users", {})

    def get_pinned_agent(self, user_id) -> Optional[str]:
        """Get the pinned agent for a user, or None if not pinned."""
        pinned = self.config.get("pinned_users", {}).get(self._user_dict_key(user_id))
        return pinned.get("agent") if pinned else None

    def get_pinned_runtime(self, user_id) -> Optional[str]:
        """Get the pinned runtime for a user, or None if not set."""
        pinned = self.config.get("pinned_users", {}).get(self._user_dict_key(user_id))
        return pinned.get("runtime") if pinned else None

    def get_pinned_model(self, user_id) -> Optional[str]:
        """Get the pinned model for a user, or None if not set."""
        pinned = self.config.get("pinned_users", {}).get(self._user_dict_key(user_id))
        return pinned.get("model") if pinned else None

    def is_yolo_allowed(self, user_id) -> bool:
        """Check if user is permitted to enable /mode yolo.

        Empty yolo_allowed_users means all users are allowed (backward compat).
        """
        yolo_users = self.config.get("yolo_allowed_users", [])
        if not yolo_users:
            return True
        return user_id in yolo_users


class BaseConnector:
    """Shared connector infrastructure for Telegram and WebEX connectors.

    Subclasses must:
    - Set class attributes ``connector_name`` and ``channel_name``
    - Call :meth:`_init_shared_state` from their ``__init__``
    - Implement the abstract properties ``_safe_file_dirs``,
      ``_max_file_bytes``, and ``_copilot_api_url``
    - Implement :meth:`_make_session_id`, :meth:`_get_user_identity`,
      :meth:`_send_channel_status`, :meth:`_edit_channel_status`, and
      :meth:`_send_channel_typing`
    """

    # Override in subclass
    connector_name: str = "Connector"
    channel_name: str = "unknown"

    def _init_shared_state(self):
        """Initialise shared state. Call from subclass ``__init__``."""
        self.session_managers: Dict = {}
        self.use_api = os.getenv("USE_API", "false").lower() == "true"
        self.api_shared_key = os.getenv("API_SHARED_KEY", "")
        self.running = False
        self.shutdown_event = threading.Event()
        self._active_request_lock = threading.Lock()
        self._active_requests = 0
        self._active_requests_drained = threading.Event()
        self._active_requests_drained.set()
        self.shutdown_timeout = self._load_shutdown_timeout()

    # ── Signal handling ──────────────────────────────────────────────────────

    def _install_signal_handlers(self):
        """Install SIGTERM/SIGINT handlers when running on the main thread."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self._handle_shutdown_signal)
            except ValueError:
                logger.debug("Skipping %s handler outside main thread", sig)

    def _handle_shutdown_signal(self, signum, _frame):
        """Stop taking new work and let in-flight requests finish."""
        signame = signal.Signals(signum).name
        print(
            f"\nReceived {signame}; draining active {self.connector_name} "
            f"requests before shutdown...",
            file=sys.stderr,
        )
        self._request_shutdown(signame)

    def _load_shutdown_timeout(self) -> Optional[float]:
        """Return an optional shutdown drain timeout from the environment."""
        raw_timeout = os.environ.get("CONNECTOR_SHUTDOWN_TIMEOUT_SECONDS", "").strip()
        if not raw_timeout:
            return None
        timeout = float(raw_timeout)
        return timeout if timeout > 0 else None

    def _request_shutdown(self, reason: str = "shutdown"):
        """Mark the connector as shutting down."""
        if self.shutdown_event.is_set():
            return
        self.shutdown_event.set()
        self.running = False
        print(
            f"[INFO] {self.connector_name} connector shutdown requested: {reason}",
            file=sys.stderr,
        )

    def _begin_active_request(self) -> bool:
        """Track a request that must complete before shutdown finishes."""
        with self._active_request_lock:
            if self.shutdown_event.is_set():
                return False
            self._active_requests += 1
            self._active_requests_drained.clear()
            return True

    def _finish_active_request(self):
        """Mark a tracked request as complete."""
        with self._active_request_lock:
            if self._active_requests > 0:
                self._active_requests -= 1
            if self._active_requests == 0:
                self._active_requests_drained.set()

    def _wait_for_active_requests(
        self, component: str = None, timeout: Optional[float] = None
    ) -> bool:
        """Wait for tracked in-flight requests to finish."""
        component = component or f"{self.connector_name} connector"
        wait_timeout = self.shutdown_timeout if timeout is None else timeout
        with self._active_request_lock:
            pending = self._active_requests
        if pending <= 0:
            return True
        print(
            f"[INFO] {component} waiting for {pending} active request(s) to finish...",
            file=sys.stderr,
        )
        drained = self._active_requests_drained.wait(wait_timeout)
        if not drained:
            with self._active_request_lock:
                remaining = self._active_requests
            print(
                f"[WARN] {component} shutdown timed out with {remaining} "
                f"active request(s) still running",
                file=sys.stderr,
            )
        return drained

    # ── Session management ───────────────────────────────────────────────────

    def get_session_manager(self, session_id: str):
        """Get or create a SessionManager for the given session_id."""
        if session_id not in self.session_managers:
            from agent_manager import SessionManager

            mgr = SessionManager()
            # Backward compatibility: older SessionManager may expose
            # load_session_map instead of load_session_data
            if not hasattr(mgr, "load_session_data"):
                if hasattr(mgr, "load_session_map"):
                    mgr.load_session_data = mgr.load_session_map
                else:
                    mgr.load_session_data = lambda sid: None
            self.session_managers[session_id] = mgr
        return self.session_managers[session_id]

    def _evict_session_manager(self, session_id: str):
        """Remove cached SessionManager so the next call gets a fresh one."""
        if session_id in self.session_managers:
            del self.session_managers[session_id]
            print(
                f"[DEBUG] Evicted cached SessionManager for: {session_id}",
                file=sys.stderr,
            )

    def _enforce_pinned_session(self, user_id, session_id: str):
        """Push pinned agent/runtime/model into the SessionManager for pinned users.

        Must be called before every query or command so session data always
        reflects the pinned values regardless of what the user has set.
        """
        if not self.config.is_user_pinned(user_id):
            return
        session_mgr = self.get_session_manager(session_id)
        pinned_agent = self.config.get_pinned_agent(user_id)
        if pinned_agent:
            session_mgr.update_session_field(session_id, "agent", pinned_agent)
        pinned_runtime = self.config.get_pinned_runtime(user_id)
        if pinned_runtime:
            session_mgr.update_session_field(session_id, "runtime", pinned_runtime)
        pinned_model = self.config.get_pinned_model(user_id)
        if pinned_model:
            session_mgr.update_session_field(session_id, "model", pinned_model)

    # ── File safety ──────────────────────────────────────────────────────────

    @property
    def _safe_file_dirs(self) -> List[Path]:
        """Allowed directories for outbound file operations.

        Subclasses should override to include their platform-specific
        downloads directory alongside the shared webui_ai_media dir.
        """
        return [Path("/tmp/webui_ai_media").resolve()]

    @property
    def _max_file_bytes(self) -> int:
        """Maximum allowed outbound file size in bytes (default 50 MB)."""
        return 50 * 1024 * 1024

    def _is_safe_file_path(self, file_path: str) -> bool:
        """Validate a file path before sending it to a user.

        Checks existence, directory allowlist (no path traversal), and size.
        """
        try:
            allowed_dirs = self._safe_file_dirs
            file_path_obj = Path(file_path).resolve()

            if not file_path_obj.exists():
                print(f"[WARN] File does not exist: {file_path}", file=sys.stderr)
                return False

            is_safe = False
            for allowed_dir in allowed_dirs:
                try:
                    is_safe = file_path_obj.is_relative_to(allowed_dir)
                except AttributeError:
                    is_safe = str(file_path_obj).startswith(str(allowed_dir))
                if is_safe:
                    break

            if not is_safe:
                print(
                    f"[WARN] File outside allowed directories: {file_path}",
                    file=sys.stderr,
                )
                return False

            if file_path_obj.stat().st_size > self._max_file_bytes:
                size_mb = self._max_file_bytes // (1024 * 1024)
                print(
                    f"[WARN] File exceeds {size_mb}MB limit: {file_path}",
                    file=sys.stderr,
                )
                return False

            return True
        except Exception as e:
            print(f"[WARN] Error validating file path: {e}", file=sys.stderr)
            return False

    # ── Image / file extraction ──────────────────────────────────────────────

    def _resolve_image_path(self, url: str) -> str:
        """Resolve /ai-media/ paths to local filesystem paths and strip ANSI codes.

        Handles LLM-mangled session IDs by fuzzy-matching directory names.
        """
        url = re.sub(r"\x1b\[[0-9;]*m", "", url)
        if url.startswith("/ai-media/"):
            resolved = url.replace("/ai-media/", "/tmp/webui_ai_media/", 1)
            if os.path.exists(resolved):
                return resolved
            # Fuzzy directory match for mangled session IDs
            base_dir = "/tmp/webui_ai_media"
            parts = resolved[len(base_dir) + 1 :].split("/", 1)
            if len(parts) == 2:
                session_dir_name, filename = parts
                try:
                    candidates = []
                    for d in os.listdir(base_dir):
                        if not os.path.isdir(os.path.join(base_dir, d)):
                            continue
                        prefix_len = min(20, len(session_dir_name), len(d))
                        if d[:prefix_len] == session_dir_name[:prefix_len]:
                            candidate_path = os.path.join(base_dir, d, filename)
                            if os.path.isfile(candidate_path):
                                candidates.append(candidate_path)
                    if len(candidates) == 1:
                        print(
                            f"[DEBUG] Fuzzy-matched image path: "
                            f"{resolved} -> {candidates[0]}",
                            file=sys.stderr,
                            flush=True,
                        )
                        return candidates[0]
                    elif len(candidates) > 1:
                        best = max(candidates, key=os.path.getmtime)
                        print(
                            f"[DEBUG] Fuzzy-matched image path "
                            f"(newest of {len(candidates)}): {resolved} -> {best}",
                            file=sys.stderr,
                            flush=True,
                        )
                        return best
                except OSError as e:
                    logger.debug("Failed to check file modification time: %s", e)
            return resolved
        return url

    def extract_image_urls(self, text: str) -> tuple:
        """Extract image URLs from text/HTML/Markdown.

        Supports:
        - ``![alt](url)`` — Markdown with alt text as caption
        - ``<img src="url"/>`` — HTML img tags
        - Bare URLs — ``https://example.com/image.png``
        - Local paths — ``/ai-media/session/image.png``

        Returns:
            Tuple of ``(image_data, remaining_text)`` where ``image_data`` is
            a list of ``(url, caption)`` tuples and ``remaining_text`` is the
            input with all image references removed.
        """
        image_extensions = r'\.(jpg|jpeg|png|gif|webp|bmp|svg)(\?[^\s"<>]*)?'
        md_img_pattern = r"!\[([^\]]*)\]\(([^)]+)\)"
        img_tag_pattern = r'<img\s+[^>]*src=["\']([^"\']+)["\'][^>]*/?\s*>'
        bare_url_pattern = r'(https?://[^\s"<>]+' + image_extensions + r")"

        image_data = []
        remaining = text

        for match in re.finditer(md_img_pattern, remaining, re.IGNORECASE):
            alt_text = match.group(1).strip()
            url = self._resolve_image_path(match.group(2).strip())
            if url not in [img[0] for img in image_data]:
                image_data.append((url, alt_text))
            remaining = remaining.replace(match.group(0), "").strip()

        for match in re.finditer(img_tag_pattern, remaining, re.IGNORECASE):
            url = self._resolve_image_path(match.group(1).strip())
            if url not in [img[0] for img in image_data]:
                image_data.append((url, ""))
            remaining = remaining.replace(match.group(0), "").strip()

        for match in re.finditer(bare_url_pattern, remaining, re.IGNORECASE):
            url = match.group(1)
            if url not in [img[0] for img in image_data]:
                image_data.append((url, ""))
                remaining = remaining.replace(url, "").strip()

        return image_data, remaining

    def extract_file_paths(self, text: str) -> tuple:
        """Extract file paths from ``[FILE:...]`` markers.

        Supports:
        - ``[FILE:/path/to/file.ext]`` — without caption
        - ``[FILE:/path/to/file.ext:Caption text]`` — with caption

        Returns:
            Tuple of ``(file_data, remaining_text)`` where ``file_data`` is a
            list of ``(path, caption)`` tuples and ``remaining_text`` is the
            input with all file references removed.
        """
        file_pattern = r"\[FILE:([^\]:]+)(?::([^\]]*))?\]"
        file_data = []
        remaining = text

        for match in re.finditer(file_pattern, remaining):
            file_path = match.group(1).strip()
            caption = match.group(2).strip() if match.group(2) else ""
            if self._is_safe_file_path(file_path):
                file_data.append((file_path, caption))
                remaining = remaining.replace(match.group(0), "").strip()
            else:
                print(f"[WARN] Unsafe file path rejected: {file_path}", file=sys.stderr)

        return file_data, remaining

    # ── Command execution ────────────────────────────────────────────────────

    def _execute_command(
        self,
        command: str,
        session_id: str,
        timeout: int = 300,
        user_identity: str = None,
    ) -> str:
        """Execute slash command via agent_manager.execute() with timeout support."""
        result_container = {"response": None, "done": False}
        effective_identity = user_identity or session_id

        def run_command():
            try:
                if self.use_api:
                    result_container["response"] = self._execute_via_api(
                        command, session_id, effective_identity, self.channel_name
                    )
                else:
                    session_mgr = self.get_session_manager(session_id)
                    result_container["response"] = session_mgr.execute(
                        command, session_id
                    )
                result_container["done"] = True
            except Exception as e:
                import traceback

                print(
                    f"Error in _execute_command: {traceback.format_exc()}",
                    file=sys.stderr,
                )
                result_container["response"] = f"Error: {str(e)[:150]}"
                result_container["done"] = True

        cmd_thread = threading.Thread(target=run_command, daemon=True)
        cmd_thread.start()

        elapsed = 0
        while not result_container["done"] and elapsed < timeout:
            time.sleep(1)
            elapsed += 1

        cmd_thread.join(timeout=5)
        return result_container["response"] or "Error: Command timed out"

    def _execute_via_api(
        self, query: str, session_id: str, identity: str, channel: str
    ) -> str:
        """Execute a query via the HTTP API. Must be implemented by subclasses.

        Called by :meth:`_execute_command` and :meth:`_query_agent` when
        ``self.use_api`` is ``True``.
        """
        raise NotImplementedError("Subclasses must implement _execute_via_api()")

    # ── Live status polling ──────────────────────────────────────────────────

    @property
    def _copilot_api_url(self) -> str:
        """Base URL for the Copilot/agent API. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement _copilot_api_url")

    def _poll_live_status(self, session_id: str) -> Optional[str]:
        """Poll the live-status endpoint for real-time LLM progress (F004).

        Returns the latest status text, or None if no live status is available.
        """
        try:
            headers = {"Authorization": f"Bearer shared_{self.api_shared_key}"}
            resp = requests.get(
                f"{self._copilot_api_url}/api/v1/sessions/{session_id}/live-status",
                headers=headers,
                timeout=3,
            )
            if resp.status_code == 200:
                return resp.json().get("status")
        except Exception:
            pass
        return None

    # ── Unified query-with-status (platform abstractions required) ───────────

    def _make_session_id(self, user_id) -> str:
        """Build the session ID string for a user. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement _make_session_id()")

    def _get_user_identity(self, user_id) -> str:
        """Return the user identity string passed to the API. Must be implemented."""
        raise NotImplementedError("Subclasses must implement _get_user_identity()")

    def _send_channel_status(self, channel_id, text: str):
        """Send a status message to the channel. Returns the message ID (or None)."""
        raise NotImplementedError("Subclasses must implement _send_channel_status()")

    def _edit_channel_status(self, channel_id, msg_id, text: str):
        """Edit an existing status message in the channel."""
        raise NotImplementedError("Subclasses must implement _edit_channel_status()")

    def _send_channel_typing(self, channel_id):
        """Send a typing indicator to the channel."""
        raise NotImplementedError("Subclasses must implement _send_channel_typing()")

    def _query_agent(
        self, query: str, agent: str, model: str, user_id, timeout: int = 300
    ) -> str:
        """Query the agent_manager with a user session tied to user_id."""
        try:
            session_id = self._make_session_id(user_id)
            print(
                f"[DEBUG] Using persistent session_mgr for: {session_id}",
                file=sys.stderr,
            )
            if self.use_api:
                result = self._execute_via_api(
                    query,
                    session_id,
                    self._get_user_identity(user_id),
                    self.channel_name,
                )
            else:
                session_mgr = self.get_session_manager(session_id)
                result = session_mgr.execute(query, session_id)
            return result if result else "No response from agent"
        except Exception as e:
            import traceback

            print(f"Error in _query_agent: {traceback.format_exc()}", file=sys.stderr)
            return f"Error: {str(e)[:150]}"

    def _query_agent_with_status(
        self,
        query: str,
        agent: str,
        model: str,
        user_id,
        channel_id,
        timeout: int = 300,
    ) -> tuple:
        """Query agent with live status updates at 30-second intervals.

        Polls the live-status API endpoint for LLM-generated progress updates
        (F004). Falls back to static messages if no live status is available.

        Returns:
            Tuple of ``(response_text, status_msg_id)`` where ``status_msg_id``
            is the message to edit with the final response, or None.
        """
        result_container = {"response": None, "done": False}
        _poll_session_id = self._make_session_id(user_id)

        status_msgs = [
            "Still working on it...",
            "Sorry it's taking so long, still working on it...",
            "Still processing, hang tight...",
            "Almost there, still working...",
            "Continuing to work on this...",
        ]

        def run_query():
            print(f"[DEBUG] Query to agent: {query[:200]}", file=sys.stderr)
            result_container["response"] = self._query_agent(
                query, agent, model, user_id, timeout
            )
            result_container["done"] = True

        query_thread = threading.Thread(target=run_query, daemon=True)
        query_thread.start()

        elapsed = 0
        status_idx = 0
        status_msg_id = None
        _last_live_status = None

        while not result_container["done"] and elapsed < timeout:
            if elapsed % 5 == 0:
                self._send_channel_typing(channel_id)

            if elapsed >= 30 and (elapsed - 30) % 10 == 0:
                live_status = self._poll_live_status(_poll_session_id)

                if live_status and live_status != _last_live_status:
                    msg = f"⚙️ {live_status}"
                    _last_live_status = live_status
                elif elapsed == 30 or (elapsed > 30 and (elapsed - 30) % 30 == 0):
                    msg = status_msgs[status_idx % len(status_msgs)]
                    status_idx += 1
                else:
                    msg = None

                if msg:
                    if status_msg_id:
                        self._edit_channel_status(channel_id, status_msg_id, msg)
                    else:
                        status_msg_id = self._send_channel_status(channel_id, msg)
                    self._send_channel_typing(channel_id)

            time.sleep(1)
            elapsed += 1

        query_thread.join(timeout=5)
        return (result_container["response"] or "Error: Query timed out", status_msg_id)
