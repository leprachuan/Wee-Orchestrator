#!/usr/bin/env python3
"""
Agent Bot Manager — Per-agent Telegram/WebEx bots for Wee Orchestrator.

Reads `agents.json`, resolves bot tokens from keyring (via secret_tool.py),
and starts dedicated polling threads for each agent that has a `bots` config.
Messages are routed directly to the assigned agent — no orchestrator hop.

Hot-reloads when agents.json changes (file mtime polling).

Usage:
    python3 agent_bot_manager.py [--config agents.json] [--poll-interval 30]
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger("agent_bot_manager")

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_AGENTS_JSON = SCRIPT_DIR / "agents.json"
SECRET_TOOL_PATH = SCRIPT_DIR / "secret_tool" / "secret_tool.py"

# API defaults
DEFAULT_API_URL = os.getenv("API_URL", "https://127.0.0.1:8000")
DEFAULT_API_SHARED_KEY = os.getenv(
    "API_SHARED_KEY", "R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU"
)


def resolve_secret(secret_name: str) -> Optional[str]:
    """Resolve a secret value from keyring via secret_tool.py.

    Returns the secret value or None if resolution fails.
    Never logs the actual secret value.
    """
    if not SECRET_TOOL_PATH.exists():
        logger.error("secret_tool.py not found at %s", SECRET_TOOL_PATH)
        return None
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(SECRET_TOOL_PATH),
                "get",
                "--name",
                secret_name,
                "--backend",
                "pass",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            try:
                data = json.loads(output)
                if data.get("status") == "success":
                    return data["value"]
            except json.JSONDecodeError:
                if output:
                    return output
        logger.warning(
            "Failed to resolve secret '%s': exit=%d stderr=%s",
            secret_name,
            result.returncode,
            result.stderr.strip()[:200],
        )
    except Exception as exc:
        logger.warning("Exception resolving secret '%s': %s", secret_name, exc)
    return None


class TelegramAgentBot:
    """Dedicated Telegram bot for a single agent.

    Polls Telegram getUpdates, routes every message to the fixed agent via
    the Wee Orchestrator API, and sends responses back.
    """

    def __init__(
        self,
        agent_name: str,
        token: str,
        allowed_users: Optional[List[str]] = None,
        api_url: str = DEFAULT_API_URL,
        api_shared_key: str = DEFAULT_API_SHARED_KEY,
    ):
        self.agent_name = agent_name
        self.token = token
        self.api_url_base = f"https://api.telegram.org/bot{token}"
        self.api_url = api_url
        self.api_shared_key = api_shared_key
        self.allowed_users = [int(u) for u in allowed_users] if allowed_users else []
        self.offset = 0
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self.bot_info: Optional[Dict] = None

    def _get_me(self) -> Optional[Dict]:
        """Fetch bot identity from Telegram."""
        try:
            resp = requests.get(f"{self.api_url_base}/getMe", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                return data.get("result")
        except Exception as exc:
            logger.warning("[%s] Failed to fetch bot info: %s", self.agent_name, exc)
        return None

    def _get_updates(self, timeout: int = 30) -> List[Dict]:
        """Long-poll Telegram for new updates."""
        try:
            resp = requests.get(
                f"{self.api_url_base}/getUpdates",
                params={"offset": self.offset, "timeout": timeout},
                timeout=timeout + 5,
            )
            resp.raise_for_status()
            updates = resp.json().get("result", [])
            if updates:
                self.offset = updates[-1]["update_id"] + 1
            return updates
        except Exception as exc:
            logger.warning("[%s] Error fetching updates: %s", self.agent_name, exc)
            return []

    def _send_message(self, chat_id: int, text: str) -> Optional[int]:
        """Send a message back to the Telegram chat. Returns message_id."""
        try:
            sanitized = self._sanitize_html(text)
            max_len = 4096
            chunks = (
                [sanitized[i : i + max_len] for i in range(0, len(sanitized), max_len)]
                if sanitized
                else ["No response"]
            )

            last_msg_id = None
            for chunk in chunks:
                resp = requests.post(
                    f"{self.api_url_base}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": chunk,
                        "parse_mode": "HTML",
                    },
                    timeout=30,
                )
                if resp.status_code == 200:
                    last_msg_id = resp.json().get("result", {}).get("message_id")
                else:
                    # Retry without HTML parse mode
                    resp2 = requests.post(
                        f"{self.api_url_base}/sendMessage",
                        json={"chat_id": chat_id, "text": chunk},
                        timeout=30,
                    )
                    if resp2.status_code == 200:
                        last_msg_id = resp2.json().get("result", {}).get("message_id")
            return last_msg_id
        except Exception as exc:
            logger.error("[%s] Failed to send message: %s", self.agent_name, exc)
            return None

    def _edit_message(self, chat_id: int, message_id: int, text: str) -> bool:
        """Edit an existing Telegram message."""
        try:
            sanitized = self._sanitize_html(text)
            if len(sanitized) > 4096:
                sanitized = sanitized[:4096]
            resp = requests.post(
                f"{self.api_url_base}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": sanitized,
                    "parse_mode": "HTML",
                },
                timeout=30,
            )
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("[%s] Failed to edit message: %s", self.agent_name, exc)
            return False

    @staticmethod
    def _sanitize_html(text: str) -> str:
        """Sanitize HTML to Telegram-supported tags only."""
        if not text:
            return "No response"
        supported = (
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
        )
        pattern = r"(</?(?:" + "|".join(supported) + r")(?:\s[^>]*)?>)"
        parts = re.split(pattern, text)
        result = []
        for i, part in enumerate(parts):
            if i % 2 == 0:
                part = (
                    part.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                )
            result.append(part)
        return "".join(result)

    def _is_user_allowed(self, user_id: int) -> bool:
        """Check if user is allowed. Empty list = all allowed."""
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users

    def _execute_via_api(self, query: str, session_id: str, user_identity: str) -> str:
        """Execute query via the Wee Orchestrator API, pinned to self.agent_name."""
        try:
            headers = {
                "Authorization": f"Bearer shared_{self.api_shared_key}",
                "Content-Type": "application/json",
                "X-User-Identity": user_identity,
                "X-Auth-Channel": "telegram",
            }
            # Create session pinned to our agent
            requests.post(
                f"{self.api_url}/api/v1/sessions/create",
                headers=headers,
                json={
                    "session_id": session_id,
                    "agent": self.agent_name,
                },
                timeout=10,
                verify=False,
            )
            # Execute the query
            resp = requests.post(
                f"{self.api_url}/api/v1/sessions/{session_id}/execute",
                headers=headers,
                json={
                    "query": query,
                    "agent": self.agent_name,
                },
                timeout=600,
                verify=False,
            )
            if resp.status_code == 200:
                return resp.json().get("response", "No response from API")
            else:
                logger.warning(
                    "[%s] API returned %d: %s",
                    self.agent_name,
                    resp.status_code,
                    resp.text[:200],
                )
                return f"⚠️ Error communicating with agent ({resp.status_code})"
        except Exception as exc:
            logger.error("[%s] API exception: %s", self.agent_name, exc)
            return f"⚠️ Agent temporarily unavailable: {exc}"

    def _handle_slash_command(
        self, text: str, chat_id: int, user_id: int
    ) -> Optional[str]:
        """Handle bot-level slash commands. Returns response or None to pass through."""
        if not text.startswith("/"):
            return None

        cmd = text.split()[0].lower()
        # Strip @botname suffix from commands
        if "@" in cmd:
            cmd = cmd.split("@")[0]

        if cmd == "/start":
            bot_name = (
                self.bot_info.get("first_name", self.agent_name)
                if self.bot_info
                else self.agent_name
            )
            return (
                f"👋 Hi! I'm <b>{bot_name}</b>, your dedicated "
                f"<b>{self.agent_name}</b> agent.\n\n"
                f"Send me any message and I'll handle it directly — "
                f"no orchestrator needed.\n\n"
                f"Commands:\n"
                f"/help — Show this help\n"
                f"/status — Check agent status\n"
                f"/model &lt;name&gt; — Change AI model"
            )
        elif cmd == "/help":
            return (
                f"🤖 <b>{self.agent_name}</b> agent bot\n\n"
                f"/start — Welcome message\n"
                f"/help — This help\n"
                f"/status — Agent status\n"
                f"/model &lt;name&gt; — Change AI model\n\n"
                f"All other messages are sent directly to the "
                f"<b>{self.agent_name}</b> agent."
            )
        elif cmd == "/status":
            return (
                f"✅ <b>{self.agent_name}</b> agent is online\n" f"API: {self.api_url}"
            )
        elif cmd in ("/agent", "/agent_set"):
            return (
                f"⚠️ Agent switching is disabled on per-agent bots. "
                f"This bot is dedicated to <b>{self.agent_name}</b>."
            )
        # /model passes through to the API
        return None

    def _handle_message(self, update: Dict):
        """Process a single Telegram update."""
        message = update.get("message", {})
        if not message:
            return

        # Ignore bot messages
        if message.get("from", {}).get("is_bot"):
            return

        text = message.get("text", "") or message.get("caption", "")
        if not text:
            return

        user_id = message["from"]["id"]
        chat_id = message["chat"]["id"]

        if not self._is_user_allowed(user_id):
            self._send_message(chat_id, "❌ You are not authorized to use this bot.")
            return

        # Check slash commands handled locally
        slash_response = self._handle_slash_command(text, chat_id, user_id)
        if slash_response is not None:
            self._send_message(chat_id, slash_response)
            return

        # Send "typing" indicator
        try:
            requests.post(
                f"{self.api_url_base}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
                timeout=5,
            )
        except Exception:
            pass

        # Send "working on it" placeholder
        placeholder_id = self._send_message(chat_id, "⏳ Working on it...")

        # Build session ID
        session_id = f"tg_{self.agent_name}_{user_id}"
        user_identity = str(user_id)

        # Execute via API
        response = self._execute_via_api(
            query=text, session_id=session_id, user_identity=user_identity
        )

        # Edit placeholder with response or send new message
        if placeholder_id and response:
            success = self._edit_message(chat_id, placeholder_id, response)
            if not success:
                self._send_message(chat_id, response)
        elif response:
            self._send_message(chat_id, response)

    def start(self):
        """Start polling in a background thread."""
        if self.running:
            return
        self.running = True
        self.bot_info = self._get_me()
        bot_username = (
            self.bot_info.get("username", "unknown") if self.bot_info else "unknown"
        )
        logger.info(
            "Starting Telegram bot for agent '%s' (@%s)",
            self.agent_name,
            bot_username,
        )
        self._thread = threading.Thread(
            target=self._poll_loop,
            name=f"telegram-{self.agent_name}",
            daemon=True,
        )
        self._thread.start()

    def _poll_loop(self):
        """Main polling loop."""
        while self.running:
            try:
                updates = self._get_updates(timeout=30)
                for update in updates:
                    try:
                        self._handle_message(update)
                    except Exception as exc:
                        logger.error(
                            "[%s] Error handling update: %s",
                            self.agent_name,
                            exc,
                        )
                if not updates:
                    time.sleep(1)
            except Exception as exc:
                logger.error("[%s] Poll loop error: %s", self.agent_name, exc)
                time.sleep(5)

    def stop(self):
        """Stop the polling thread."""
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("Stopped Telegram bot for agent '%s'", self.agent_name)


class WebExAgentBot:
    """Dedicated WebEx bot for a single agent.

    Listens on a RabbitMQ queue for WebEx messages and routes them to
    the fixed agent via the Wee Orchestrator API.
    """

    def __init__(
        self,
        agent_name: str,
        token: str,
        rabbitmq_host: str = "192.168.0.85",
        rabbitmq_port: int = 5672,
        queue_name: Optional[str] = None,
        api_url: str = DEFAULT_API_URL,
        api_shared_key: str = DEFAULT_API_SHARED_KEY,
    ):
        self.agent_name = agent_name
        self.token = token
        self.rabbitmq_host = rabbitmq_host
        self.rabbitmq_port = rabbitmq_port
        self.queue_name = queue_name or f"webex-agent-{agent_name}"
        self.api_url = api_url
        self.api_shared_key = api_shared_key
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._connection = None
        self._channel = None

    def _send_webex_message(self, room_id: str, text: str) -> Optional[str]:
        """Send message via WebEx API."""
        try:
            resp = requests.post(
                "https://webexapis.com/v1/messages",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                json={"roomId": room_id, "markdown": text},
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json().get("id")
            logger.warning(
                "[%s] WebEx send failed (%d): %s",
                self.agent_name,
                resp.status_code,
                resp.text[:200],
            )
        except Exception as exc:
            logger.error("[%s] WebEx send error: %s", self.agent_name, exc)
        return None

    def _execute_via_api(self, query: str, session_id: str, user_identity: str) -> str:
        """Execute query via the Wee Orchestrator API, pinned to agent."""
        try:
            headers = {
                "Authorization": f"Bearer shared_{self.api_shared_key}",
                "Content-Type": "application/json",
                "X-User-Identity": user_identity,
                "X-Auth-Channel": "webex",
            }
            requests.post(
                f"{self.api_url}/api/v1/sessions/create",
                headers=headers,
                json={
                    "session_id": session_id,
                    "agent": self.agent_name,
                },
                timeout=10,
                verify=False,
            )
            resp = requests.post(
                f"{self.api_url}/api/v1/sessions/{session_id}/execute",
                headers=headers,
                json={
                    "query": query,
                    "agent": self.agent_name,
                },
                timeout=600,
                verify=False,
            )
            if resp.status_code == 200:
                return resp.json().get("response", "No response from API")
            logger.warning(
                "[%s] API returned %d: %s",
                self.agent_name,
                resp.status_code,
                resp.text[:200],
            )
            return f"⚠️ Error communicating with agent ({resp.status_code})"
        except Exception as exc:
            logger.error("[%s] API exception: %s", self.agent_name, exc)
            return f"⚠️ Agent temporarily unavailable: {exc}"

    def _handle_message(self, message_data: Dict):
        """Process a WebEx message."""
        room_id = message_data.get("roomId", "")
        person_id = message_data.get("personId", "")
        text = message_data.get("text", "")

        if not text or not room_id:
            return

        # Check /agent command — block it
        if text.strip().lower().startswith("/agent"):
            self._send_webex_message(
                room_id,
                f"⚠️ Agent switching is disabled. This bot is dedicated to "
                f"**{self.agent_name}**.",
            )
            return

        session_id = f"wx_{self.agent_name}_{person_id}"
        response = self._execute_via_api(
            query=text, session_id=session_id, user_identity=person_id
        )
        if response:
            self._send_webex_message(room_id, response)

    def _connect_rabbitmq(self) -> bool:
        """Connect to RabbitMQ and declare the queue."""
        try:
            import pika

            credentials = pika.PlainCredentials("guest", "guest")
            params = pika.ConnectionParameters(
                host=self.rabbitmq_host,
                port=self.rabbitmq_port,
                credentials=credentials,
                heartbeat=600,
                blocked_connection_timeout=300,
            )
            self._connection = pika.BlockingConnection(params)
            self._channel = self._connection.channel()
            self._channel.queue_declare(queue=self.queue_name, durable=True)
            logger.info(
                "[%s] Connected to RabbitMQ, queue=%s",
                self.agent_name,
                self.queue_name,
            )
            return True
        except Exception as exc:
            logger.error("[%s] RabbitMQ connection failed: %s", self.agent_name, exc)
            return False

    def _consume_loop(self):
        """Consume messages from RabbitMQ."""
        while self.running:
            try:
                if not self._connection or self._connection.is_closed:
                    if not self._connect_rabbitmq():
                        time.sleep(10)
                        continue

                def callback(ch, method, properties, body):
                    try:
                        message_data = json.loads(body)
                        self._handle_message(message_data)
                    except Exception as exc:
                        logger.error(
                            "[%s] Message handling error: %s",
                            self.agent_name,
                            exc,
                        )
                    finally:
                        ch.basic_ack(delivery_tag=method.delivery_tag)

                self._channel.basic_qos(prefetch_count=1)
                self._channel.basic_consume(
                    queue=self.queue_name, on_message_callback=callback
                )
                self._channel.start_consuming()
            except Exception as exc:
                logger.error("[%s] Consume loop error: %s", self.agent_name, exc)
                time.sleep(5)

    def start(self):
        """Start consuming in a background thread."""
        if self.running:
            return
        self.running = True
        logger.info(
            "Starting WebEx bot for agent '%s' (queue=%s)",
            self.agent_name,
            self.queue_name,
        )
        self._thread = threading.Thread(
            target=self._consume_loop,
            name=f"webex-{self.agent_name}",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        """Stop consuming."""
        self.running = False
        try:
            if self._channel:
                self._channel.stop_consuming()
            if self._connection and not self._connection.is_closed:
                self._connection.close()
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("Stopped WebEx bot for agent '%s'", self.agent_name)


class AgentBotManager:
    """Manages per-agent Telegram and WebEx bot threads.

    Reads agents.json, resolves tokens, starts bots, and hot-reloads
    when the config file changes.
    """

    def __init__(
        self,
        agents_json_path: str = str(DEFAULT_AGENTS_JSON),
        api_url: str = DEFAULT_API_URL,
        api_shared_key: str = DEFAULT_API_SHARED_KEY,
        reload_interval: int = 30,
    ):
        self.agents_json_path = Path(agents_json_path)
        self.api_url = api_url
        self.api_shared_key = api_shared_key
        self.reload_interval = reload_interval
        self._telegram_bots: Dict[str, TelegramAgentBot] = {}
        self._webex_bots: Dict[str, WebExAgentBot] = {}
        self._last_mtime: float = 0.0
        self._running = False
        self._lock = threading.Lock()

    def _load_agents(self) -> List[Dict]:
        """Load agents from agents.json."""
        try:
            with open(self.agents_json_path) as f:
                data = json.load(f)
            return data.get("agents", [])
        except Exception as exc:
            logger.error("Failed to load %s: %s", self.agents_json_path, exc)
            return []

    def _get_bot_configs(self, agents: List[Dict]) -> Dict[str, Dict]:
        """Extract bot configs keyed by agent name."""
        configs = {}
        for agent in agents:
            name = agent.get("name", "")
            bots = agent.get("bots")
            if bots and isinstance(bots, dict):
                configs[name] = {
                    "bots": bots,
                    "path": agent.get("path", ""),
                }
        return configs

    def _sync_bots(self, configs: Dict[str, Dict]):
        """Start/stop bots to match current config."""
        with self._lock:
            current_tg = set(self._telegram_bots.keys())
            current_wx = set(self._webex_bots.keys())

            desired_tg = set()
            desired_wx = set()

            for agent_name, cfg in configs.items():
                bots = cfg["bots"]
                if "telegram" in bots:
                    desired_tg.add(agent_name)
                if "webex" in bots:
                    desired_wx.add(agent_name)

            # Stop removed Telegram bots
            for name in current_tg - desired_tg:
                logger.info("Stopping removed Telegram bot: %s", name)
                self._telegram_bots[name].stop()
                del self._telegram_bots[name]

            # Stop removed WebEx bots
            for name in current_wx - desired_wx:
                logger.info("Stopping removed WebEx bot: %s", name)
                self._webex_bots[name].stop()
                del self._webex_bots[name]

            # Start new Telegram bots
            for name in desired_tg - current_tg:
                tg_cfg = configs[name]["bots"]["telegram"]
                token_secret = tg_cfg.get("token_secret", "")
                if not token_secret:
                    logger.warning(
                        "Agent '%s' has telegram bot config but no token_secret",
                        name,
                    )
                    continue
                token = resolve_secret(token_secret)
                if not token:
                    logger.warning(
                        "Failed to resolve token for agent '%s' "
                        "(secret: %s) — skipping",
                        name,
                        token_secret,
                    )
                    continue
                allowed = tg_cfg.get("allowed_users")
                bot = TelegramAgentBot(
                    agent_name=name,
                    token=token,
                    allowed_users=allowed,
                    api_url=self.api_url,
                    api_shared_key=self.api_shared_key,
                )
                bot.start()
                self._telegram_bots[name] = bot

            # Start new WebEx bots
            for name in desired_wx - current_wx:
                wx_cfg = configs[name]["bots"]["webex"]
                token_secret = wx_cfg.get("token_secret", "")
                if not token_secret:
                    logger.warning(
                        "Agent '%s' has webex bot config but no token_secret",
                        name,
                    )
                    continue
                token = resolve_secret(token_secret)
                if not token:
                    logger.warning(
                        "Failed to resolve token for agent '%s' "
                        "(secret: %s) — skipping",
                        name,
                        token_secret,
                    )
                    continue
                queue_name = wx_cfg.get("queue_name")
                bot = WebExAgentBot(
                    agent_name=name,
                    token=token,
                    queue_name=queue_name,
                    api_url=self.api_url,
                    api_shared_key=self.api_shared_key,
                )
                bot.start()
                self._webex_bots[name] = bot

    def _check_reload(self):
        """Check if agents.json changed and reload if so."""
        try:
            mtime = self.agents_json_path.stat().st_mtime
        except OSError:
            return
        if mtime > self._last_mtime:
            logger.info("agents.json changed — reloading bot configs")
            self._last_mtime = mtime
            agents = self._load_agents()
            configs = self._get_bot_configs(agents)
            self._sync_bots(configs)

    def start(self):
        """Start the manager: load initial config and begin watching."""
        self._running = True
        logger.info(
            "AgentBotManager starting (config=%s, reload_interval=%ds)",
            self.agents_json_path,
            self.reload_interval,
        )
        # Initial load
        self._last_mtime = 0.0
        self._check_reload()

        active_tg = len(self._telegram_bots)
        active_wx = len(self._webex_bots)
        logger.info(
            "Initial load: %d Telegram bot(s), %d WebEx bot(s)",
            active_tg,
            active_wx,
        )

        # Watch loop
        while self._running:
            time.sleep(self.reload_interval)
            self._check_reload()

    def stop(self):
        """Stop all bots and the manager."""
        self._running = False
        with self._lock:
            for name, bot in self._telegram_bots.items():
                bot.stop()
            for name, bot in self._webex_bots.items():
                bot.stop()
            self._telegram_bots.clear()
            self._webex_bots.clear()
        logger.info("AgentBotManager stopped")

    def get_status(self) -> Dict:
        """Return current bot status for monitoring."""
        with self._lock:
            return {
                "telegram_bots": {
                    name: {
                        "running": bot.running,
                        "bot_info": bot.bot_info,
                    }
                    for name, bot in self._telegram_bots.items()
                },
                "webex_bots": {
                    name: {"running": bot.running}
                    for name, bot in self._webex_bots.items()
                },
            }


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Per-agent Telegram/WebEx bot manager")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_AGENTS_JSON),
        help="Path to agents.json",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help="Wee Orchestrator API URL",
    )
    parser.add_argument(
        "--api-key",
        default=DEFAULT_API_SHARED_KEY,
        help="API shared key",
    )
    parser.add_argument(
        "--reload-interval",
        type=int,
        default=30,
        help="Seconds between agents.json reload checks",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    manager = AgentBotManager(
        agents_json_path=args.config,
        api_url=args.api_url,
        api_shared_key=args.api_key,
        reload_interval=args.reload_interval,
    )

    try:
        manager.start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        manager.stop()


if __name__ == "__main__":
    main()
