#!/usr/bin/env python3
"""
Telegram Connector for N8N Copilot Shim
Bridges Telegram chat with the agent_manager.py
Handles user pairing, message routing, and configuration
"""

import json
import logging
import math
import mimetypes
import os
import re
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests

import agent_manager
import audio_transcriber

logger = logging.getLogger(__name__)

# If production listener should be disabled to avoid duplicate responders, create the
# sentinel file /opt/n8n-copilot-shim/PROD_DISABLED. When present, any process
# started from the production path will exit immediately.
_prod_disable_path = Path("/opt/n8n-copilot-shim/PROD_DISABLED")
if (
    os.path.abspath(__file__).startswith("/opt/n8n-copilot-shim/")
    and _prod_disable_path.exists()
):
    print(
        "Production Telegram connector disabled via PROD_DISABLED sentinel.",
        file=sys.stderr,
    )
    sys.exit(0)


class TelegramConfig:
    """Manages Telegram connector configuration"""

    def __init__(self, config_file: str = "telegram_config.json"):
        self.config_file = Path(config_file)
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        """Load configuration from file or create defaults"""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading config: {e}", file=sys.stderr)
                return self._default_config()
        return self._default_config()

    def _default_config(self) -> Dict:
        """Return default configuration"""
        return {
            "token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            "allowed_users": [],  # List of telegram user IDs allowed to chat
            "user_pairings": {},  # Maps telegram user ID to session info
            "enable_auto_pair": False,  # Auto-pair new users
            "default_agent": os.environ.get("COPILOT_DEFAULT_AGENT", "orchestrator"),
            "default_model": os.environ.get("COPILOT_DEFAULT_MODEL", "gpt-5-mini"),
            "pinned_users": {},  # Maps user_id (str) to {"agent": "name"} - locks user to that agent
            "yolo_allowed_users": [],  # User IDs permitted to enable /mode yolo; empty = all allowed
        }

    def save(self):
        """Save configuration to file"""
        try:
            with open(self.config_file, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}", file=sys.stderr)

    def is_user_allowed(self, user_id: int) -> bool:
        """Check if user is allowed to chat"""
        if not self.config["allowed_users"]:
            return True  # No restrictions if list is empty
        return user_id in self.config["allowed_users"]

    def get_user_session(self, user_id: int) -> Optional[Dict]:
        """Get session info for a user"""
        return self.config["user_pairings"].get(str(user_id))

    def set_user_session(self, user_id: int, session_info: Dict):
        """Store session info for a user"""
        self.config["user_pairings"][str(user_id)] = session_info
        self.save()

    def get_user_timeout(self, user_id: int) -> int:
        """Get timeout for user (default 300s)"""
        session = self.get_user_session(user_id)
        if session:
            return session.get("timeout", 300)
        return 300

    def set_user_timeout(self, user_id: int, timeout: int):
        """Set timeout for user"""
        session = self.get_user_session(user_id)
        if session:
            session["timeout"] = max(30, min(timeout, 3600))  # Clamp 30-3600s
            self.set_user_session(user_id, session)

    def allow_user(self, user_id: int):
        """Add user to allowed list"""
        if user_id not in self.config["allowed_users"]:
            self.config["allowed_users"].append(user_id)
            self.save()

    def deny_user(self, user_id: int):
        """Remove user from allowed list"""
        if user_id in self.config["allowed_users"]:
            self.config["allowed_users"].remove(user_id)
            self.save()

    def is_user_pinned(self, user_id: int) -> bool:
        """Check if user is pinned to a specific agent"""
        return str(user_id) in self.config.get("pinned_users", {})

    def get_pinned_agent(self, user_id: int) -> Optional[str]:
        """Get the pinned agent for a user, or None if not pinned"""
        pinned = self.config.get("pinned_users", {}).get(str(user_id))
        if pinned:
            return pinned.get("agent")
        return None

    def get_pinned_runtime(self, user_id: int) -> Optional[str]:
        """Get the pinned runtime for a user, or None if not set"""
        pinned = self.config.get("pinned_users", {}).get(str(user_id))
        if pinned:
            return pinned.get("runtime")
        return None

    def get_pinned_model(self, user_id: int) -> Optional[str]:
        """Get the pinned model for a user, or None if not set"""
        pinned = self.config.get("pinned_users", {}).get(str(user_id))
        if pinned:
            return pinned.get("model")
        return None

    def is_yolo_allowed(self, user_id: int) -> bool:
        """Check if user is permitted to enable /mode yolo.
        If yolo_allowed_users is empty, all users are allowed (backward compatible)."""
        yolo_users = self.config.get("yolo_allowed_users", [])
        if not yolo_users:
            return True
        return user_id in yolo_users


