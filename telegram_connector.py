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
import mimetypes
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime
import agent_manager

# If production listener should be disabled to avoid duplicate responders, create the
# sentinel file /opt/n8n-copilot-shim/PROD_DISABLED. When present, any process
# started from the production path will exit immediately.
_prod_disable_path = Path("/opt/n8n-copilot-shim/PROD_DISABLED")
if os.path.abspath(__file__).startswith("/opt/n8n-copilot-shim/") and _prod_disable_path.exists():
    print("Production Telegram connector disabled via PROD_DISABLED sentinel.", file=sys.stderr)
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
            "pinned_users": {},       # Maps user_id (str) to {"agent": "name"} - locks user to that agent
            "yolo_allowed_users": [], # User IDs permitted to enable /mode yolo; empty = all allowed
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
        self.token = token
        self.config = TelegramConfig(config_file)
        
        # Keep persistent SessionManager per session_id for context persistence
        self.session_managers = {}  # {session_id: SessionManager}

        # Set token in config if provided
        if token and not self.config.config.get("token"):
            self.config.config["token"] = token
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
            print(f"[DEBUG] Evicted cached SessionManager for: {session_id}", file=sys.stderr)

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

    def _execute_via_api(self, query: str, session_id: str, user_identity: str, channel: str) -> str:
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
                timeout=self.config.get_user_timeout(int(user_identity.replace("telegram_", ""))) if "telegram_" in str(user_identity) else 600,
            )

            if resp.status_code == 200:
                return resp.json().get("response", "No response from API")
            else:
                print(f"[WARN] API request failed ({resp.status_code}): {resp.text}", file=sys.stderr)
                # Fallback to direct mode
                session_mgr = self.get_session_manager(session_id)
                return session_mgr.execute(query, session_id)
        except Exception as e:
            print(f"[WARN] API request exception: {e}, falling back to direct mode", file=sys.stderr)
            session_mgr = self.get_session_manager(session_id)
            return session_mgr.execute(query, session_id)

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

    def sanitize_telegram_html(self, text: str) -> str:
        """Sanitize HTML to only contain Telegram-supported tags.
        Telegram supports: b, strong, i, em, u, ins, s, strike, del, a, code, pre, blockquote, tg-spoiler"""
        # Supported Telegram tags (opening and closing)
        supported_tags = {'b', 'strong', 'i', 'em', 'u', 'ins', 's', 'strike', 'del',
                          'a', 'code', 'pre', 'blockquote', 'tg-spoiler', 'tg-emoji'}
        
        # Replace <br> with newline before processing
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        
        # Replace <pre><code class="..."> with just <pre>
        text = re.sub(r'<pre>\s*<code[^>]*>', '<pre>', text)
        text = re.sub(r'</code>\s*</pre>', '</pre>', text)
        
        # Remove attributes from all tags except <a> (which needs href) and <blockquote> (expandable)
        def clean_tag(match):
            full = match.group(0)
            # Closing tags
            if full.startswith('</'):
                tag_name = re.match(r'</(\w[\w-]*)', full)
                if tag_name and tag_name.group(1).lower() in supported_tags:
                    return f'</{tag_name.group(1)}>'
                return ''  # Remove unsupported closing tag
            # Opening tags
            tag_match = re.match(r'<(\w[\w-]*)([\s>])', full)
            if not tag_match:
                # Self-closing or malformed - remove
                return ''
            tag_name = tag_match.group(1).lower()
            if tag_name not in supported_tags:
                return ''  # Remove unsupported tag
            if tag_name == 'a':
                # Keep href attribute
                href = re.search(r'href=["\']([^"\']*)["\']', full, re.IGNORECASE)
                if href:
                    return f'<a href="{href.group(1)}">'
                return ''  # <a> without href is useless
            if tag_name == 'blockquote':
                if 'expandable' in full:
                    return '<blockquote expandable>'
                return '<blockquote>'
            # All other supported tags - strip attributes
            return f'<{tag_name}>'
        
        text = re.sub(r'</?[\w][\w-]*(?:\s[^>]*)?\s*/?>', clean_tag, text)
        
        # Escape stray < and > that aren't part of valid tags
        # First protect valid tags, then escape strays, then restore
        import html as html_mod
        parts = re.split(r'(</?(?:' + '|'.join(supported_tags) + r')(?:\s[^>]*)?>)', text)
        result = []
        for i, part in enumerate(parts):
            if i % 2 == 0:
                # Not a tag - escape any remaining < >
                part = part.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            result.append(part)
        text = ''.join(result)
        
        return text

    def send_message(self, chat_id: int, text: str) -> Optional[int]:
        """Send message to Telegram chat with HTML formatting, fallback to plain text. Returns message_id."""
        try:
            sanitized = self.sanitize_telegram_html(text)
            max_len = 4096
            chunks = [sanitized[i:i + max_len] for i in range(0, len(sanitized), max_len)] if sanitized else ["No response"]
            
            last_msg_id = None
            for chunk in chunks:
                # Log outbound attempt (short snippet)
                print(f"[DEBUG OUTBOUND] send_message -> chat_id={chat_id} text_snippet={repr(chunk[:200])}", file=sys.stderr)
                resp = requests.post(
                    f"{self.api_url}/sendMessage",
                    json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"},
                    timeout=10,
                )
                print(f"[DEBUG OUTBOUND] send_message HTTP {resp.status_code} for chat_id={chat_id}", file=sys.stderr)
                if resp.status_code != 200:
                    print(f"[WARN] HTML send failed ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
                    # Fallback to plain text
                    plain = re.sub(r'<[^>]+>', '', chunk)
                    plain = plain.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                    print(f"[DEBUG OUTBOUND] send_message falling back to plain text for chat_id={chat_id}", file=sys.stderr)
                    resp = requests.post(
                        f"{self.api_url}/sendMessage",
                        json={"chat_id": chat_id, "text": plain},
                        timeout=10,
                    )
                    print(f"[DEBUG OUTBOUND] send_message fallback HTTP {resp.status_code} for chat_id={chat_id}", file=sys.stderr)
                    if resp.status_code != 200:
                        print(f"[ERROR] Plain text also failed ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
                
                if resp.status_code == 200:
                    try:
                        result = resp.json()
                        if result.get("ok"):
                            last_msg_id = result["result"]["message_id"]
                            print(f"[DEBUG OUTBOUND] send_message succeeded message_id={last_msg_id} for chat_id={chat_id}", file=sys.stderr)
                    except Exception as e:
                        print(f"[WARN] send_message response JSON parse error: {e}", file=sys.stderr)
            return last_msg_id
        except Exception as e:
            print(f"Error sending message: {e}", file=sys.stderr)
            return None

    def edit_message(self, chat_id: int, message_id: int, text: str):
        """Edit an existing message with HTML formatting, fallback to plain text."""
        try:
            sanitized = self.sanitize_telegram_html(text)
            max_len = 4096
            chunks = [sanitized[i:i + max_len] for i in range(0, len(sanitized), max_len)] if sanitized else ["No response"]
            
            # Log edit attempt
            print(f"[DEBUG OUTBOUND] edit_message -> chat_id={chat_id} message_id={message_id} text_snippet={repr(chunks[0][:200])}", file=sys.stderr)
            resp = requests.post(
                f"{self.api_url}/editMessageText",
                json={"chat_id": chat_id, "message_id": message_id, "text": chunks[0], "parse_mode": "HTML"},
                timeout=10,
            )
            print(f"[DEBUG OUTBOUND] edit_message HTTP {resp.status_code} for chat_id={chat_id} message_id={message_id}", file=sys.stderr)
            if resp.status_code != 200:
                # Fallback to plain text
                plain = re.sub(r'<[^>]+>', '', chunks[0])
                plain = plain.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                print(f"[DEBUG OUTBOUND] edit_message falling back to plain text for chat_id={chat_id} message_id={message_id}", file=sys.stderr)
                resp = requests.post(
                    f"{self.api_url}/editMessageText",
                    json={"chat_id": chat_id, "message_id": message_id, "text": plain},
                    timeout=10,
                )
                print(f"[DEBUG OUTBOUND] edit_message fallback HTTP {resp.status_code} for chat_id={chat_id} message_id={message_id}", file=sys.stderr)
                if resp.status_code != 200:
                    print(f"[WARN] Edit failed ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
            
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
            print(f"[DEBUG OUTBOUND] send_typing HTTP {resp.status_code} for chat_id={chat_id}", file=sys.stderr)
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
            print(f"[DEBUG] unpinAllChatMessages status: {resp.status_code}", file=sys.stderr)
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
            print(f"[WARN] Failed to pin message ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
            return False
        except Exception as e:
            print(f"Error pinning message: {e}", file=sys.stderr)
            return False

    def _is_safe_file_path(self, file_path: str) -> bool:
        """Validate file path for security.

        Checks:
        - File exists
        - File is within allowed directory (telegram_downloads)
        - No path traversal attacks
        - File size within limits (50MB)
        """
        try:
            allowed_dir = Path("/opt/n8n-copilot-shim-dev/telegram_downloads").resolve()
            file_path_obj = Path(file_path).resolve()

            # Check file exists
            if not file_path_obj.exists():
                print(f"[WARN] File does not exist: {file_path}", file=sys.stderr)
                return False

            # Check file is in allowed directory (compatible with Python 3.8+)
            try:
                # Python 3.9+
                is_safe = file_path_obj.is_relative_to(allowed_dir)
            except AttributeError:
                # Python 3.8 fallback: check if resolved path starts with allowed dir
                is_safe = str(file_path_obj).startswith(str(allowed_dir))

            if not is_safe:
                print(f"[WARN] File outside allowed directory: {file_path}", file=sys.stderr)
                return False

            # Check file size (50MB limit)
            if file_path_obj.stat().st_size > 50 * 1024 * 1024:
                print(f"[WARN] File exceeds 50MB limit: {file_path}", file=sys.stderr)
                return False

            return True
        except Exception as e:
            print(f"[WARN] Error validating file path: {e}", file=sys.stderr)
            return False

    def send_photo(self, chat_id: int, photo_url: str, caption: str = "") -> Optional[int]:
        """Send a photo to Telegram chat via URL. Returns message_id."""
        try:
            print(f"[DEBUG] Attempting sendPhoto with URL: {photo_url}", file=sys.stderr)
            data = {"chat_id": chat_id, "photo": photo_url}
            if caption:
                # Sanitize caption to support safe HTML formatting
                sanitized_caption = self.sanitize_telegram_html(caption.strip())
                data["caption"] = sanitized_caption[:1024]  # Telegram limit
                data["parse_mode"] = "HTML"
            resp = requests.post(
                f"{self.api_url}/sendPhoto",
                json=data,
                timeout=30,
            )
            if resp.status_code != 200:
                if caption:
                    data.pop("parse_mode", None)
                    resp = requests.post(f"{self.api_url}/sendPhoto", json=data, timeout=30)
                if resp.status_code != 200:
                    print(f"[WARN] sendPhoto failed ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
                    # Fallback: send URL as clickable link
                    self.send_message(chat_id, f'<a href="{photo_url}">📷 Image link</a>')
                    return None
            result = resp.json()
            if result.get("ok"):
                return result["result"]["message_id"]
        except Exception as e:
            print(f"Error sending photo: {e}", file=sys.stderr)
            self.send_message(chat_id, f'<a href="{photo_url}">📷 Image link</a>')
        return None

    def send_document(self, chat_id: int, file_path: str, caption: str = "", filename: str = None) -> Optional[int]:
        """Send a document to Telegram chat via multipart upload. Returns message_id."""
        try:
            # Security validation
            if not self._is_safe_file_path(file_path):
                self.send_message(chat_id, f"⚠️ Cannot send file: {file_path} (security check failed)")
                return None

            print(f"[DEBUG] Attempting sendDocument with file: {file_path}", file=sys.stderr)

            # Detect MIME type
            mime_type, _ = mimetypes.guess_type(file_path)
            if not mime_type:
                mime_type = "application/octet-stream"

            # Upload file via multipart
            with open(file_path, 'rb') as f:
                files = {
                    'document': (filename or Path(file_path).name, f, mime_type)
                }
                data = {'chat_id': chat_id}

                if caption:
                    # Sanitize caption (same as images)
                    sanitized_caption = self.sanitize_telegram_html(caption.strip())
                    data['caption'] = sanitized_caption[:1024]  # Telegram limit
                    data['parse_mode'] = 'HTML'

                resp = requests.post(
                    f"{self.api_url}/sendDocument",
                    data=data,
                    files=files,
                    timeout=60  # Longer timeout for file uploads
                )

                if resp.status_code != 200:
                    # Retry without HTML parse_mode if caption failed
                    if caption:
                        data.pop('parse_mode', None)
                        resp = requests.post(
                            f"{self.api_url}/sendDocument",
                            data=data,
                            files={'document': (filename or Path(file_path).name, open(file_path, 'rb'), mime_type)},
                            timeout=60
                        )

                    if resp.status_code != 200:
                        print(f"[WARN] sendDocument failed ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
                        self.send_message(chat_id, f"⚠️ Failed to send file: {Path(file_path).name}")
                        return None

                result = resp.json()
                if result.get("ok"):
                    return result["result"]["message_id"]
        except Exception as e:
            print(f"Error sending document: {e}", file=sys.stderr)
            self.send_message(chat_id, f"⚠️ Error sending file: {str(e)}")
        return None

    def extract_image_urls(self, text: str) -> tuple:
        """Extract image URLs from text/HTML/Markdown.

        Supports:
        - Markdown: ![alt text](URL) - extracts URL and alt text as caption
        - HTML: <img src="URL"/> - extracts URL, no caption
        - Bare URLs: https://example.com/image.png - extracts URL, no caption

        Returns:
            Tuple of (image_data, remaining_text) where:
            - image_data: List of (url, caption) tuples
            - remaining_text: Text with all image references removed
        """
        image_extensions = r'\.(jpg|jpeg|png|gif|webp|bmp|svg)(\?[^\s"<>]*)?'

        # Match markdown images: ![alt text](url)
        md_img_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
        # Match <img> tags
        img_tag_pattern = r'<img\s+[^>]*src=["\']([^"\']+)["\'][^>]*/?\s*>'
        # Match bare image URLs
        bare_url_pattern = r'(https?://[^\s"<>]+' + image_extensions + r')'

        image_data = []  # List of (url, caption) tuples
        remaining = text

        # Extract from markdown images FIRST (preserves alt text before bare URL pattern strips it)
        for match in re.finditer(md_img_pattern, remaining, re.IGNORECASE):
            alt_text = match.group(1).strip()
            url = match.group(2).strip()
            if url not in [img[0] for img in image_data]:
                image_data.append((url, alt_text))
            remaining = remaining.replace(match.group(0), "").strip()

        # Extract from <img> tags
        for match in re.finditer(img_tag_pattern, remaining, re.IGNORECASE):
            url = match.group(1)
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
        file_pattern = r'\[FILE:([^\]:]+)(?::([^\]]*))?\]'

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

    def send_response(self, chat_id: int, text: str, status_msg_id: Optional[int] = None):
        """Send response, detecting image URLs and file paths."""
        print(f"[DEBUG OUTBOUND] send_response -> chat_id={chat_id} text_snippet={repr(text[:200])} status_msg_id={status_msg_id}", file=sys.stderr)
        # Extract images first, then files from remaining text
        image_data, text_after_images = self.extract_image_urls(text)
        file_data, remaining_text = self.extract_file_paths(text_after_images)

        # Handle text portion
        if remaining_text.strip():
            if status_msg_id:
                print(f"[DEBUG OUTBOUND] send_response editing status message {status_msg_id} for chat_id={chat_id}", file=sys.stderr)
                self.edit_message(chat_id, status_msg_id, remaining_text)
                status_msg_id = None
            else:
                print(f"[DEBUG OUTBOUND] send_response sending text to chat_id={chat_id}", file=sys.stderr)
                self.send_message(chat_id, remaining_text)
        elif status_msg_id and (image_data or file_data):
            # No text, just media - delete status message
            try:
                resp = requests.post(
                    f"{self.api_url}/deleteMessage",
                    json={"chat_id": chat_id, "message_id": status_msg_id},
                    timeout=5,
                )
                print(f"[DEBUG OUTBOUND] deleteMessage HTTP {resp.status_code} for chat_id={chat_id} message_id={status_msg_id}", file=sys.stderr)
            except Exception as e:
                print(f"[WARN] deleteMessage failed: {e}", file=sys.stderr)
            status_msg_id = None
        elif status_msg_id:
            # No text, no media - edit status message with default text
            print(f"[DEBUG OUTBOUND] send_response editing status message {status_msg_id} to checkmark for chat_id={chat_id}", file=sys.stderr)
            self.edit_message(chat_id, status_msg_id, "✓")

        # Send images
        for url, caption in image_data:
            print(f"[DEBUG OUTBOUND] send_response sending photo URL={url} caption={repr(caption[:100])} to chat_id={chat_id}", file=sys.stderr)
            self.send_photo(chat_id, url, caption)

        # Send files
        for file_path, caption in file_data:
            print(f"[DEBUG OUTBOUND] send_response sending document path={file_path} caption={repr(caption[:100])} to chat_id={chat_id}", file=sys.stderr)
            self.send_document(chat_id, file_path, caption)

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

            # Ignore messages from bots (including this bot) to avoid handling bot-sent updates which can cause duplicate responses
            if message.get("from", {}).get("is_bot"):
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
                # Block pinned users from changing their agent
                elif cmd_lower.startswith("/agent set") and self.config.is_user_pinned(user_id):
                    pinned_agent = self.config.get_pinned_agent(user_id)
                    self.send_message(
                        chat_id,
                        f"❌ Your agent is pinned to **{pinned_agent}** by an administrator. You cannot change agents.",
                    )
                # Block pinned users from changing their runtime (if a runtime is pinned)
                elif cmd_lower.startswith("/runtime set") and self.config.get_pinned_runtime(user_id):
                    pinned_runtime = self.config.get_pinned_runtime(user_id)
                    self.send_message(
                        chat_id,
                        f"❌ Your runtime is pinned to **{pinned_runtime}** by an administrator. You cannot change runtimes.",
                    )
                # Block pinned users from changing their model (if a model is pinned)
                elif cmd_lower.startswith("/model set") and self.config.get_pinned_model(user_id):
                    pinned_model = self.config.get_pinned_model(user_id)
                    self.send_message(
                        chat_id,
                        f"❌ Your model is pinned to **{pinned_model}** by an administrator. You cannot change models.",
                    )
                # Block unauthorized users from enabling yolo mode
                elif cmd_lower.startswith("/mode yolo") and not self.config.is_yolo_allowed(user_id):
                    self.send_message(chat_id, "❌ You are not authorized to enable YOLO mode.")
                else:
                    # Regular slash commands - get user timeout
                    timeout = self.config.get_user_timeout(user_id)
                    response = self._execute_command(text, session_id, timeout)
                    msg_id = self.send_message(chat_id, response)

                    # Pin agent set messages so user always knows which agent they're talking to
                    if cmd_lower.startswith("/agent set") and msg_id:
                        self.unpin_all_messages(chat_id)
                        self.pin_message(chat_id, msg_id)

                    # Evict cached SessionManager on session-affecting commands
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
                if self.use_api:
                    result_container["response"] = self._execute_via_api(command, session_id, session_id, "telegram")
                else:
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
            # Session ID tied to user ID and bot id (if available)
            if self.bot_id:
                session_id = f"telegram_{self.bot_id}_{user_id}"
            else:
                session_id = f"telegram_{user_id}"
            
            # Use persistent session manager
            session_mgr = self.get_session_manager(session_id)
            
            # Debug: log session info
            print(f"[DEBUG] Using persistent session_mgr for: {session_id}", file=sys.stderr)
            
            # Use execute() which routes to the correct runtime automatically
            if self.use_api:
                result = self._execute_via_api(query, session_id, session_id, "telegram")
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
