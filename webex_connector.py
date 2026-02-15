#!/usr/bin/env python3
"""
WebEX Connector for N8N Copilot Shim
Bridges WebEX messages from RabbitMQ queue with the agent_manager.py
Mirrors Telegram listener architecture but uses RabbitMQ as message source
"""

import sys
import os
import json
import re
import requests
import threading
import time
import pika
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime
import agent_manager


class WebEXConfig:
    """Manages WebEX connector configuration"""

    def __init__(self, config_file: str = "webex_config.json"):
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
            "token": os.environ.get("WEBEX_BOT_TOKEN", ""),
            "rabbitmq_host": os.environ.get("RABBITMQ_HOST", "192.168.0.85"),
            "rabbitmq_port": int(os.environ.get("RABBITMQ_PORT", "5672")),
            "rabbitmq_user": os.environ.get("RABBITMQ_USER", "admin"),
            "rabbitmq_password": os.environ.get("RABBITMQ_PASSWORD", ""),
            "rabbitmq_queue": os.environ.get("RABBITMQ_QUEUE", "webex"),
            "rabbitmq_vhost": "/",
            "allowed_users": [],  # List of WebEX person IDs allowed to chat
            "user_pairings": {},  # Maps WebEX person ID to session info
            "enable_auto_pair": False,  # Auto-pair new users
            "default_agent": os.environ.get("COPILOT_DEFAULT_AGENT", "orchestrator"),
            "default_model": os.environ.get("COPILOT_DEFAULT_MODEL", "gpt-5-mini"),
        }

    def save(self):
        """Save configuration to file"""
        try:
            with open(self.config_file, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}", file=sys.stderr)

    def is_user_allowed(self, person_id: str) -> bool:
        """Check if user is allowed to chat"""
        if not self.config["allowed_users"]:
            return True  # No restrictions if list is empty
        return person_id in self.config["allowed_users"]

    def get_user_session(self, person_id: str) -> Optional[Dict]:
        """Get session info for a user"""
        return self.config["user_pairings"].get(person_id)

    def set_user_session(self, person_id: str, session_info: Dict):
        """Store session info for a user"""
        self.config["user_pairings"][person_id] = session_info
        self.save()

    def get_user_timeout(self, person_id: str) -> int:
        """Get timeout for user (default 300s)"""
        session = self.get_user_session(person_id)
        if session:
            return session.get("timeout", 300)
        return 300

    def set_user_timeout(self, person_id: str, timeout: int):
        """Set timeout for user"""
        session = self.get_user_session(person_id)
        if session:
            session["timeout"] = max(30, min(timeout, 3600))  # Clamp 30-3600s
            self.set_user_session(person_id, session)

    def allow_user(self, person_id: str):
        """Add user to allowed list"""
        if person_id not in self.config["allowed_users"]:
            self.config["allowed_users"].append(person_id)
            self.save()

    def deny_user(self, person_id: str):
        """Remove user from allowed list"""
        if person_id in self.config["allowed_users"]:
            self.config["allowed_users"].remove(person_id)
            self.save()