class TelegramConnector:
    """Main Telegram connector class"""

    def __init__(self, token: str, config_file: str = "telegram_config.json"):
        """
        Initialize Telegram connector

        Args:
            token: Telegram bot token
            config_file: Path to configuration file
        """
        self.config = TelegramConfig(config_file)

        # Prefer config file token over env var (env var may be stale in systemd)
        config_token = self.config.config.get("token", "")
        self.token = config_token if config_token else token

        # Keep persistent SessionManager per session_id for context persistence
        self.session_managers = {}  # {session_id: SessionManager}

        # Set token in config if provided
        if self.token and not self.config.config.get("token"):
            self.config.config["token"] = self.token
            self.config.save()

        # API mode configuration
        self.use_api = os.getenv("USE_API", "false").lower() == "true"
        self.api_url_copilot = os.getenv("API_URL", "http://127.0.0.1:8001")
        self.api_shared_key = os.getenv("API_SHARED_KEY", "")

        self.api_url = f"https://api.telegram.org/bot{self.token}"
        # Determine bot id (useful when multiple bots run against same service)
        self.bot_id = None
        try:
            resp = requests.get(f"{self.api_url}/getMe", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok") and "result" in data and "id" in data["result"]:
                self.bot_id = data["result"]["id"]
                print(f"[DEBUG] Detected bot id: {self.bot_id}", file=sys.stderr)
            else:
                print(f"[WARN] getMe did not return bot id: {data}", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Failed to determine bot id: {e}", file=sys.stderr)

        self.offset = 0
        self.running = False
        self.shutdown_event = threading.Event()
        self._active_request_lock = threading.Lock()
        self._active_requests = 0
        self._active_requests_drained = threading.Event()
        self._active_requests_drained.set()
        self.shutdown_timeout = self._load_shutdown_timeout()
        self.poll_slice_timeout = max(
            1.0, float(os.environ.get("TELEGRAM_POLL_SLICE_TIMEOUT_SECONDS", "5"))
        )

        if not self.config.config.get("allowed_users"):
            print(
                "⚠️  WARNING: allowed_users is empty — ALL Telegram users can interact with this bot!",
                file=sys.stderr,
            )

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
            f"\nReceived {signame}; draining active Telegram requests before shutdown...",
            file=sys.stderr,
        )
        self._request_shutdown(signame)

    def _load_shutdown_timeout(self) -> Optional[float]:
        """Return an optional shutdown drain timeout from the environment."""
        raw_timeout = os.environ.get("CONNECTOR_SHUTDOWN_TIMEOUT_SECONDS", "").strip()
        if not raw_timeout:
            return None

        timeout = float(raw_timeout)
        if timeout <= 0:
            return None
        return timeout

    def _request_shutdown(self, reason: str = "shutdown"):
        """Mark the connector as shutting down."""
        if self.shutdown_event.is_set():
            return
        self.shutdown_event.set()
        self.running = False
        print(f"[INFO] Telegram connector shutdown requested: {reason}", file=sys.stderr)

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
        self, component: str = "Telegram connector", timeout: Optional[float] = None
    ) -> bool:
        """Wait for tracked in-flight requests to finish."""
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
                f"[WARN] {component} shutdown timed out with {remaining} active request(s) still running",
                file=sys.stderr,
            )
        return drained

    def get_session_manager(self, session_id: str):
        """Get or create SessionManager for session_id"""
        if session_id not in self.session_managers:
            from agent_manager import SessionManager

            mgr = SessionManager()
            # Backwards compatibility: ensure older SessionManager implementations
            # that expose load_session_map still satisfy callers expecting
            # load_session_data
            if not hasattr(mgr, "load_session_data"):
                if hasattr(mgr, "load_session_map"):
                    mgr.load_session_data = mgr.load_session_map
                else:
                    mgr.load_session_data = lambda sid: None
            self.session_managers[session_id] = mgr
        return self.session_managers[session_id]

    def _evict_session_manager(self, session_id: str):
        """Remove cached SessionManager so next call gets a fresh one"""
        if session_id in self.session_managers:
            del self.session_managers[session_id]
            print(
                f"[DEBUG] Evicted cached SessionManager for: {session_id}",
                file=sys.stderr,
            )

    def register_bot_commands(self) -> str:
        """Register slash commands with Telegram BotFather for autocomplete.

        Fetches command names and descriptions from agent_manager's
        SessionManager slash-command registry and calls the Telegram
        Bot API ``setMyCommands`` endpoint.  Called automatically on
        startup and can be refreshed via ``/registercommands``.
        """
        try:
            from agent_manager import SessionManager

            sm = SessionManager()
            commands = sm.get_slash_commands()
        except Exception as e:
            logger.warning(
                "Failed to load slash commands from SessionManager: %s", e
            )
            commands = {}

        if not commands:
            return "\u26a0\ufe0f No slash commands found to register."

        bot_commands = []
        seen: set = set()
        for cmd, desc in commands.items():
            name = cmd.lstrip("/").lower()
            if not re.match(r"^[a-z0-9_]{1,32}$", name):
                continue
            if name in seen:
                continue
            seen.add(name)
            desc = (desc or "No description")[:256]
            if len(desc) < 3:
                desc = desc + "." * (3 - len(desc))
            bot_commands.append({"command": name, "description": desc})

        bot_commands.append(
            {
                "command": "registercommands",
                "description": "Re-register slash commands with Telegram",
            }
        )
        bot_commands.sort(key=lambda x: x["command"])

        try:
            resp = requests.post(
                f"{self.api_url}/setMyCommands",
                json={"commands": bot_commands},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                count = len(bot_commands)
                logger.info("Registered %d commands with Telegram", count)
                return (
                    f"\u2705 Registered {count} commands with Telegram."
                )
            else:
                err = data.get("description", "Unknown error")
                return f"\u26a0\ufe0f setMyCommands failed: {err}"
        except Exception as e:
            logger.error("Failed to register bot commands: %s", e)
            return (
                f"\u274c Failed to register commands: {str(e)[:100]}"
            )

    def _enforce_pinned_session(self, user_id: int, session_id: str):
        """For pinned users, push pinned agent/runtime/model into the SessionManager.
        Must be called before every query or command so the SessionManager's session
        data always reflects the pinned values regardless of what the user has set."""
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

    def _execute_via_api(
        self, query: str, session_id: str, user_identity: str, channel: str
    ) -> str:
        """Execute query via API using shared key authentication."""
        try:
            headers = {
                "Authorization": f"Bearer shared_{self.api_shared_key}",
                "Content-Type": "application/json",
                "X-User-Identity": user_identity,
                "X-Auth-Channel": channel,
            }

            # Ensure session exists
            requests.post(
                f"{self.api_url_copilot}/api/v1/sessions/create",
                headers=headers,
                json={},
                timeout=10,
            )

            # Execute the query
            resp = requests.post(
                f"{self.api_url_copilot}/api/v1/sessions/{session_id}/execute",
                headers=headers,
                json={"query": query},
                timeout=(
                    self.config.get_user_timeout(
                        int(user_identity.replace("telegram_", ""))
                    )
                    if "telegram_" in str(user_identity)
                    else 600
                ),
            )

            if resp.status_code == 200:
                return resp.json().get("response", "No response from API")
            else:
                print(
                    f"[WARN] API request failed ({resp.status_code}): {resp.text}",
                    file=sys.stderr,
                )
                # Fallback to direct mode
                session_mgr = self.get_session_manager(session_id)
                return session_mgr.execute(query, session_id)
        except Exception as e:
            print(
                f"[WARN] API request exception: {e}, falling back to direct mode",
                file=sys.stderr,
            )
            session_mgr = self.get_session_manager(session_id)
            return session_mgr.execute(query, session_id)

    def get_updates(self, timeout: int = 30) -> List[Dict]:
        """Fetch new messages from Telegram"""
        deadline = time.monotonic() + max(0, timeout)

        while self.running and not self.shutdown_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []

            poll_timeout = min(self.poll_slice_timeout, remaining)
            server_timeout = max(1, math.ceil(poll_timeout))

            try:
                response = requests.get(
                    f"{self.api_url}/getUpdates",
                    params={"offset": self.offset, "timeout": server_timeout},
                    timeout=poll_timeout + 5,
                )
                response.raise_for_status()
                updates = response.json().get("result", [])

                if updates:
                    self.offset = updates[-1]["update_id"] + 1
                    return updates
            except requests.exceptions.ReadTimeout:
                continue
            except Exception as e:
                if self.shutdown_event.is_set() or not self.running:
                    return []
                print(f"Error fetching updates: {e}", file=sys.stderr)
                return []

        return []

    def sanitize_telegram_html(self, text: str) -> str:
        """Sanitize HTML to only contain Telegram-supported tags.
        Telegram supports: b, strong, i, em, u, ins, s, strike, del, a, code, pre, blockquote, tg-spoiler
        """
        # Supported Telegram tags (opening and closing)
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
            "a",
            "code",
            "pre",
            "blockquote",
            "tg-spoiler",
            "tg-emoji",
        }

        # Replace <br> with newline before processing
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

        # Replace <pre><code class="..."> with just <pre>
        text = re.sub(r"<pre>\s*<code[^>]*>", "<pre>", text)
        text = re.sub(r"</code>\s*</pre>", "</pre>", text)

        # Remove attributes from all tags except <a> (which needs href) and <blockquote> (expandable)
        def clean_tag(match):
            full = match.group(0)
            # Closing tags
            if full.startswith("</"):
                tag_name = re.match(r"</(\w[\w-]*)", full)
                if tag_name and tag_name.group(1).lower() in supported_tags:
                    return f"</{tag_name.group(1)}>"
                return ""  # Remove unsupported closing tag
            # Opening tags
            tag_match = re.match(r"<(\w[\w-]*)([\s>])", full)
            if not tag_match:
                # Self-closing or malformed - remove
                return ""
            tag_name = tag_match.group(1).lower()
            if tag_name not in supported_tags:
                return ""  # Remove unsupported tag
            if tag_name == "a":
                # Keep href attribute
                href = re.search(r'href=["\']([^"\']*)["\']', full, re.IGNORECASE)
                if href:
                    return f'<a href="{href.group(1)}">'
                return ""  # <a> without href is useless
            if tag_name == "blockquote":
                if "expandable" in full:
                    return "<blockquote expandable>"
                return "<blockquote>"
            # All other supported tags - strip attributes
            return f"<{tag_name}>"

        text = re.sub(r"</?[\w][\w-]*(?:\s[^>]*)?\s*/?>", clean_tag, text)

        # Escape stray < and > that aren't part of valid tags
        # First protect valid tags, then escape strays, then restore
        import html as html_mod

        parts = re.split(
            r"(</?(?:" + "|".join(supported_tags) + r")(?:\s[^>]*)?>)", text
        )
        result = []
        for i, part in enumerate(parts):
            if i % 2 == 0:
                # Not a tag - escape any remaining < >
                part = (
                    part.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                )
            result.append(part)
        text = "".join(result)

        return text

    def send_message(self, chat_id: int, text: str) -> Optional[int]:
        """Send message to Telegram chat with HTML formatting, fallback to plain text. Returns message_id."""
        try:
            sanitized = self.sanitize_telegram_html(text)
            max_len = 4096
            chunks = (
                [sanitized[i : i + max_len] for i in range(0, len(sanitized), max_len)]
                if sanitized
                else ["No response"]
            )

            last_msg_id = None
            for chunk in chunks:
                # Log outbound attempt (short snippet)
                print(
                    f"[DEBUG OUTBOUND] send_message -> chat_id={chat_id} text_snippet={repr(chunk[:200])}",
                    file=sys.stderr,
                )
                resp = requests.post(
                    f"{self.api_url}/sendMessage",
                    json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"},
                    timeout=30,
                )
                print(
                    f"[DEBUG OUTBOUND] send_message HTTP {resp.status_code} for chat_id={chat_id}",
                    file=sys.stderr,
                )
                if resp.status_code != 200:
                    print(
                        f"[WARN] HTML send failed ({resp.status_code}): {resp.text[:200]}",
                        file=sys.stderr,
                    )
                    # Fallback to plain text
                    plain = re.sub(r"<[^>]+>", "", chunk)
                    plain = (
                        plain.replace("&amp;", "&")
                        .replace("&lt;", "<")
                        .replace("&gt;", ">")
                    )
                    print(
                        f"[DEBUG OUTBOUND] send_message falling back to plain text for chat_id={chat_id}",
                        file=sys.stderr,
                    )
                    resp = requests.post(
                        f"{self.api_url}/sendMessage",
                        json={"chat_id": chat_id, "text": plain},
                        timeout=30,
                    )
                    print(
                        f"[DEBUG OUTBOUND] send_message fallback HTTP {resp.status_code} for chat_id={chat_id}",
                        file=sys.stderr,
                    )
                    if resp.status_code != 200:
                        print(
                            f"[ERROR] Plain text also failed ({resp.status_code}): {resp.text[:200]}",
                            file=sys.stderr,
                        )

                if resp.status_code == 200:
                    try:
                        result = resp.json()
                        if result.get("ok"):
                            last_msg_id = result["result"]["message_id"]
                            print(
                                f"[DEBUG OUTBOUND] send_message succeeded message_id={last_msg_id} for chat_id={chat_id}",
                                file=sys.stderr,
                            )
                    except Exception as e:
                        print(
                            f"[WARN] send_message response JSON parse error: {e}",
                            file=sys.stderr,
                        )
            return last_msg_id
        except Exception as e:
            print(f"Error sending message: {e}", file=sys.stderr)
            return None

    def edit_message(self, chat_id: int, message_id: int, text: str):
        """Edit an existing message with HTML formatting, fallback to plain text."""
        try:
            sanitized = self.sanitize_telegram_html(text)
            max_len = 4096
            chunks = (
                [sanitized[i : i + max_len] for i in range(0, len(sanitized), max_len)]
                if sanitized
                else ["No response"]
            )

            # Log edit attempt
            print(
                f"[DEBUG OUTBOUND] edit_message -> chat_id={chat_id} message_id={message_id} text_snippet={repr(chunks[0][:200])}",
                file=sys.stderr,
            )
            resp = requests.post(
                f"{self.api_url}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": chunks[0],
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
            print(
                f"[DEBUG OUTBOUND] edit_message HTTP {resp.status_code} for chat_id={chat_id} message_id={message_id}",
                file=sys.stderr,
            )
            if resp.status_code != 200:
                # Fallback to plain text
                plain = re.sub(r"<[^>]+>", "", chunks[0])
                plain = (
                    plain.replace("&amp;", "&")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                )
                print(
                    f"[DEBUG OUTBOUND] edit_message falling back to plain text for chat_id={chat_id} message_id={message_id}",
                    file=sys.stderr,
                )
                resp = requests.post(
                    f"{self.api_url}/editMessageText",
                    json={"chat_id": chat_id, "message_id": message_id, "text": plain},
                    timeout=10,
                )
                print(
                    f"[DEBUG OUTBOUND] edit_message fallback HTTP {resp.status_code} for chat_id={chat_id} message_id={message_id}",
                    file=sys.stderr,
                )
                if resp.status_code != 200:
                    print(
                        f"[WARN] Edit failed ({resp.status_code}): {resp.text[:200]}",
                        file=sys.stderr,
                    )

            for chunk in chunks[1:]:
                self.send_message(chat_id, chunk)
        except Exception as e:
            print(f"Error editing message: {e}", file=sys.stderr)

    def send_typing(self, chat_id: int):
        """Send typing indicator to chat"""
        try:
            print(f"[DEBUG OUTBOUND] send_typing -> chat_id={chat_id}", file=sys.stderr)
            resp = requests.post(
                f"{self.api_url}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
                timeout=5,
            )
            print(
                f"[DEBUG OUTBOUND] send_typing HTTP {resp.status_code} for chat_id={chat_id}",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"Error sending typing indicator: {e}", file=sys.stderr)

    def unpin_all_messages(self, chat_id: int) -> bool:
        """Unpin all messages in the chat."""
        try:
            resp = requests.post(
                f"{self.api_url}/unpinAllChatMessages",
                json={"chat_id": chat_id},
                timeout=5,
            )
            if resp.status_code == 200 and resp.json().get("ok"):
                return True
            print(
                f"[DEBUG] unpinAllChatMessages status: {resp.status_code}",
                file=sys.stderr,
            )
            return False
        except Exception as e:
            print(f"Error unpinning all messages: {e}", file=sys.stderr)
            return False

    def pin_message(self, chat_id: int, message_id: int) -> bool:
        """Pin a message in the chat. Returns True if successful."""
        try:
            resp = requests.post(
                f"{self.api_url}/pinChatMessage",
                json={"chat_id": chat_id, "message_id": message_id},
                timeout=5,
            )
            if resp.status_code == 200 and resp.json().get("ok"):
                return True
            print(
                f"[WARN] Failed to pin message ({resp.status_code}): {resp.text[:200]}",
                file=sys.stderr,
            )
            return False
        except Exception as e:
            print(f"Error pinning message: {e}", file=sys.stderr)
            return False

    def _is_safe_file_path(self, file_path: str) -> bool:
        """Validate file path for security.

        Checks:
        - File exists
        - File is within allowed directories (telegram_downloads, /tmp/webui_ai_media)
        - No path traversal attacks
        - File size within limits (50MB)
        """
        try:
            allowed_dirs = [
                Path("/opt/n8n-copilot-shim-dev/telegram_downloads").resolve(),
                Path("/tmp/webui_ai_media").resolve(),
            ]
            file_path_obj = Path(file_path).resolve()

            # Check file exists
            if not file_path_obj.exists():
                print(f"[WARN] File does not exist: {file_path}", file=sys.stderr)
                return False

            # Check file is in any allowed directory
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

            # Check file size (50MB limit)
            if file_path_obj.stat().st_size > 50 * 1024 * 1024:
                print(f"[WARN] File exceeds 50MB limit: {file_path}", file=sys.stderr)
                return False

            return True
        except Exception as e:
            print(f"[WARN] Error validating file path: {e}", file=sys.stderr)
            return False

    def send_photo(
        self, chat_id: int, photo_source: str, caption: str = ""
    ) -> Optional[int]:
        """Send a photo to Telegram chat via URL or local file upload. Returns message_id."""
        try:
            is_local = not photo_source.startswith(
                ("http://", "https://")
            ) and os.path.isfile(photo_source)
            print(
                f"[DEBUG] Attempting sendPhoto {'(local file)' if is_local else '(URL)'}: {photo_source}",
                file=sys.stderr,
            )

            if is_local:
                # Local file: multipart upload
                mime_type, _ = mimetypes.guess_type(photo_source)
                if not mime_type:
                    mime_type = "image/png"
                data = {"chat_id": chat_id}
                if caption:
                    sanitized_caption = self.sanitize_telegram_html(caption.strip())
                    data["caption"] = sanitized_caption[:1024]
                    data["parse_mode"] = "HTML"
                with open(photo_source, "rb") as f:
                    files = {"photo": (Path(photo_source).name, f, mime_type)}
                    resp = requests.post(
                        f"{self.api_url}/sendPhoto",
                        data=data,
                        files=files,
                        timeout=30,
                    )
                    if resp.status_code != 200 and caption:
                        data.pop("parse_mode", None)
                        f.seek(0)
                        resp = requests.post(
                            f"{self.api_url}/sendPhoto",
                            data=data,
                            files={"photo": (Path(photo_source).name, f, mime_type)},
                            timeout=30,
                        )
                    if resp.status_code != 200:
                        print(
                            f"[WARN] sendPhoto (local) failed ({resp.status_code}): {resp.text[:200]}",
                            file=sys.stderr,
                        )
                        self.send_message(
                            chat_id,
                            f"⚠️ Failed to send image: {Path(photo_source).name}",
                        )
                        return None
                result = resp.json()
                if result.get("ok"):
                    return result["result"]["message_id"]
            else:
                # URL-based send
                data = {"chat_id": chat_id, "photo": photo_source}
                if caption:
                    sanitized_caption = self.sanitize_telegram_html(caption.strip())
                    data["caption"] = sanitized_caption[:1024]
                    data["parse_mode"] = "HTML"
                resp = requests.post(
                    f"{self.api_url}/sendPhoto",
                    json=data,
                    timeout=30,
                )
                if resp.status_code != 200:
                    if caption:
                        data.pop("parse_mode", None)
                        resp = requests.post(
                            f"{self.api_url}/sendPhoto", json=data, timeout=30
                        )
                    if resp.status_code != 200:
                        print(
                            f"[WARN] sendPhoto failed ({resp.status_code}): {resp.text[:200]}",
                            file=sys.stderr,
                        )
                        self.send_message(
                            chat_id, f'<a href="{photo_source}">📷 Image link</a>'
                        )
                        return None
                result = resp.json()
                if result.get("ok"):
                    return result["result"]["message_id"]
        except Exception as e:
            print(f"Error sending photo: {e}", file=sys.stderr)
            self.send_message(chat_id, f'<a href="{photo_url}">📷 Image link</a>')
        return None

    def send_document(
        self, chat_id: int, file_path: str, caption: str = "", filename: str = None
    ) -> Optional[int]:
        """Send a document to Telegram chat via multipart upload. Returns message_id."""
        try:
            # Security validation
            if not self._is_safe_file_path(file_path):
                self.send_message(
                    chat_id, f"⚠️ Cannot send file: {file_path} (security check failed)"
                )
                return None

            print(
                f"[DEBUG] Attempting sendDocument with file: {file_path}",
                file=sys.stderr,
            )

            # Detect MIME type
            mime_type, _ = mimetypes.guess_type(file_path)
            if not mime_type:
                mime_type = "application/octet-stream"

            # Upload file via multipart
            with open(file_path, "rb") as f:
                files = {"document": (filename or Path(file_path).name, f, mime_type)}
                data = {"chat_id": chat_id}

                if caption:
                    # Sanitize caption (same as images)
                    sanitized_caption = self.sanitize_telegram_html(caption.strip())
                    data["caption"] = sanitized_caption[:1024]  # Telegram limit
                    data["parse_mode"] = "HTML"

                resp = requests.post(
                    f"{self.api_url}/sendDocument",
                    data=data,
                    files=files,
                    timeout=60,  # Longer timeout for file uploads
                )

                if resp.status_code != 200:
                    # Retry without HTML parse_mode if caption failed
                    if caption:
                        data.pop("parse_mode", None)
                        resp = requests.post(
                            f"{self.api_url}/sendDocument",
                            data=data,
                            files={
                                "document": (
                                    filename or Path(file_path).name,
                                    open(file_path, "rb"),
                                    mime_type,
                                )
                            },
                            timeout=60,
                        )

                    if resp.status_code != 200:
                        print(
                            f"[WARN] sendDocument failed ({resp.status_code}): {resp.text[:200]}",
                            file=sys.stderr,
                        )
                        self.send_message(
                            chat_id, f"⚠️ Failed to send file: {Path(file_path).name}"
                        )
                        return None

                result = resp.json()
                if result.get("ok"):
                    return result["result"]["message_id"]
        except Exception as e:
            print(f"Error sending document: {e}", file=sys.stderr)
            self.send_message(chat_id, f"⚠️ Error sending file: {str(e)}")
        return None

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
                            f"[DEBUG] Fuzzy-matched image path: {resolved} -> {candidates[0]}",
                            file=sys.stderr,
                            flush=True,
                        )
                        return candidates[0]
                    elif len(candidates) > 1:
                        best = max(candidates, key=os.path.getmtime)
                        print(
                            f"[DEBUG] Fuzzy-matched image path (newest of {len(candidates)}): {resolved} -> {best}",
                            file=sys.stderr,
                            flush=True,
                        )
                        return best
                except OSError as e:
                    logger.debug(f"Failed to check file modification time: {e}")
            return resolved
        return url

    def extract_image_urls(self, text: str) -> tuple:
        """Extract image URLs from text/HTML/Markdown.

        Supports:
        - Markdown: ![alt text](URL) - extracts URL and alt text as caption
        - HTML: <img src="URL"/> - extracts URL, no caption
        - Bare URLs: https://example.com/image.png - extracts URL, no caption
        - Local paths: /ai-media/session/image.png (mapped to /tmp/webui_ai_media/)

        Returns:
            Tuple of (image_data, remaining_text) where:
            - image_data: List of (url, caption) tuples
            - remaining_text: Text with all image references removed
        """
        image_extensions = r'\.(jpg|jpeg|png|gif|webp|bmp|svg)(\?[^\s"<>]*)?'

        # Match markdown images: ![alt text](url)
        md_img_pattern = r"!\[([^\]]*)\]\(([^)]+)\)"
        # Match <img> tags
        img_tag_pattern = r'<img\s+[^>]*src=["\']([^"\']+)["\'][^>]*/?\s*>'
        # Match bare image URLs
        bare_url_pattern = r'(https?://[^\s"<>]+' + image_extensions + r")"

        image_data = []  # List of (url, caption) tuples
        remaining = text

        # Extract from markdown images FIRST (preserves alt text before bare URL pattern strips it)
        for match in re.finditer(md_img_pattern, remaining, re.IGNORECASE):
            alt_text = match.group(1).strip()
            url = self._resolve_image_path(match.group(2).strip())
            if url not in [img[0] for img in image_data]:
                image_data.append((url, alt_text))
            remaining = remaining.replace(match.group(0), "").strip()

        # Extract from <img> tags
        for match in re.finditer(img_tag_pattern, remaining, re.IGNORECASE):
            url = self._resolve_image_path(match.group(1).strip())
            if url not in [img[0] for img in image_data]:
                image_data.append((url, ""))  # Empty caption
            remaining = remaining.replace(match.group(0), "").strip()

        # Extract bare image URLs
        for match in re.finditer(bare_url_pattern, remaining, re.IGNORECASE):
            url = match.group(1)
            if url not in [img[0] for img in image_data]:
                image_data.append((url, ""))  # Empty caption
                remaining = remaining.replace(url, "").strip()

        return image_data, remaining

    def extract_file_paths(self, text: str) -> tuple:
        """Extract file paths from [FILE:...] markers.

        Supports:
        - [FILE:/path/to/file.ext] - file without caption
        - [FILE:/path/to/file.ext:Caption text] - file with caption

        Returns:
            Tuple of (file_data, remaining_text) where:
            - file_data: List of (path, caption) tuples
            - remaining_text: Text with all file references removed
        """
        # Match [FILE:path] or [FILE:path:caption]
        file_pattern = r"\[FILE:([^\]:]+)(?::([^\]]*))?\]"

        file_data = []  # List of (path, caption) tuples
        remaining = text

        for match in re.finditer(file_pattern, remaining):
            file_path = match.group(1).strip()
            caption = match.group(2).strip() if match.group(2) else ""

            # Validate path before adding
            if self._is_safe_file_path(file_path):
                file_data.append((file_path, caption))
                remaining = remaining.replace(match.group(0), "").strip()
            else:
                # Keep marker in text as error indicator
                print(f"[WARN] Unsafe file path rejected: {file_path}", file=sys.stderr)

        return file_data, remaining

    def send_response(
        self, chat_id: int, text: str, status_msg_id: Optional[int] = None
    ):
        """Send response, detecting image URLs and file paths."""
        print(
            f"[DEBUG OUTBOUND] send_response -> chat_id={chat_id} text_snippet={repr(text[:200])} status_msg_id={status_msg_id}",
            file=sys.stderr,
        )
        # Extract images first, then files from remaining text
        image_data, text_after_images = self.extract_image_urls(text)
        file_data, remaining_text = self.extract_file_paths(text_after_images)

        # Handle text portion
        if remaining_text.strip():
            if status_msg_id:
                print(
                    f"[DEBUG OUTBOUND] send_response editing status message {status_msg_id} for chat_id={chat_id}",
                    file=sys.stderr,
                )
                self.edit_message(chat_id, status_msg_id, remaining_text)
                status_msg_id = None
            else:
                print(
                    f"[DEBUG OUTBOUND] send_response sending text to chat_id={chat_id}",
                    file=sys.stderr,
                )
                self.send_message(chat_id, remaining_text)
        elif status_msg_id and (image_data or file_data):
            # No text, just media - delete status message
            try:
                resp = requests.post(
                    f"{self.api_url}/deleteMessage",
                    json={"chat_id": chat_id, "message_id": status_msg_id},
                    timeout=5,
                )
                print(
                    f"[DEBUG OUTBOUND] deleteMessage HTTP {resp.status_code} for chat_id={chat_id} message_id={status_msg_id}",
                    file=sys.stderr,
                )
            except Exception as e:
                print(f"[WARN] deleteMessage failed: {e}", file=sys.stderr)
            status_msg_id = None
        elif status_msg_id:
            # No text, no media - edit status message with default text
            print(
                f"[DEBUG OUTBOUND] send_response editing status message {status_msg_id} to checkmark for chat_id={chat_id}",
                file=sys.stderr,
            )
            self.edit_message(chat_id, status_msg_id, "✓")

        # Send images
        for url, caption in image_data:
            print(
                f"[DEBUG OUTBOUND] send_response sending photo URL={url} caption={repr(caption[:100])} to chat_id={chat_id}",
                file=sys.stderr,
                flush=True,
            )
            result = self.send_photo(chat_id, url, caption)
            print(
                f"[DEBUG OUTBOUND] send_photo returned: {result}",
                file=sys.stderr,
                flush=True,
            )

        # Send files
        for file_path, caption in file_data:
            print(
                f"[DEBUG OUTBOUND] send_response sending document path={file_path} caption={repr(caption[:100])} to chat_id={chat_id}",
                file=sys.stderr,
            )
            self.send_document(chat_id, file_path, caption)

    def download_file(self, file_id: str, user_id: int) -> Optional[str]:
        """Download file from Telegram and store it. Returns file path or None."""
        try:
            # Get file info
            file_response = requests.get(
                f"{self.api_url}/getFile", params={"file_id": file_id}, timeout=10
            )
            file_info = file_response.json()
            if not file_info.get("ok"):
                return None

            file_path = file_info["result"]["file_path"]

            # Create downloads directory in repo
            downloads_dir = Path("/opt/n8n-copilot-shim-dev/telegram_downloads")
            downloads_dir.mkdir(exist_ok=True)

            # Download file
            file_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
            file_response = requests.get(file_url, timeout=30)

            if file_response.status_code == 200:
                # Save file with user_id prefix
                file_name = Path(file_path).name
                local_path = downloads_dir / f"{user_id}_{file_name}"
                with open(local_path, "wb") as f:
                    f.write(file_response.content)

                # Make world readable
                import os

                os.chmod(local_path, 0o644)

                return str(local_path)
        except Exception as e:
            print(f"Error downloading file: {e}", file=sys.stderr)
        return None

    def cleanup_files(self, user_id: int):
        """Clean up downloaded files for user"""
        try:
            downloads_dir = Path("/opt/n8n-copilot-shim-dev/telegram_downloads")
            if downloads_dir.exists():
                for file in downloads_dir.glob(f"{user_id}_*"):
                    file.unlink()
        except Exception as e:
            print(f"Error cleaning up files: {e}", file=sys.stderr)

    def handle_message(self, update: Dict):
        """Process incoming Telegram message"""
        if not self._begin_active_request():
            print(
                "[INFO] Ignoring Telegram update while shutdown is in progress",
                file=sys.stderr,
            )
            return
        chat_id = None
        user_id = None
        try:
            message = update.get("message", {})
            sender = message.get("from") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            user_id = sender.get("id")

            # Ignore messages from bots (including this bot) to avoid handling bot-sent updates which can cause duplicate responses
            if sender.get("is_bot"):
                return

            if user_id is None or chat_id is None:
                print(
                    f"[DEBUG] Incomplete Telegram message: {message}",
                    file=sys.stderr,
                )
                return

            if not self.config.is_user_allowed(user_id):
                self.send_message(chat_id, "❌ You are not authorized to use this bot.")
                return

            # Handle files with optional caption
            file_path = None
            text = message.get("text", "") or message.get("caption", "")

            # Check for document or photo
            if "document" in message:
                document = message["document"]
                user_id = message["from"]["id"]
                chat_id = message["chat"]["id"]

                file_path = self.download_file(document["file_id"], user_id)
                print(
                    f"[DEBUG] Document detected, file_path: {file_path}",
                    file=sys.stderr,
                )
                if file_path:
                    if not text:
                        text = f"Please analyze this file: {file_path}"
                    else:
                        text = f"{text}\n\nFile to analyze: {file_path}"
                else:
                    self.send_message(chat_id, "Failed to download file")
                    return
            elif "photo" in message:
                # Telegram sends photos as arrays, get largest
                photos = message["photo"]
                user_id = message["from"]["id"]
                chat_id = message["chat"]["id"]

                largest_photo = photos[-1]  # Last is highest quality
                file_path = self.download_file(largest_photo["file_id"], user_id)
                print(
                    f"[DEBUG] Photo detected, file_path: {file_path}", file=sys.stderr
                )
                if file_path:
                    if not text:
                        text = f"Please analyze this image: {file_path}"
                    else:
                        text = f"{text}\n\nImage to analyze: {file_path}"
                else:
                    self.send_message(chat_id, "Failed to download image")
                    return
            elif "voice" in message or "audio" in message or "video_note" in message:
                # Voice messages, audio files, and video notes → transcribe
                user_id = message["from"]["id"]
                chat_id = message["chat"]["id"]

                if "voice" in message:
                    file_id = message["voice"]["file_id"]
                    duration = message["voice"].get("duration", 0)
                    media_type = "voice message"
                elif "audio" in message:
                    file_id = message["audio"]["file_id"]
                    duration = message["audio"].get("duration", 0)
                    media_type = "audio"
                else:  # video_note
                    file_id = message["video_note"]["file_id"]
                    duration = message["video_note"].get("duration", 0)
                    media_type = "video note"

                print(
                    f"[DEBUG] {media_type} detected ({duration}s), downloading...",
                    file=sys.stderr,
                )
                audio_path = self.download_file(file_id, user_id)

                if audio_path:
                    # Transcribe the audio
                    transcribed, backend = audio_transcriber.transcribe(audio_path)
                    if transcribed:
                        # Use caption if provided, otherwise use transcription as the query
                        if text:
                            text = f"{text}\n\n[Voice transcription ({backend})]: {transcribed}"
                        else:
                            text = transcribed
                        print(
                            f"[DEBUG] Transcribed {media_type} via {backend}: {text[:100]}...",
                            file=sys.stderr,
                        )
                    else:
                        self.send_message(
                            chat_id,
                            f"⚠️ Could not transcribe {media_type}. Please send as text instead.",
                        )
                        return
                else:
                    self.send_message(chat_id, f"Failed to download {media_type}")
                    return
            elif not text:
                # No text, no file - ignore
                return

            user_name = sender.get("username", f"user_{user_id}")
            # text already set from above (may be from file or message.text)

            # Get or create user session
            session_info = self.config.get_user_session(user_id)
            if not session_info:
                # Create new session - use pinned agent if configured
                default_agent = self.config.config["default_agent"]
                pinned_agent = self.config.get_pinned_agent(user_id)
                if pinned_agent:
                    default_agent = pinned_agent
                session_info = {
                    "user_id": user_id,
                    "username": user_name,
                    "paired_at": datetime.now().isoformat(),
                    "agent": default_agent,
                    "model": self.config.config["default_model"],
                    "render_type": "telegram_html",  # Default render type with markdown image support
                }
                self.config.set_user_session(user_id, session_info)
            elif self.config.is_user_pinned(user_id):
                # Enforce pinned agent even for existing sessions
                pinned_agent = self.config.get_pinned_agent(user_id)
                if pinned_agent and session_info.get("agent") != pinned_agent:
                    session_info["agent"] = pinned_agent
                    self.config.set_user_session(user_id, session_info)

            # Handle slash commands via agent_manager
            if self.bot_id:
                session_id = f"telegram_{self.bot_id}_{user_id}"
            else:
                session_id = f"telegram_{user_id}"

            # Push pinned agent/runtime/model into SessionManager before every message
            self._enforce_pinned_session(user_id, session_id)

            # Show typing indicator
            self.send_typing(chat_id)

            if text.startswith("/"):
                cmd_lower = text.lower().strip()
                # Check for timeout command
                if cmd_lower.startswith("/timeout"):
                    parts = text.split()
                    if len(parts) == 1 or (
                        len(parts) > 1 and parts[1].lower() == "current"
                    ):
                        # Get current timeout
                        current = self.config.get_user_timeout(user_id)
                        response = f"Current timeout: {current} seconds"
                    elif len(parts) > 1 and parts[1].lower() == "set":
                        # Set timeout
                        if len(parts) < 3:
                            response = "Invalid timeout. Use: /timeout set <seconds>"
                        else:
                            try:
                                new_timeout = int(parts[2])
                                if new_timeout < 30:
                                    response = "Timeout must be at least 30 seconds"
                                elif new_timeout > 3600:
                                    response = (
                                        "Timeout must be at most 3600 seconds (1 hour)"
                                    )
                                else:
                                    self.config.set_user_timeout(user_id, new_timeout)
                                    response = (
                                        f"✅ Timeout set to {new_timeout} seconds"
                                    )
                            except ValueError:
                                response = (
                                    "Invalid timeout. Use: /timeout set <seconds>"
                                )
                    else:
                        response = "Invalid timeout command. Use: /timeout current or /timeout set <seconds>"
                    self.send_message(chat_id, response)
                # Block pinned users from changing their agent
                elif cmd_lower.startswith("/agent set") and self.config.is_user_pinned(
                    user_id
                ):
                    pinned_agent = self.config.get_pinned_agent(user_id)
                    self.send_message(
                        chat_id,
                        f"❌ Your agent is pinned to **{pinned_agent}** by an administrator. You cannot change agents.",
                    )
                # Block pinned users from changing their runtime (if a runtime is pinned)
                elif cmd_lower.startswith(
                    "/runtime set"
                ) and self.config.get_pinned_runtime(user_id):
                    pinned_runtime = self.config.get_pinned_runtime(user_id)
                    self.send_message(
                        chat_id,
                        f"❌ Your runtime is pinned to **{pinned_runtime}** by an administrator. You cannot change runtimes.",
                    )
                # Block pinned users from changing their model (if a model is pinned)
                elif cmd_lower.startswith(
                    "/model set"
                ) and self.config.get_pinned_model(user_id):
                    pinned_model = self.config.get_pinned_model(user_id)
                    self.send_message(
                        chat_id,
                        f"❌ Your model is pinned to **{pinned_model}** by an administrator. You cannot change models.",
                    )
                # Block unauthorized users from enabling yolo mode
                elif cmd_lower.startswith(
                    "/mode yolo"
                ) and not self.config.is_yolo_allowed(user_id):
                    self.send_message(
                        chat_id, "❌ You are not authorized to enable YOLO mode."
                    )
                # F016: /registercommands — re-register with BotFather
                elif cmd_lower == "/registercommands":
                    result = self.register_bot_commands()
                    self.send_message(chat_id, result)
                else:
                    # Regular slash commands - get user timeout
                    timeout = self.config.get_user_timeout(user_id)
                    response = self._execute_command(
                        text, session_id, timeout, user_identity=str(user_id)
                    )
                    msg_id = self.send_message(chat_id, response)

                    # Pin agent set messages so user always knows which agent they're talking to
                    if cmd_lower.startswith("/agent set") and msg_id:
                        self.unpin_all_messages(chat_id)
                        self.pin_message(chat_id, msg_id)

                    # Evict cached SessionManager on session-affecting commands
                    if cmd_lower.startswith("/session reset") or cmd_lower.startswith(
                        "/runtime set"
                    ):
                        self._evict_session_manager(session_id)
            else:
                # Check for bash command (!)
                if text.startswith("!"):
                    # Bash commands - get user timeout
                    timeout = self.config.get_user_timeout(user_id)
                    response = self._execute_command(
                        text, session_id, timeout, user_identity=str(user_id)
                    )
                    self.send_message(chat_id, response)
                else:
                    # Route regular messages to agent_manager with status updates
                    timeout = self.config.get_user_timeout(user_id)
                    response, status_msg_id = self._query_agent_with_status(
                        text,
                        session_info["agent"],
                        session_info["model"],
                        user_id,
                        chat_id,
                        timeout,
                    )
                    self.send_response(chat_id, response, status_msg_id)

            # Cleanup temp files after query
            self.cleanup_files(user_id)

        except Exception as e:
            print(f"Error handling message: {e}", file=sys.stderr)
            if chat_id is not None:
                self.send_message(chat_id, f"❌ Error: {str(e)[:100]}")
        finally:
            self._finish_active_request()

    def _handle_command(self, chat_id: int, user_id: int, command: str):
        """Handle Telegram commands - DEPRECATED: Commands now pass to agent_manager"""
        pass  # Commands are now routed to agent_manager

    def _execute_command(
        self,
        command: str,
        session_id: str,
        timeout: int = 300,
        user_identity: str = None,
    ) -> str:
        """Execute slash command via agent_manager.execute() with timeout support"""
        # Container for result and thread control
        result_container = {"response": None, "done": False}
        effective_identity = user_identity or session_id

        def run_command():
            """Run command in background thread"""
            try:
                if self.use_api:
                    result_container["response"] = self._execute_via_api(
                        command, session_id, effective_identity, "telegram"
                    )
                else:
                    session_mgr = self.get_session_manager(session_id)
                    result_container["response"] = session_mgr.execute(
                        command, session_id
                    )
                result_container["done"] = True
            except Exception as e:
                import traceback

                tb_str = traceback.format_exc()
                print(f"Error in _execute_command: {tb_str}", file=sys.stderr)
                result_container["response"] = f"Error: {str(e)[:150]}"
                result_container["done"] = True

        # Start command in background
        cmd_thread = threading.Thread(target=run_command, daemon=True)
        cmd_thread.start()

        # Wait for result with timeout
        elapsed = 0
        while not result_container["done"] and elapsed < timeout:
            time.sleep(1)
            elapsed += 1

        # Wait for thread to complete
        cmd_thread.join(timeout=5)

        return result_container["response"] or "Error: Command timed out"

    def _poll_live_status(self, session_id: str) -> Optional[str]:
        """Poll the live-status endpoint for real-time LLM progress (F004).

        Returns the latest status text, or None if no live status is available.
        """
        try:
            headers = {
                "Authorization": f"Bearer shared_{self.api_shared_key}",
            }
            resp = requests.get(
                f"{self.api_url_copilot}/api/v1/sessions/{session_id}/live-status",
                headers=headers,
                timeout=3,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("status")
        except Exception:
            pass
        return None

    def _query_agent_with_status(
        self,
        query: str,
        agent: str,
        model: str,
        user_id: int,
        chat_id: int,
        timeout: int = 300,
    ) -> tuple:
        """Query agent with live status updates at 30s intervals.

        Polls the live-status API endpoint for LLM-generated progress updates
        (F004). Falls back to static messages if no live status is available.

        Returns (response_text, status_msg_id) where status_msg_id is the
        message to edit with the final response, or None if no status was sent.
        """
        # Container for result and thread control
        result_container = {"response": None, "done": False}

        # Determine session ID for live-status polling
        if self.bot_id:
            _poll_session_id = f"telegram_{self.bot_id}_{user_id}"
        else:
            _poll_session_id = f"telegram_{user_id}"

        # Fallback static status messages (used when no live status from LLM)
        status_msgs = [
            "Still working on it...",
            "Sorry it's taking so long, still working on it...",
            "Still processing, hang tight...",
            "Almost there, still working...",
            "Continuing to work on this...",
        ]

        def run_query():
            """Run query in background thread"""
            print(f"[DEBUG] Query to agent: {query[:200]}", file=sys.stderr)
            result_container["response"] = self._query_agent(
                query, agent, model, user_id, timeout
            )
            result_container["done"] = True

        # Start query in background
        query_thread = threading.Thread(target=run_query, daemon=True)
        query_thread.start()

        # Wait for result with status updates
        elapsed = 0
        status_idx = 0
        status_msg_id = None
        _last_live_status = None  # Track last live status to avoid redundant edits
        while not result_container["done"] and elapsed < timeout:
            # Re-send typing every 5 seconds to keep indicator alive
            if elapsed % 5 == 0:
                self.send_typing(chat_id)

            if elapsed >= 30 and (elapsed - 30) % 10 == 0:
                # Poll live-status endpoint for LLM-generated progress
                live_status = self._poll_live_status(_poll_session_id)

                if live_status and live_status != _last_live_status:
                    # Use LLM-generated status update
                    msg = f"⚙️ {live_status}"
                    _last_live_status = live_status
                elif elapsed == 30 or (elapsed > 30 and (elapsed - 30) % 30 == 0):
                    # Fall back to static message on 30s boundaries
                    msg = status_msgs[status_idx % len(status_msgs)]
                    status_idx += 1
                else:
                    msg = None

                if msg:
                    if status_msg_id:
                        self.edit_message(chat_id, status_msg_id, msg)
                    else:
                        status_msg_id = self.send_message(chat_id, msg)
                    self.send_typing(chat_id)

            time.sleep(1)
            elapsed += 1

        # Wait for thread to complete (with timeout)
        query_thread.join(timeout=5)

        return (result_container["response"] or "Error: Query timed out", status_msg_id)

    def _query_agent(
        self, query: str, agent: str, model: str, user_id: int, timeout: int = 300
    ) -> str:
        """Query the agent_manager with user session tied to user ID"""
        try:
            # Session ID tied to user ID and bot id (if available)
            if self.bot_id:
                session_id = f"telegram_{self.bot_id}_{user_id}"
            else:
                session_id = f"telegram_{user_id}"

            # Use persistent session manager
            session_mgr = self.get_session_manager(session_id)

            # Debug: log session info
            print(
                f"[DEBUG] Using persistent session_mgr for: {session_id}",
                file=sys.stderr,
            )

            # Use execute() which routes to the correct runtime automatically
            if self.use_api:
                result = self._execute_via_api(
                    query, session_id, str(user_id), "telegram"
                )
            else:
                result = session_mgr.execute(query, session_id)

            return result if result else "No response from agent"
        except Exception as e:
            import traceback

            tb_str = traceback.format_exc()
            print(f"Error in _query_agent: {tb_str}", file=sys.stderr)
            return f"Error: {str(e)[:150]}"

    def run(self, poll_interval: int = 1):
        """Start polling for messages"""
        self._install_signal_handlers()
        self.shutdown_event.clear()
        self.running = True
        print(f"Starting Telegram connector with token: {self.token[:20]}...")

        # F016: Register slash commands with BotFather for autocomplete
        reg_result = self.register_bot_commands()
        print(f"[F016] {reg_result}", file=sys.stderr)

        try:
            while self.running:
                updates = self.get_updates()
                for update in updates:
                    if self.shutdown_event.is_set():
                        break
                    if "message" in update:
                        self.handle_message(update)
                    elif "callback_query" in update:
                        # Handle button callbacks if needed
                        pass

                if not updates and self.running:
                    time.sleep(poll_interval)

        except KeyboardInterrupt:
            self._request_shutdown("KeyboardInterrupt")
        finally:
            self._wait_for_active_requests()

    def stop(self):
        """Stop the connector"""
        self._request_shutdown("stop()")
        self._wait_for_active_requests()


def main():
    """Main entry point for Telegram connector"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Telegram connector for N8N Copilot Shim"
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        help="Telegram bot token (or use TELEGRAM_BOT_TOKEN env var)",
    )
    parser.add_argument(
        "--config",
        default="telegram_config.json",
        help="Configuration file path",
    )
    parser.add_argument(
        "--allow-user",
        type=int,
        help="Allow a user ID to chat",
    )
    parser.add_argument(
        "--deny-user",
        type=int,
        help="Deny a user ID from chatting",
    )
    parser.add_argument(
        "--list-users",
        action="store_true",
        help="List allowed users",
    )

    args = parser.parse_args()

    # Initialize config
    config = TelegramConfig(args.config)

    # Handle config commands
    if args.allow_user:
        config.allow_user(args.allow_user)
        print(f"✅ User {args.allow_user} allowed")
        return

    if args.deny_user:
        config.deny_user(args.deny_user)
        print(f"✅ User {args.deny_user} denied")
        return

    if args.list_users:
        allowed = config.config.get("allowed_users", [])
        print(f"Allowed users: {allowed if allowed else 'None (all users allowed)'}")
        return

    # Start connector
    if not args.token:
        print(
            "Error: Telegram bot token required (--token or TELEGRAM_BOT_TOKEN env var)"
        )
        sys.exit(1)

    connector = TelegramConnector(args.token, args.config)
    connector.run()


if __name__ == "__main__":
    main()
