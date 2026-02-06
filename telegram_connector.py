#!/usr/bin/env python3
"""
Telegram Connector for N8N Copilot Shim
Bridges Telegram chat with the agent_manager.py
Handles user pairing, message routing, and configuration
"""

import sys
import os
import json
import requests
import threading
import time
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime
import agent_manager


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


class TelegramConnector:
    """Main Telegram connector class"""

    def __init__(self, token: str, config_file: str = "telegram_config.json"):
        """
        Initialize Telegram connector

        Args:
            token: Telegram bot token
            config_file: Path to configuration file
        """
        self.token = token
        self.config = TelegramConfig(config_file)

        # Set token in config if provided
        if token and not self.config.config.get("token"):
            self.config.config["token"] = token
            self.config.save()

        self.api_url = f"https://api.telegram.org/bot{self.token}"
        self.offset = 0
        self.running = False

    def get_updates(self, timeout: int = 30) -> List[Dict]:
        """Fetch new messages from Telegram"""
        try:
            response = requests.get(
                f"{self.api_url}/getUpdates",
                params={"offset": self.offset, "timeout": timeout},
                timeout=timeout + 5,
            )
            response.raise_for_status()
            updates = response.json().get("result", [])

            if updates:
                self.offset = updates[-1]["update_id"] + 1

            return updates
        except Exception as e:
            print(f"Error fetching updates: {e}", file=sys.stderr)
            return []

    def send_message(self, chat_id: int, text: str):
        """Send message to Telegram chat"""
        try:
            requests.post(
                f"{self.api_url}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
        except Exception as e:
            print(f"Error sending message: {e}", file=sys.stderr)

    def send_typing(self, chat_id: int):
        """Send typing indicator to chat"""
        try:
            requests.post(
                f"{self.api_url}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
                timeout=5,
            )
        except Exception as e:
            print(f"Error sending typing indicator: {e}", file=sys.stderr)

    def download_file(self, file_id: str, user_id: int) -> Optional[str]:
        """Download file from Telegram and store it. Returns file path or None."""
        try:
            # Get file info
            file_response = requests.get(
                f"{self.api_url}/getFile",
                params={"file_id": file_id},
                timeout=10
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
        try:
            message = update.get("message", {})
            
            # Handle files with optional caption
            file_path = None
            text = message.get("text", "") or message.get("caption", "")
            
            # Check for document or photo
            if "document" in message:
                document = message["document"]
                user_id = message["from"]["id"]
                chat_id = message["chat"]["id"]
                
                file_path = self.download_file(document["file_id"], user_id)
                print(f"[DEBUG] Document detected, file_path: {file_path}", file=sys.stderr)
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
                print(f"[DEBUG] Photo detected, file_path: {file_path}", file=sys.stderr)
                if file_path:
                    if not text:
                        text = f"Please analyze this image: {file_path}"
                    else:
                        text = f"{text}\n\nImage to analyze: {file_path}"
                else:
                    self.send_message(chat_id, "Failed to download image")
                    return
            elif not text:
                # No text, no file - ignore
                return

            user_id = message["from"]["id"]
            chat_id = message["chat"]["id"]
            user_name = message["from"].get("username", f"user_{user_id}")
            # text already set from above (may be from file or message.text)

            # Check if user is allowed
            if not self.config.is_user_allowed(user_id):
                self.send_message(
                    chat_id, "❌ You are not authorized to use this bot."
                )
                return

            # Get or create user session
            session_info = self.config.get_user_session(user_id)
            if not session_info:
                # Create new session
                session_info = {
                    "user_id": user_id,
                    "username": user_name,
                    "paired_at": datetime.now().isoformat(),
                    "agent": self.config.config["default_agent"],
                    "model": self.config.config["default_model"],
                }
                self.config.set_user_session(user_id, session_info)

            # Handle slash commands via agent_manager
            session_id = f"telegram_{user_id}"
            
            # Show typing indicator
            self.send_typing(chat_id)
            
            if text.startswith("/"):
                # Check for timeout command
                if text.lower().startswith("/timeout"):
                    parts = text.split()
                    if len(parts) == 1 or (len(parts) > 1 and parts[1].lower() == "current"):
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
                                    response = "Timeout must be at most 3600 seconds (1 hour)"
                                else:
                                    self.config.set_user_timeout(user_id, new_timeout)
                                    response = f"✅ Timeout set to {new_timeout} seconds"
                            except ValueError:
                                response = "Invalid timeout. Use: /timeout set <seconds>"
                    else:
                        response = "Invalid timeout command. Use: /timeout current or /timeout set <seconds>"
                    self.send_message(chat_id, response)
                else:
                    # Regular slash commands
                    response = self._execute_command(text, session_id)
                    self.send_message(chat_id, response)
            else:
                # Route regular messages to agent_manager with status updates
                timeout = self.config.get_user_timeout(user_id)
                response = self._query_agent_with_status(
                    text, session_info["agent"], session_info["model"], user_id, chat_id, timeout
                )
                self.send_message(chat_id, response)
            
            # Cleanup temp files after query
            self.cleanup_files(user_id)

        except Exception as e:
            print(f"Error handling message: {e}", file=sys.stderr)
            self.send_message(chat_id, f"❌ Error: {str(e)[:100]}")

    def _handle_command(self, chat_id: int, user_id: int, command: str):
        """Handle Telegram commands - DEPRECATED: Commands now pass to agent_manager"""
        pass  # Commands are now routed to agent_manager

    def _execute_command(self, command: str, session_id: str) -> str:
        """Execute slash command via agent_manager.execute()"""
        try:
            from agent_manager import SessionManager
            
            session_mgr = SessionManager()
            result = session_mgr.execute(command, session_id)
            return result if result else "No response from command"
        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            print(f"Error in _execute_command: {tb_str}", file=sys.stderr)
            return f"Error: {str(e)[:150]}"

    def _query_agent_with_status(
        self, query: str, agent: str, model: str, user_id: int, chat_id: int, timeout: int = 300
    ) -> str:
        """Query agent with status updates at 10s, 30s, 60s, etc."""
        # Container for result and thread control
        result_container = {"response": None, "done": False}
        
        # Status messages
        status_msgs = [
            "Still working on it...",
            "Sorry it's taking so long, still working on it...",
            "Still processing, hang tight...",
            "Almost there, still working...",
            "Continuing to work on this...",
        ]
        
        def run_query():
            """Run query in background thread"""
            # Log what's being sent
            print(f"[DEBUG] Query to agent: {query[:200]}", file=sys.stderr)
            result_container["response"] = self._query_agent(query, agent, model, user_id)
            result_container["done"] = True
        
        # Start query in background
        query_thread = threading.Thread(target=run_query, daemon=True)
        query_thread.start()
        
        # Wait for result with status updates
        elapsed = 0
        status_idx = 0
        while not result_container["done"] and elapsed < timeout:
            if elapsed == 10:
                # First status at 10s
                self.send_message(chat_id, status_msgs[0])
                status_idx = 1
            elif elapsed > 10 and (elapsed - 10) % 30 == 0:
                # Status every 30s after initial 10s
                msg = status_msgs[status_idx % len(status_msgs)]
                self.send_message(chat_id, msg)
                status_idx += 1
            
            time.sleep(1)
            elapsed += 1
        
        # Wait for thread to complete (with timeout)
        query_thread.join(timeout=5)
        
        return result_container["response"] or "Error: Query timed out"

    def _query_agent(
        self, query: str, agent: str, model: str, user_id: int
    ) -> str:
        """Query the agent_manager with user session tied to user ID"""
        try:
            from agent_manager import SessionManager
            
            # Session ID tied to user ID
            session_id = f"telegram_{user_id}"
            
            # Create session manager (uses default config)
            session_mgr = SessionManager()
            
            # Run the query through the agent
            result = session_mgr.run_copilot(
                prompt=query,
                model=model,
                agent=agent,
                session_id=session_id,
                resume=True,  # Resume existing session if available
                n8n_session_id=session_id,
                timeout=30
            )
            
            return result if result else "No response from agent"
        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            print(f"Error in _query_agent: {tb_str}", file=sys.stderr)
            return f"Error: {str(e)[:150]}"

    def run(self, poll_interval: int = 1):
        """Start polling for messages"""
        self.running = True
        print(f"Starting Telegram connector with token: {self.token[:20]}...")

        try:
            while self.running:
                updates = self.get_updates()
                for update in updates:
                    if "message" in update:
                        self.handle_message(update)
                    elif "callback_query" in update:
                        # Handle button callbacks if needed
                        pass

                if not updates:
                    time.sleep(poll_interval)

        except KeyboardInterrupt:
            print("\nShutting down Telegram connector...")
            self.running = False

    def stop(self):
        """Stop the connector"""
        self.running = False


def main():
    """Main entry point for Telegram connector"""
    import argparse

    parser = argparse.ArgumentParser(description="Telegram connector for N8N Copilot Shim")
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
        print("Error: Telegram bot token required (--token or TELEGRAM_BOT_TOKEN env var)")
        sys.exit(1)

    connector = TelegramConnector(args.token, args.config)
    connector.run()


if __name__ == "__main__":
    main()