class WebEXConnector:
    """Main WebEX connector class - listens to RabbitMQ queue"""

    def __init__(self, token: str, config_file: str = "webex_config.json"):
        """
        Initialize WebEX connector

        Args:
            token: WebEX bot token
            config_file: Path to configuration file
        """
        self.token = token
        self.config = WebEXConfig(config_file)

        # Keep persistent SessionManager per session_id for context persistence
        self.session_managers = {}  # {session_id: SessionManager}

        # Set token in config if provided
        if token and not self.config.config.get("token"):
            self.config.config["token"] = token
            self.config.save()

        self.running = False
        self.rabbitmq_connection = None
        self.rabbitmq_channel = None

    def get_session_manager(self, session_id: str):
        """Get or create SessionManager for session_id"""
        if session_id not in self.session_managers:
            from agent_manager import SessionManager
            self.session_managers[session_id] = SessionManager()
        return self.session_managers[session_id]

    def _evict_session_manager(self, session_id: str):
        """Remove cached SessionManager so next call gets a fresh one"""
        if session_id in self.session_managers:
            del self.session_managers[session_id]
            print(f"[DEBUG] Evicted cached SessionManager for: {session_id}", file=sys.stderr)

    def connect_rabbitmq(self) -> bool:
        """Connect to RabbitMQ"""
        try:
            credentials = pika.PlainCredentials(
                self.config.config["rabbitmq_user"],
                self.config.config["rabbitmq_password"]
            )
            parameters = pika.ConnectionParameters(
                host=self.config.config["rabbitmq_host"],
                port=self.config.config["rabbitmq_port"],
                virtual_host=self.config.config["rabbitmq_vhost"],
                credentials=credentials,
                connection_attempts=3,
                retry_delay=2
            )
            self.rabbitmq_connection = pika.BlockingConnection(parameters)
            self.rabbitmq_channel = self.rabbitmq_connection.channel()

            # Declare queue (durable)
            self.rabbitmq_channel.queue_declare(
                queue=self.config.config["rabbitmq_queue"],
                durable=True
            )

            print(f"✅ Connected to RabbitMQ on {self.config.config['rabbitmq_host']}", file=sys.stderr)
            return True
        except Exception as e:
            print(f"❌ Error connecting to RabbitMQ: {e}", file=sys.stderr)
            return False

    def disconnect_rabbitmq(self):
        """Disconnect from RabbitMQ"""
        try:
            if self.rabbitmq_connection and not self.rabbitmq_connection.is_closed:
                self.rabbitmq_connection.close()
        except Exception as e:
            print(f"Error disconnecting from RabbitMQ: {e}", file=sys.stderr)

    def send_message(self, room_id: str, text: str) -> bool:
        """Send message to WebEX room via API"""
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }

            # Split into chunks if too long
            max_len = 4000  # WebEX message length limit
            chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] if text else ["No response"]

            for chunk in chunks:
                data = {
                    "roomId": room_id,
                    "text": chunk
                }

                response = requests.post(
                    "https://webexapis.com/v1/messages",
                    headers=headers,
                    json=data,
                    timeout=10
                )

                if response.status_code != 200:
                    print(f"[WARN] WebEX send failed ({response.status_code}): {response.text[:200]}", file=sys.stderr)
                    return False

            return True
        except Exception as e:
            print(f"Error sending WebEX message: {e}", file=sys.stderr)
            return False

    def get_person_info(self, person_id: str) -> Optional[Dict]:
        """Get WebEX person info by ID"""
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            response = requests.get(
                f"https://webexapis.com/v1/people/{person_id}",
                headers=headers,
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            print(f"Error getting person info: {e}", file=sys.stderr)
        return None

    def handle_message(self, message_data: Dict):
        """Process incoming WebEX message from RabbitMQ"""
        try:
            person_id = message_data.get("personId")
            room_id = message_data.get("roomId")
            text = message_data.get("text", "").strip()
            person_email = message_data.get("personEmail", "unknown")

            if not person_id or not room_id or not text:
                print(f"[DEBUG] Incomplete message: {message_data}", file=sys.stderr)
                return

            # Check if user is allowed
            if not self.config.is_user_allowed(person_id):
                self.send_message(room_id, "❌ You are not authorized to use this bot.")
                return

            # Get or create user session
            session_info = self.config.get_user_session(person_id)
            if not session_info:
                # Create new session
                session_info = {
                    "person_id": person_id,
                    "email": person_email,
                    "paired_at": datetime.now().isoformat(),
                    "agent": self.config.config["default_agent"],
                    "model": self.config.config["default_model"],
                }
                self.config.set_user_session(person_id, session_info)

            session_id = f"webex_{person_id}"

            # Handle slash commands
            if text.startswith("/"):
                if text.lower().startswith("/timeout"):
                    parts = text.split()
                    if len(parts) == 1 or (len(parts) > 1 and parts[1].lower() == "current"):
                        current = self.config.get_user_timeout(person_id)
                        response = f"Current timeout: {current} seconds"
                    elif len(parts) > 1 and parts[1].lower() == "set":
                        if len(parts) < 3:
                            response = "Invalid timeout. Use: /timeout set <seconds>"
                        else:
                            try:
                                new_timeout = int(parts[2])
                                if new_timeout < 30:
                                    response = "Timeout must be at least 30 seconds"
                                elif new_timeout > 3600:
                                    response = "Timeout must be at most 3600 seconds (1 hour)"
                                else:
                                    self.config.set_user_timeout(person_id, new_timeout)
                                    response = f"✅ Timeout set to {new_timeout} seconds"
                            except ValueError:
                                response = "Invalid timeout. Use: /timeout set <seconds>"
                    else:
                        response = "Invalid timeout command. Use: /timeout current or /timeout set <seconds>"
                    self.send_message(room_id, response)
                else:
                    # Regular slash commands
                    timeout = self.config.get_user_timeout(person_id)
                    response = self._execute_command(text, session_id, timeout)
                    self.send_message(room_id, response)

                    # Evict cached SessionManager on session-affecting commands
                    cmd_lower = text.lower().strip()
                    if cmd_lower.startswith("/session reset") or cmd_lower.startswith("/runtime set"):
                        self._evict_session_manager(session_id)
            else:
                # Check for bash command (!)
                if text.startswith("!"):
                    timeout = self.config.get_user_timeout(person_id)
                    response = self._execute_command(text, session_id, timeout)
                    self.send_message(room_id, response)
                else:
                    # Route regular messages to agent_manager with status updates
                    timeout = self.config.get_user_timeout(person_id)
                    response = self._query_agent_with_status(
                        text, session_info["agent"], session_info["model"], person_id, room_id, timeout
                    )
                    self.send_message(room_id, response)

        except Exception as e:
            print(f"Error handling message: {e}", file=sys.stderr)
            if room_id:
                self.send_message(room_id, f"❌ Error: {str(e)[:100]}")

    def _execute_command(self, command: str, session_id: str, timeout: int = 300) -> str:
        """Execute slash command via agent_manager.execute() with timeout support"""
        result_container = {"response": None, "done": False}

        def run_command():
            try:
                session_mgr = self.get_session_manager(session_id)
                result_container["response"] = session_mgr.execute(command, session_id)
                result_container["done"] = True
            except Exception as e:
                import traceback
                tb_str = traceback.format_exc()
                print(f"Error in _execute_command: {tb_str}", file=sys.stderr)
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

    def _query_agent_with_status(
        self, query: str, agent: str, model: str, person_id: str, room_id: str, timeout: int = 300
    ) -> str:
        """Query agent with periodic updates"""
        result_container = {"response": None, "done": False}

        status_msgs = [
            "Still working on it...",
            "Sorry it's taking so long, still working on it...",
            "Still processing, hang tight...",
            "Almost there, still working...",
            "Continuing to work on this...",
        ]

        def run_query():
            print(f"[DEBUG] Query to agent: {query[:200]}", file=sys.stderr)
            result_container["response"] = self._query_agent(query, agent, model, person_id, timeout)
            result_container["done"] = True

        query_thread = threading.Thread(target=run_query, daemon=True)
        query_thread.start()

        elapsed = 0
        status_idx = 0
        while not result_container["done"] and elapsed < timeout:
            if elapsed == 30:
                self.send_message(room_id, status_msgs[0])
                status_idx = 1
            elif elapsed > 30 and (elapsed - 30) % 30 == 0:
                msg = status_msgs[status_idx % len(status_msgs)]
                self.send_message(room_id, msg)
                status_idx += 1

            time.sleep(1)
            elapsed += 1

        query_thread.join(timeout=5)
        return result_container["response"] or "Error: Query timed out"

    def _query_agent(
        self, query: str, agent: str, model: str, person_id: str, timeout: int = 300
    ) -> str:
        """Query the agent_manager with user session tied to person ID"""
        try:
            session_id = f"webex_{person_id}"
            session_mgr = self.get_session_manager(session_id)

            print(f"[DEBUG] Using persistent session_mgr for: {session_id}", file=sys.stderr)
            result = session_mgr.execute(query, session_id)

            return result if result else "No response from agent"
        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            print(f"Error in _query_agent: {tb_str}", file=sys.stderr)
            return f"Error: {str(e)[:150]}"

    def listen_to_queue(self, poll_interval: int = 1):
        """Listen to RabbitMQ queue for WebEX messages"""
        self.running = True
        print(f"Starting WebEX connector, listening to queue: {self.config.config['rabbitmq_queue']}")

        try:
            def callback(ch, method, properties, body):
                """Handle message from queue"""
                try:
                    message_data = json.loads(body.decode())
                    print(f"[DEBUG] Received WebEX message: {message_data}", file=sys.stderr)
                    self.handle_message(message_data)
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                except Exception as e:
                    print(f"Error processing queue message: {e}", file=sys.stderr)
                    ch.basic_nack(delivery_tag=method.delivery_tag)

            if not self.connect_rabbitmq():
                print("Failed to connect to RabbitMQ, exiting...", file=sys.stderr)
                return

            self.rabbitmq_channel.basic_consume(
                queue=self.config.config["rabbitmq_queue"],
                on_message_callback=callback
            )

            print(f"[✅] Listening to RabbitMQ queue: {self.config.config['rabbitmq_queue']}", file=sys.stderr)
            self.rabbitmq_channel.start_consuming()

        except KeyboardInterrupt:
            print("\nShutting down WebEX connector...")
            self.running = False
        finally:
            self.disconnect_rabbitmq()

    def stop(self):
        """Stop the connector"""
        self.running = False
        self.disconnect_rabbitmq()


def main():
    """Main entry point for WebEX connector"""
    import argparse

    parser = argparse.ArgumentParser(description="WebEX connector for N8N Copilot Shim")
    parser.add_argument(
        "--token",
        default=os.environ.get("WEBEX_BOT_TOKEN", ""),
        help="WebEX bot token (or use WEBEX_BOT_TOKEN env var)",
    )
    parser.add_argument(
        "--config",
        default="webex_config.json",
        help="Configuration file path",
    )
    parser.add_argument(
        "--allow-user",
        help="Allow a person ID to chat",
    )
    parser.add_argument(
        "--deny-user",
        help="Deny a person ID from chatting",
    )
    parser.add_argument(
        "--list-users",
        action="store_true",
        help="List allowed users",
    )

    args = parser.parse_args()

    # Initialize config
    config = WebEXConfig(args.config)

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
        print("Error: WebEX bot token required (--token or WEBEX_BOT_TOKEN env var)")
        sys.exit(1)

    connector = WebEXConnector(args.token, args.config)
    connector.listen_to_queue()


if __name__ == "__main__":
    main()
