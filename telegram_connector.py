#!/usr/bin/env python3
"""
Telegram Connector for N8N Copilot Shim
Bridges Telegram chat with the agent_manager.py
Handles user pairing, message routing, and configuration
"""

import sys
import os
import json
import re
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
        
        # Keep persistent SessionManager per session_id for context persistence
        self.session_managers = {}  # {session_id: SessionManager}

        # Set token in config if provided
        if token and not self.config.config.get("token"):
            self.config.config["token"] = token
            self.config.save()

        self.api_url = f"https://api.telegram.org/bot{self.token}"
        self.offset = 0
        self.running = False
    
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

    def send_message(self, chat_id: int, text: str) -> Optional[int]:
        """Send message to Telegram chat with HTML formatting, fallback to plain text. Returns message_id."""
        try:
            # Split long messages (Telegram limit is 4096 chars)
            max_len = 4096
            chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] if text else ["No response"]
            
            last_msg_id = None
            for chunk in chunks:
                # Try HTML first
                resp = requests.post(
                    f"{self.api_url}/sendMessage",
                    json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"},
                    timeout=10,
                )
                if resp.status_code != 200:
                    # HTML failed, fallback to plain text
                    print(f"[WARN] HTML send failed ({resp.status_code}), falling back to plain text", file=sys.stderr)
                    resp = requests.post(
                        f"{self.api_url}/sendMessage",
                        json={"chat_id": chat_id, "text": chunk},
                        timeout=10,
                    )
                    if resp.status_code != 200:
                        print(f"[ERROR] Plain text send also failed ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
                
                if resp.status_code == 200:
                    result = resp.json()
                    if result.get("ok"):
                        last_msg_id = result["result"]["message_id"]
            return last_msg_id
        except Exception as e:
            print(f"Error sending message: {e}", file=sys.stderr)
            return None

    def edit_message(self, chat_id: int, message_id: int, text: str):
        """Edit an existing message with HTML formatting, fallback to plain text.
        Handles long messages by editing the first chunk and sending the rest."""
        try:
            max_len = 4096
            chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] if text else ["No response"]
            
            # Edit with first chunk (try HTML, fallback to plain)
            resp = requests.post(
                f"{self.api_url}/editMessageText",
                json={"chat_id": chat_id, "message_id": message_id, "text": chunks[0], "parse_mode": "HTML"},
                timeout=10,
            )
            if resp.status_code != 200:
                resp = requests.post(
                    f"{self.api_url}/editMessageText",
                    json={"chat_id": chat_id, "message_id": message_id, "text": chunks[0]},
                    timeout=10,
                )
                if resp.status_code != 200:
                    print(f"[WARN] Edit message failed ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
            
            # Send remaining chunks as new messages
            for chunk in chunks[1:]:
                self.send_message(chat_id, chunk)
        except Exception as e:
            print(f"Error editing message: {e}", file=sys.stderr)

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

    def send_photo(self, chat_id: int, photo_url: str, caption: str = "") -> Optional[int]:
        """Send a photo to Telegram chat via URL. Returns message_id."""
        try:
            data = {"chat_id": chat_id, "photo": photo_url}
            if caption:
                # Telegram caption limit is 1024 chars
                data["caption"] = caption[:1024]
                data["parse_mode"] = "HTML"
            resp = requests.post(
                f"{self.api_url}/sendPhoto",
                json=data,
                timeout=30,
            )
            if resp.status_code != 200:
                # Fallback: try without parse_mode
                if caption:
                    data.pop("parse_mode", None)
                    resp = requests.post(f"{self.api_url}/sendPhoto", json=data, timeout=30)
                if resp.status_code != 200:
                    print(f"[WARN] sendPhoto failed ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
                    return None
            result = resp.json()
            if result.get("ok"):
                return result["result"]["message_id"]
        except Exception as e:
            print(f"Error sending photo: {e}", file=sys.stderr)
        return None

    def extract_image_urls(self, text: str) -> tuple:
        """Extract image URLs from text/HTML. Returns (image_urls, remaining_text)."""
        image_extensions = r'\.(jpg|jpeg|png|gif|webp|bmp|svg)(\?[^\s"<>]*)?'
        
        # Match <img> tags
        img_tag_pattern = r'<img\s+[^>]*src=["\']([^"\']+)["\'][^>]*/?\s*>'
        # Match bare image URLs
        bare_url_pattern = r'(https?://[^\s"<>]+' + image_extensions + r')'
        
        image_urls = []
        remaining = text
        
        # Extract from <img> tags
        for match in re.finditer(img_tag_pattern, text, re.IGNORECASE):
            url = match.group(1)
            if url not in image_urls:
                image_urls.append(url)
            remaining = remaining.replace(match.group(0), "").strip()
        
        # Extract bare image URLs
        for match in re.finditer(bare_url_pattern, remaining, re.IGNORECASE):
            url = match.group(1)
            if url not in image_urls:
                image_urls.append(url)
                remaining = remaining.replace(url, "").strip()
        
        return image_urls, remaining

    def send_response(self, chat_id: int, text: str, status_msg_id: Optional[int] = None):
        """Send response, detecting image URLs and using sendPhoto when appropriate."""
        image_urls, remaining_text = self.extract_image_urls(text)
        
        if image_urls:
            # If there was a status message, edit it with the text portion
            if remaining_text.strip() and status_msg_id:
                self.edit_message(chat_id, status_msg_id, remaining_text)
                status_msg_id = None  # Already used
            elif remaining_text.strip():
                self.send_message(chat_id, remaining_text)
            elif status_msg_id:
                # No text, just images — delete the status message
                try:
                    requests.post(
                        f"{self.api_url}/deleteMessage",
                        json={"chat_id": chat_id, "message_id": status_msg_id},
                        timeout=5,
                    )
                except Exception:
                    pass
                status_msg_id = None
            
            # Send each image
            for url in image_urls:
                self.send_photo(chat_id, url)
        else:
            # No images — normal text response
            if status_msg_id:
                self.edit_message(chat_id, status_msg_id, text)
            else:
                self.send_message(chat_id, text)

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
                    # Regular slash commands - get user timeout
                    timeout = self.config.get_user_timeout(user_id)
                    response = self._execute_command(text, session_id, timeout)
                    self.send_message(chat_id, response)
                    # Evict cached SessionManager on session-affecting commands
                    cmd_lower = text.lower().strip()
                    if cmd_lower.startswith("/session reset") or cmd_lower.startswith("/runtime set"):
                        self._evict_session_manager(session_id)
            else:
                # Check for bash command (!)
                if text.startswith("!"):
                    # Bash commands - get user timeout
                    timeout = self.config.get_user_timeout(user_id)
                    response = self._execute_command(text, session_id, timeout)
                    self.send_message(chat_id, response)
                else:
                    # Route regular messages to agent_manager with status updates
                    timeout = self.config.get_user_timeout(user_id)
                    response, status_msg_id = self._query_agent_with_status(
                        text, session_info["agent"], session_info["model"], user_id, chat_id, timeout
                    )
                    self.send_response(chat_id, response, status_msg_id)
            
            # Cleanup temp files after query
            self.cleanup_files(user_id)

        except Exception as e:
            print(f"Error handling message: {e}", file=sys.stderr)
            self.send_message(chat_id, f"❌ Error: {str(e)[:100]}")

    def _handle_command(self, chat_id: int, user_id: int, command: str):
        """Handle Telegram commands - DEPRECATED: Commands now pass to agent_manager"""
        pass  # Commands are now routed to agent_manager

    def _execute_command(self, command: str, session_id: str, timeout: int = 300) -> str:
        """Execute slash command via agent_manager.execute() with timeout support"""
        # Container for result and thread control
        result_container = {"response": None, "done": False}
        
        def run_command():
            """Run command in background thread"""
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

    def _query_agent_with_status(
        self, query: str, agent: str, model: str, user_id: int, chat_id: int, timeout: int = 300
    ) -> tuple:
        """Query agent with status updates at 30s intervals.
        
        Returns (response_text, status_msg_id) where status_msg_id is the
        message to edit with the final response, or None if no status was sent.
        """
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
            result_container["response"] = self._query_agent(query, agent, model, user_id, timeout)
            result_container["done"] = True
        
        # Start query in background
        query_thread = threading.Thread(target=run_query, daemon=True)
        query_thread.start()
        
        # Wait for result with status updates
        elapsed = 0
        status_idx = 0
        status_msg_id = None  # Track the status message to edit it
        while not result_container["done"] and elapsed < timeout:
            # Re-send typing every 5 seconds to keep indicator alive
            if elapsed % 5 == 0:
                self.send_typing(chat_id)
            
            if elapsed == 30:
                # First status at 30s - send new message
                status_msg_id = self.send_message(chat_id, status_msgs[0])
                self.send_typing(chat_id)
                status_idx = 1
            elif elapsed > 30 and (elapsed - 30) % 30 == 0:
                # Edit existing status message
                msg = status_msgs[status_idx % len(status_msgs)]
                if status_msg_id:
                    self.edit_message(chat_id, status_msg_id, msg)
                else:
                    status_msg_id = self.send_message(chat_id, msg)
                self.send_typing(chat_id)
                status_idx += 1
            
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
            # Session ID tied to user ID
            session_id = f"telegram_{user_id}"
            
            # Use persistent session manager
            session_mgr = self.get_session_manager(session_id)
            
            # Debug: log session info
            print(f"[DEBUG] Using persistent session_mgr for: {session_id}", file=sys.stderr)
            
            # Use execute() which routes to the correct runtime automatically
            result = session_mgr.execute(query, session_id)
            
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
