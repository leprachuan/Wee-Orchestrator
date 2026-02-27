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
import mimetypes
import ssl
from pathlib import Path
from urllib.parse import unquote
from typing import Optional, Dict, List
from datetime import datetime, timedelta
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
            "pinned_users": {},       # Maps person_id (str) to {"agent": "name"} - locks user to that agent
            "yolo_allowed_users": [], # Person IDs permitted to enable /mode yolo; empty = all allowed
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

    def is_user_pinned(self, person_id: str) -> bool:
        """Check if user is pinned to a specific agent"""
        return person_id in self.config.get("pinned_users", {})

    def get_pinned_agent(self, person_id: str) -> Optional[str]:
        """Get the pinned agent for a user, or None if not pinned"""
        pinned = self.config.get("pinned_users", {}).get(person_id)
        if pinned:
            return pinned.get("agent")
        return None

    def get_pinned_runtime(self, person_id: str) -> Optional[str]:
        """Get the pinned runtime for a user, or None if not set"""
        pinned = self.config.get("pinned_users", {}).get(person_id)
        if pinned:
            return pinned.get("runtime")
        return None

    def get_pinned_model(self, person_id: str) -> Optional[str]:
        """Get the pinned model for a user, or None if not set"""
        pinned = self.config.get("pinned_users", {}).get(person_id)
        if pinned:
            return pinned.get("model")
        return None

    def is_yolo_allowed(self, person_id: str) -> bool:
        """Check if user is permitted to enable /mode yolo.
        If yolo_allowed_users is empty, all users are allowed (backward compatible)."""
        yolo_users = self.config.get("yolo_allowed_users", [])
        if not yolo_users:
            return True
        return person_id in yolo_users


class WebEXConnector:
    """Main WebEX connector class - listens to RabbitMQ queue"""

    def __init__(self, token: str, config_file: str = "webex_config.json"):
        """
        Initialize WebEX connector

        Args:
            token: WebEX bot token
            config_file: Path to configuration file
        """
        self.config = WebEXConfig(config_file)

        # Prefer config file token over env var (env var may be stale)
        config_token = self.config.config.get("token", "")
        self.token = config_token if config_token else token

        # Keep persistent SessionManager per session_id for context persistence
        self.session_managers = {}  # {session_id: SessionManager}

        # Save token to config if only provided via env/arg
        if token and not config_token:
            self.config.config["token"] = token
            self.config.save()

        # API mode configuration
        self.use_api = os.getenv("USE_API", "false").lower() == "true"
        self.api_url = os.getenv("API_URL", "http://127.0.0.1:8001")
        self.api_shared_key = os.getenv("API_SHARED_KEY", "")

        self.running = False
        self.rabbitmq_connection = None
        self.rabbitmq_channel = None
        self.cleanup_thread = None

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

    def _enforce_pinned_session(self, person_id: str, session_id: str):
        """For pinned users, push pinned agent/runtime/model into the SessionManager.
        Must be called before every query or command so the SessionManager's session
        data always reflects the pinned values regardless of what the user has set."""
        if not self.config.is_user_pinned(person_id):
            return
        session_mgr = self.get_session_manager(session_id)
        pinned_agent = self.config.get_pinned_agent(person_id)
        if pinned_agent:
            session_mgr.update_session_field(session_id, "agent", pinned_agent)
        pinned_runtime = self.config.get_pinned_runtime(person_id)
        if pinned_runtime:
            session_mgr.update_session_field(session_id, "runtime", pinned_runtime)
        pinned_model = self.config.get_pinned_model(person_id)
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

            # Create session with our session_id so it's properly tracked
            create_resp = requests.post(
                f"{self.api_url}/api/v1/sessions/create",
                headers=headers,
                json={"session_id": session_id},
                timeout=10,
            )
            if create_resp.status_code != 200:
                print(f"[WARN] Session create failed ({create_resp.status_code}): {create_resp.text}", file=sys.stderr)

            # Execute the query
            resp = requests.post(
                f"{self.api_url}/api/v1/sessions/{session_id}/execute",
                headers=headers,
                json={"query": query},
                timeout=600,
            )

            if resp.status_code == 200:
                return resp.json().get("response", "No response from API")
            else:
                print(f"[WARN] API request failed ({resp.status_code}): {resp.text}", file=sys.stderr)
                session_mgr = self.get_session_manager(session_id)
                return session_mgr.execute(query, session_id)
        except Exception as e:
            print(f"[WARN] API request exception: {e}, falling back to direct mode", file=sys.stderr)
            session_mgr = self.get_session_manager(session_id)
            return session_mgr.execute(query, session_id)

    def connect_rabbitmq(self) -> bool:
        """Connect to RabbitMQ with optional SSL and DNS override support"""
        try:
            credentials = pika.PlainCredentials(
                self.config.config["rabbitmq_user"],
                self.config.config["rabbitmq_password"]
            )
            
            # Get connection parameters
            host = self.config.config["rabbitmq_host"]
            port = self.config.config["rabbitmq_port"]
            
            # Fix 3: Use rabbitmq_host_ip if provided for direct IP connection (bypass DNS)
            host_ip = self.config.config.get("rabbitmq_host_ip") or host
            
            # Fix 2: SSL/TLS support - auto-detect on port 5671 or use rabbitmq_ssl config
            ssl_options = None
            use_ssl = self.config.config.get("rabbitmq_ssl", port == 5671)
            if use_ssl:
                ctx = ssl.create_default_context()
                # Fix 2: Allow optional SSL verification disable via rabbitmq_ssl_verify
                if not self.config.config.get("rabbitmq_ssl_verify", True):
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                ssl_options = pika.SSLOptions(ctx, host)  # SNI uses original hostname
            
            parameters = pika.ConnectionParameters(
                host=host_ip,  # Use IP for TCP connection (DNS bypass)
                port=port,
                virtual_host=self.config.config["rabbitmq_vhost"],
                credentials=credentials,
                ssl_options=ssl_options,
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

            ssl_info = " (SSL/TLS enabled)" if use_ssl else ""
            print(f"✅ Connected to RabbitMQ on {host_ip}:{port}{ssl_info}", file=sys.stderr)
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

    def send_message(self, room_id: str, text: str) -> Optional[str]:
        """Send message to WebEX room/person via API. Returns message ID of last chunk sent.
        
        Fix 4: Supports both roomId and toPersonEmail based on destination format.
        If room_id contains '@', treats it as email address (toPersonEmail).
        Otherwise treats it as room ID (roomId).
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }

            # Split into chunks if too long
            max_len = 4000  # WebEX message length limit
            chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] if text else ["No response"]

            last_msg_id = None
            for chunk in chunks:
                # Fix 4: Determine if destination is email or room ID
                if "@" in room_id:
                    # Email address - use toPersonEmail
                    data = {
                        "toPersonEmail": room_id,
                        "text": chunk,
                        "markdown": chunk
                    }
                else:
                    # Room ID - use roomId
                    data = {
                        "roomId": room_id,
                        "text": chunk,
                        "markdown": chunk
                    }

                response = requests.post(
                    "https://webexapis.com/v1/messages",
                    headers=headers,
                    json=data,
                    timeout=10
                )

                if response.status_code != 200:
                    print(f"[WARN] WebEX send failed ({response.status_code}): {response.text[:200]}", file=sys.stderr)
                    return None

                # Extract message ID from response
                resp_json = response.json()
                if resp_json and "id" in resp_json:
                    last_msg_id = resp_json["id"]

            return last_msg_id
        except Exception as e:
            print(f"Error sending WebEX message: {e}", file=sys.stderr)
            return None

    def edit_message(self, message_id: str, room_id: str, text: str) -> bool:
        """Edit a message in WebEX. Returns True if successful."""
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }

            data = {
                "roomId": room_id,
                "text": text,
                "markdown": text
            }

            print(f"[DEBUG] Attempting to edit message {message_id} in room {room_id} with text: {text[:100]}", file=sys.stderr)
            response = requests.put(
                f"https://webexapis.com/v1/messages/{message_id}",
                headers=headers,
                json=data,
                timeout=10
            )

            print(f"[DEBUG] Edit response status: {response.status_code}", file=sys.stderr)
            if response.status_code == 200:
                print(f"[DEBUG] Message edit successful", file=sys.stderr)
                return True
            else:
                print(f"[WARN] WebEX edit failed ({response.status_code}): {response.text[:200]}", file=sys.stderr)
                return False
        except Exception as e:
            print(f"[ERROR] Error editing WebEX message: {e}", file=sys.stderr)
            return False

    def send_typing(self, room_id: str) -> bool:
        """Send typing indicator to WebEX room. Returns True if successful."""
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }

            data = {"roomId": room_id}

            response = requests.post(
                "https://webexapis.com/v1/messages/typing",
                headers=headers,
                json=data,
                timeout=10
            )

            if response.status_code == 204:
                return True
            else:
                # Typing indicator might not be supported by all WebEX instances
                print(f"[DEBUG] WebEX typing indicator: {response.status_code}", file=sys.stderr)
                return False
        except Exception as e:
            print(f"[DEBUG] Typing indicator not available: {e}", file=sys.stderr)
            return False

    def send_file(self, room_id: str, file_path: str, caption: str = "") -> Optional[str]:
        """Send a file to WebEX room via multipart upload. Returns message ID."""
        try:
            # Security validation
            if not self._is_safe_file_path(file_path):
                self.send_message(room_id, f"⚠️ Cannot send file: {file_path} (security check failed)")
                return None

            print(f"[DEBUG] Attempting to send file to WebEX: {file_path}", file=sys.stderr)

            # Detect MIME type
            mime_type, _ = mimetypes.guess_type(file_path)
            if not mime_type:
                mime_type = "application/octet-stream"

            headers = {
                "Authorization": f"Bearer {self.token}",
            }

            # Upload file via multipart
            with open(file_path, 'rb') as f:
                files = {
                    'files': (Path(file_path).name, f, mime_type)
                }
                data = {'roomId': room_id}

                if caption:
                    data['text'] = caption

                response = requests.post(
                    "https://webexapis.com/v1/messages",
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=60  # Longer timeout for file uploads
                )

                if response.status_code != 200:
                    print(f"[WARN] WebEX file send failed ({response.status_code}): {response.text[:200]}", file=sys.stderr)
                    self.send_message(room_id, f"⚠️ Failed to send file: {Path(file_path).name}")
                    return None

                result = response.json()
                if result and "id" in result:
                    return result["id"]
        except Exception as e:
            print(f"Error sending file to WebEX: {e}", file=sys.stderr)
            self.send_message(room_id, f"⚠️ Error sending file: {str(e)}")
        return None

    def _send_image_url(self, room_id: str, url: str, caption: str = "") -> Optional[str]:
        """Send an image to WebEX room via external URL. Returns message ID."""
        try:
            print(f"[DEBUG] Sending image URL to WebEX: {url[:100]}", file=sys.stderr, flush=True)
            headers = {"Authorization": f"Bearer {self.token}"}
            data = {"roomId": room_id, "files": [url]}
            if caption:
                data["text"] = caption
            response = requests.post(
                "https://webexapis.com/v1/messages",
                headers=headers,
                json=data,
                timeout=30,
            )
            print(f"[DEBUG] WebEX image URL response: {response.status_code}", file=sys.stderr, flush=True)
            if response.status_code == 200:
                result = response.json()
                return result.get("id")
            else:
                print(f"[WARN] WebEX image URL send failed ({response.status_code}): {response.text[:200]}", file=sys.stderr, flush=True)
                # Fallback: send as markdown link
                self.send_message(room_id, f"[📷 Image]({url})" + (f" - {caption}" if caption else ""))
        except Exception as e:
            print(f"[ERROR] Exception sending image URL to WebEX: {e}", file=sys.stderr, flush=True)
            self.send_message(room_id, f"[📷 Image]({url})" + (f" - {caption}" if caption else ""))
        return None

    def _send_image_file(self, room_id: str, file_path: str, caption: str = "") -> Optional[str]:
        """Send a local image file to WebEX room via multipart upload. Returns message ID.
        
        Converts PNG images to JPEG for reliable inline preview in WebEx client.
        """
        try:
            print(f"[DEBUG] Uploading local image to WebEX: {file_path} (size={os.path.getsize(file_path)})", file=sys.stderr, flush=True)
            headers = {"Authorization": f"Bearer {self.token}"}
            data = {'roomId': room_id}
            if caption:
                data['text'] = caption

            # Convert PNG to JPEG for reliable WebEx inline preview
            file_ext = Path(file_path).suffix.lower()
            if file_ext == '.png':
                try:
                    from PIL import Image
                    import io
                    img = Image.open(file_path)
                    if img.mode == 'RGBA':
                        # Flatten alpha onto white background
                        bg = Image.new('RGB', img.size, (255, 255, 255))
                        bg.paste(img, mask=img.split()[3])
                        img = bg
                    elif img.mode != 'RGB':
                        img = img.convert('RGB')
                    buf = io.BytesIO()
                    img.save(buf, format='JPEG', quality=85)
                    buf.seek(0)
                    jpeg_name = Path(file_path).stem + '.jpg'
                    print(f"[DEBUG] Converted PNG->JPEG: {os.path.getsize(file_path)} -> {buf.getbuffer().nbytes} bytes", file=sys.stderr, flush=True)
                    files = {'files': (jpeg_name, buf, 'image/jpeg')}
                    response = requests.post(
                        "https://webexapis.com/v1/messages",
                        headers=headers, data=data, files=files, timeout=60,
                    )
                except ImportError:
                    print(f"[DEBUG] PIL not available, sending PNG as-is", file=sys.stderr, flush=True)
                    with open(file_path, 'rb') as f:
                        files = {'files': (Path(file_path).name, f, 'image/png')}
                        response = requests.post(
                            "https://webexapis.com/v1/messages",
                            headers=headers, data=data, files=files, timeout=60,
                        )
            else:
                mime_type, _ = mimetypes.guess_type(file_path)
                if not mime_type:
                    mime_type = "image/jpeg"
                with open(file_path, 'rb') as f:
                    files = {'files': (Path(file_path).name, f, mime_type)}
                    response = requests.post(
                        "https://webexapis.com/v1/messages",
                        headers=headers, data=data, files=files, timeout=60,
                    )

            print(f"[DEBUG] WebEX image upload response: {response.status_code}", file=sys.stderr, flush=True)
            if response.status_code == 200:
                result = response.json()
                msg_id = result.get("id")
                print(f"[DEBUG] WebEX image sent successfully, msg_id={msg_id}", file=sys.stderr, flush=True)
                return msg_id
            else:
                print(f"[WARN] WebEX image file send failed ({response.status_code}): {response.text[:500]}", file=sys.stderr, flush=True)
                self.send_message(room_id, f"⚠️ Failed to send image: {Path(file_path).name}")
        except Exception as e:
            print(f"[ERROR] Exception sending image file to WebEX: {e}", file=sys.stderr, flush=True)
            self.send_message(room_id, f"⚠️ Error sending image: {str(e)}")
        return None

    def _resolve_image_path(self, url: str) -> str:
        """Resolve /ai-media/ paths to local filesystem paths and strip ANSI codes.
        
        Handles LLM-mangled session IDs by fuzzy-matching directory names
        when the exact path doesn't exist.
        """
        # Strip any ANSI escape codes that might leak from CLI output
        url = re.sub(r'\x1b\[[0-9;]*m', '', url)
        if url.startswith("/ai-media/"):
            resolved = url.replace("/ai-media/", "/tmp/webui_ai_media/", 1)
            # If exact path exists, use it
            if os.path.exists(resolved):
                return resolved
            # LLM may mangle the session ID in the path — try fuzzy directory match
            base_dir = "/tmp/webui_ai_media"
            parts = resolved[len(base_dir) + 1:].split("/", 1)  # [session_dir, filename]
            if len(parts) == 2:
                session_dir_name, filename = parts
                try:
                    # Find directories that share a long common prefix with the mangled name
                    candidates = []
                    for d in os.listdir(base_dir):
                        if not os.path.isdir(os.path.join(base_dir, d)):
                            continue
                        # Check if first 20 chars match (enough to identify the session)
                        prefix_len = min(20, len(session_dir_name), len(d))
                        if d[:prefix_len] == session_dir_name[:prefix_len]:
                            candidate_path = os.path.join(base_dir, d, filename)
                            if os.path.isfile(candidate_path):
                                candidates.append(candidate_path)
                    if len(candidates) == 1:
                        print(f"[DEBUG] Fuzzy-matched image path: {resolved} -> {candidates[0]}", file=sys.stderr, flush=True)
                        return candidates[0]
                    elif len(candidates) > 1:
                        # Multiple matches — pick most recent
                        best = max(candidates, key=os.path.getmtime)
                        print(f"[DEBUG] Fuzzy-matched image path (newest of {len(candidates)}): {resolved} -> {best}", file=sys.stderr, flush=True)
                        return best
                except OSError:
                    pass
            return resolved
        return url

    def extract_image_urls(self, text: str) -> tuple:
        """Extract image URLs from text/HTML/Markdown and return (image_data, remaining_text).
        
        Supports:
        - ![caption](url) - Markdown syntax with caption
        - <img src="url"/> - HTML img tags
        - Bare URL - https://example.com/image.jpg
        - Local paths - /ai-media/session/image.png (mapped to /tmp/webui_ai_media/)
        
        Returns:
            Tuple of (image_data, remaining_text) where:
            - image_data: List of (url, caption) tuples
            - remaining_text: Text with image references removed
        """
        image_data = []
        remaining = text

        # Match markdown syntax: ![caption](url)
        md_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
        for match in re.finditer(md_pattern, remaining):
            caption = match.group(1).strip()
            url = self._resolve_image_path(match.group(2).strip())
            if url not in [img[0] for img in image_data]:
                image_data.append((url, caption))
            remaining = remaining.replace(match.group(0), "").strip()

        # Match <img> tags
        img_tag_pattern = r'<img\s+[^>]*src=["\']([^"\']+)["\'][^>]*/?\s*>'
        for match in re.finditer(img_tag_pattern, remaining, re.IGNORECASE):
            url = self._resolve_image_path(match.group(1).strip())
            if url not in [img[0] for img in image_data]:
                image_data.append((url, ""))
            remaining = remaining.replace(match.group(0), "").strip()

        # Match bare URLs (http/https ending in image extensions)
        url_pattern = r'(https?://[^\s\[\]<>"]+\.(?:jpg|jpeg|png|gif|webp)(?:\?[^\s<>"]*)?)'
        for match in re.finditer(url_pattern, remaining, re.IGNORECASE):
            url = match.group(1)
            if url not in [img[0] for img in image_data]:
                image_data.append((url, ""))
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

    def _is_safe_file_path(self, file_path: str) -> bool:
        """Validate file path for security.

        Checks:
        - File exists
        - File is within allowed directories (webex_downloads, /tmp/webui_ai_media)
        - No path traversal attacks
        - File size within limits (100MB - WebEX limit)
        """
        try:
            allowed_dirs = [
                Path("/opt/n8n-copilot-shim-dev/webex_downloads").resolve(),
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
                print(f"[WARN] File outside allowed directories: {file_path}", file=sys.stderr)
                return False

            # Check file size (100MB limit - WebEX max)
            if file_path_obj.stat().st_size > 100 * 1024 * 1024:
                print(f"[WARN] File exceeds 100MB limit: {file_path}", file=sys.stderr)
                return False

            return True
        except Exception as e:
            print(f"[WARN] Error validating file path: {e}", file=sys.stderr)
            return False

    def download_file(self, file_url: str, person_id: str) -> Optional[tuple]:
        """Download file from WebEX and store it. Returns (file_path, filename) tuple or None."""
        try:
            headers = {
                "Authorization": f"Bearer {self.token}"
            }

            print(f"[DEBUG] Downloading file from: {file_url}", file=sys.stderr)

            # Download file (requires authentication)
            response = requests.get(file_url, headers=headers, timeout=30)

            if response.status_code == 200:
                # Create downloads directory
                downloads_dir = Path("/opt/n8n-copilot-shim-dev/webex_downloads")
                downloads_dir.mkdir(exist_ok=True)

                # Extract filename from Content-Disposition header
                filename = "file"
                content_disp = response.headers.get("Content-Disposition", "")
                if "filename=" in content_disp:
                    # Parse filename from header
                    filename = content_disp.split("filename=")[1].strip('"').strip("'")
                    # URL-decode the filename (handles %E2%80%AF, +, etc.)
                    filename = unquote(filename).replace('+', ' ')
                else:
                    # Fallback: use timestamp
                    filename = f"file_{int(time.time())}"

                # Check file size (100MB limit - WebEX max)
                if len(response.content) > 100 * 1024 * 1024:
                    print(f"[WARN] File exceeds 100MB limit", file=sys.stderr)
                    return None

                # Save with person_id prefix
                local_path = downloads_dir / f"{person_id}_{filename}"
                with open(local_path, "wb") as f:
                    f.write(response.content)

                # Make world readable
                os.chmod(local_path, 0o644)

                print(f"[DEBUG] File saved to: {local_path}", file=sys.stderr)
                return (str(local_path), filename)

            elif response.status_code == 410:
                print(f"[WARN] File was infected and removed by WebEX", file=sys.stderr)
                return None

            elif response.status_code == 428:
                # File is not scannable (encrypted) - retry with allow=unscannable
                print(f"[DEBUG] File not scannable, retrying with allow=unscannable", file=sys.stderr)
                response = requests.get(
                    f"{file_url}?allow=unscannable",
                    headers=headers,
                    timeout=30
                )
                if response.status_code == 200:
                    # Repeat save logic above
                    downloads_dir = Path("/opt/n8n-copilot-shim-dev/webex_downloads")
                    downloads_dir.mkdir(exist_ok=True)

                    filename = "file"
                    content_disp = response.headers.get("Content-Disposition", "")
                    if "filename=" in content_disp:
                        filename = content_disp.split("filename=")[1].strip('"').strip("'")
                        # URL-decode the filename (handles %E2%80%AF, +, etc.)
                        filename = unquote(filename).replace('+', ' ')
                    else:
                        filename = f"file_{int(time.time())}"

                    # Check file size
                    if len(response.content) > 100 * 1024 * 1024:
                        print(f"[WARN] File exceeds 100MB limit", file=sys.stderr)
                        return None

                    local_path = downloads_dir / f"{person_id}_{filename}"
                    with open(local_path, "wb") as f:
                        f.write(response.content)

                    os.chmod(local_path, 0o644)
                    print(f"[DEBUG] Unscannable file saved to: {local_path}", file=sys.stderr)
                    return (str(local_path), filename)

            print(f"[WARN] File download failed: HTTP {response.status_code}", file=sys.stderr)
            return None

        except Exception as e:
            print(f"Error downloading file: {e}", file=sys.stderr)
            return None

    def start_cleanup_background_task(self, interval_seconds: int = 300):
        """Start background cleanup task (runs every 5 minutes by default).

        Removes WebEX temp files older than 5 minutes from /tmp and webex_downloads/
        This avoids race conditions with agents that are still processing files.
        """
        def cleanup_old_files():
            while self.running:
                try:
                    current_time = datetime.now()
                    max_age = timedelta(minutes=5)

                    # Clean up temp files in /tmp
                    tmp_dir = Path("/tmp")
                    if tmp_dir.exists():
                        for tmp_file in tmp_dir.glob("webex_*.png"):
                            try:
                                file_age = current_time - datetime.fromtimestamp(tmp_file.stat().st_mtime)
                                if file_age > max_age:
                                    tmp_file.unlink()
                                    print(f"[DEBUG] Cleaned up old temp file: {tmp_file}", file=sys.stderr)
                            except Exception as e:
                                pass  # Silently ignore errors (file may have been deleted)

                    # Clean up downloaded files in webex_downloads/
                    downloads_dir = Path("/opt/n8n-copilot-shim-dev/webex_downloads")
                    if downloads_dir.exists():
                        for file in downloads_dir.glob("*_*"):
                            try:
                                file_age = current_time - datetime.fromtimestamp(file.stat().st_mtime)
                                if file_age > max_age:
                                    file.unlink()
                                    print(f"[DEBUG] Cleaned up old download: {file}", file=sys.stderr)
                            except Exception as e:
                                pass  # Silently ignore errors

                    # Sleep before next cleanup cycle
                    time.sleep(interval_seconds)
                except Exception as e:
                    print(f"Error in cleanup thread: {e}", file=sys.stderr)
                    time.sleep(interval_seconds)

        self.cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
        self.cleanup_thread.start()
        print(f"[DEBUG] Started background cleanup task (interval: {interval_seconds}s)", file=sys.stderr)

    def pin_message(self, message_id: str, room_id: str) -> bool:
        """Pin a message in WebEX room and set as banner. Returns True if successful."""
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }

            # WebEX pinning: PUT /v1/messages/{id}/pin with roomId in body
            data = {"roomId": room_id}
            response = requests.put(
                f"https://webexapis.com/v1/messages/{message_id}/pin",
                headers=headers,
                json=data,
                timeout=10
            )

            if response.status_code in [200, 204]:
                print(f"[DEBUG] Message pinned successfully", file=sys.stderr)
                return True
            else:
                print(f"[DEBUG] WebEX pin failed ({response.status_code}): {response.text[:200]}", file=sys.stderr)
                return False
        except Exception as e:
            print(f"[DEBUG] Pin error: {e}", file=sys.stderr)
            return False

    def unpin_all_messages(self, room_id: str) -> bool:
        """Unpin all messages in a room. Returns True if successful."""
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }

            response = requests.put(
                f"https://webexapis.com/v1/rooms/{room_id}/unpinAllMessages",
                headers=headers,
                timeout=10
            )

            if response.status_code in [200, 204]:
                print(f"[DEBUG] All messages unpinned successfully", file=sys.stderr)
                return True
            else:
                print(f"[DEBUG] WebEX unpin all failed ({response.status_code})", file=sys.stderr)
                return False
        except Exception as e:
            print(f"[DEBUG] Unpin error: {e}", file=sys.stderr)
            return False

    def send_response(self, room_id: str, text: str, status_msg_id: Optional[str] = None):
        """Send response, detecting image URLs and file paths.
        
        Mirrors Telegram pattern: extracts images and files, sends text portion first,
        then sends media items. If status_msg_id exists, edits it with text portion.
        """
        print(f"[DEBUG OUTBOUND] send_response -> room_id={room_id} text_snippet={repr(text[:200])} status_msg_id={status_msg_id}", file=sys.stderr)
        
        # Extract images first, then files from remaining text
        image_data, text_after_images = self.extract_image_urls(text)
        file_data, remaining_text = self.extract_file_paths(text_after_images)

        # Handle text portion
        if remaining_text.strip():
            if status_msg_id:
                print(f"[DEBUG OUTBOUND] send_response editing status message {status_msg_id} for room_id={room_id}", file=sys.stderr)
                self.edit_message(status_msg_id, room_id, remaining_text)
                status_msg_id = None
            else:
                print(f"[DEBUG OUTBOUND] send_response sending text to room_id={room_id}", file=sys.stderr)
                self.send_message(room_id, remaining_text)
        elif status_msg_id and (image_data or file_data):
            # No text, just media - delete status message by editing to empty
            try:
                # WebEX doesn't support deleting messages, so edit to indicate completion
                self.edit_message(status_msg_id, room_id, "✓")
                print(f"[DEBUG OUTBOUND] Edited status message to checkmark for room_id={room_id}", file=sys.stderr)
            except Exception as e:
                print(f"[WARN] Could not edit status message: {e}", file=sys.stderr)
            status_msg_id = None
        elif status_msg_id:
            # No text, no media - edit status message with default text
            print(f"[DEBUG OUTBOUND] send_response editing status message {status_msg_id} to checkmark for room_id={room_id}", file=sys.stderr)
            self.edit_message(status_msg_id, room_id, "✓")

        # Send images
        for url, caption in image_data:
            # Strip any residual ANSI codes from URL
            url = re.sub(r'\x1b\[[0-9;]*m', '', url)
            print(f"[DEBUG OUTBOUND] send_response sending image URL={url} caption={repr(caption[:100])} to room_id={room_id}", file=sys.stderr, flush=True)
            print(f"[DEBUG OUTBOUND] -> url repr: {repr(url)}", file=sys.stderr, flush=True)
            if url.startswith(('http://', 'https://')):
                # External URL: send as file URL attachment
                print(f"[DEBUG OUTBOUND] -> dispatching _send_image_url", file=sys.stderr, flush=True)
                self._send_image_url(room_id, url, caption)
            else:
                # Local file path - check with retry for potential race condition
                file_found = os.path.isfile(url)
                if not file_found:
                    # Retry after short delay in case file is still being written
                    print(f"[DEBUG OUTBOUND] -> file not found on first check, retrying in 2s...", file=sys.stderr, flush=True)
                    print(f"[DEBUG OUTBOUND] -> dir exists: {os.path.isdir(os.path.dirname(url))}, dir contents: {os.listdir(os.path.dirname(url)) if os.path.isdir(os.path.dirname(url)) else 'N/A'}", file=sys.stderr, flush=True)
                    time.sleep(2)
                    file_found = os.path.isfile(url)
                    print(f"[DEBUG OUTBOUND] -> after retry: isfile={file_found}", file=sys.stderr, flush=True)
                if file_found:
                    print(f"[DEBUG OUTBOUND] -> dispatching _send_image_file (size={os.path.getsize(url)})", file=sys.stderr, flush=True)
                    result = self._send_image_file(room_id, url, caption)
                    print(f"[DEBUG OUTBOUND] -> _send_image_file returned: {result}", file=sys.stderr, flush=True)
                else:
                    # Unresolved path: send as text link
                    print(f"[DEBUG OUTBOUND] -> image path not found after retry, sending text fallback", file=sys.stderr, flush=True)
                    self.send_message(room_id, f"[Image]({url})" + (f" - {caption}" if caption else ""))

        # Send files
        for file_path, caption in file_data:
            print(f"[DEBUG OUTBOUND] send_response sending file path={file_path} caption={repr(caption[:100])} to room_id={room_id}", file=sys.stderr)
            self.send_file(room_id, file_path, caption)


    def handle_message(self, message_data: Dict):
        """Process incoming WebEX message from RabbitMQ"""
        try:
            person_id = message_data.get("personId")
            room_id = message_data.get("roomId")
            text = message_data.get("text", "").strip()
            person_email = message_data.get("personEmail", "unknown")
            files = message_data.get("files", [])

            # Handle files with optional caption
            file_path = None
            file_name = None
            if files:
                # WebEX supports one file per message
                file_url = files[0]
                file_result = self.download_file(file_url, person_id)

                if file_result:
                    file_path, file_name = file_result
                    # Sanitize filename - remove spaces and special chars for shell safety
                    # Keep extension, use underscores for safety
                    safe_filename = f"webex_{person_id}_{int(time.time())}.png"

                    # Copy file to /tmp for universal accessibility
                    # (avoids sandboxing issues with /opt)
                    tmp_file_path = Path("/tmp") / safe_filename
                    try:
                        # Copy the file to /tmp
                        with open(file_path, "rb") as src:
                            with open(tmp_file_path, "wb") as dst:
                                dst.write(src.read())

                        # Make world readable
                        os.chmod(tmp_file_path, 0o644)
                        print(f"[DEBUG] File copied to temp: {tmp_file_path}", file=sys.stderr)
                        file_path = tmp_file_path
                    except Exception as e:
                        print(f"[WARN] Could not copy to /tmp: {e}, using original path", file=sys.stderr)

                    # Add file path to query
                    if not text:
                        text = f"Please analyze this file: {file_path}"
                    else:
                        text = f"{text}\n\nFile to analyze: {file_path}"
                    print(f"[DEBUG] File query: {text[:200]}", file=sys.stderr)
                else:
                    self.send_message(room_id, "❌ Failed to download file")
                    return

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
                # Create new session - use pinned agent if configured
                default_agent = self.config.config["default_agent"]
                pinned_agent = self.config.get_pinned_agent(person_id)
                if pinned_agent:
                    default_agent = pinned_agent
                session_info = {
                    "person_id": person_id,
                    "email": person_email,
                    "paired_at": datetime.now().isoformat(),
                    "agent": default_agent,
                    "model": self.config.config["default_model"],
                }
                self.config.set_user_session(person_id, session_info)
            elif self.config.is_user_pinned(person_id):
                # Enforce pinned agent even for existing sessions
                pinned_agent = self.config.get_pinned_agent(person_id)
                if pinned_agent and session_info.get("agent") != pinned_agent:
                    session_info["agent"] = pinned_agent
                    self.config.set_user_session(person_id, session_info)

            session_id = f"webex_{person_id}"

            # Push pinned agent/runtime/model into SessionManager before every message
            self._enforce_pinned_session(person_id, session_id)

            # Handle slash commands
            if text.startswith("/"):
                cmd_lower = text.lower().strip()
                if cmd_lower.startswith("/timeout"):
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
                    msg_id = self.send_message(room_id, response)
                # Block pinned users from changing their agent
                elif cmd_lower.startswith("/agent set") and self.config.is_user_pinned(person_id):
                    pinned_agent = self.config.get_pinned_agent(person_id)
                    self.send_message(
                        room_id,
                        f"❌ Your agent is pinned to **{pinned_agent}** by an administrator. You cannot change agents.",
                    )
                # Block pinned users from changing their runtime (if a runtime is pinned)
                elif cmd_lower.startswith("/runtime set") and self.config.get_pinned_runtime(person_id):
                    pinned_runtime = self.config.get_pinned_runtime(person_id)
                    self.send_message(
                        room_id,
                        f"❌ Your runtime is pinned to **{pinned_runtime}** by an administrator. You cannot change runtimes.",
                    )
                # Block pinned users from changing their model (if a model is pinned)
                elif cmd_lower.startswith("/model set") and self.config.get_pinned_model(person_id):
                    pinned_model = self.config.get_pinned_model(person_id)
                    self.send_message(
                        room_id,
                        f"❌ Your model is pinned to **{pinned_model}** by an administrator. You cannot change models.",
                    )
                # Block unauthorized users from enabling yolo mode
                elif cmd_lower.startswith("/mode yolo") and not self.config.is_yolo_allowed(person_id):
                    self.send_message(room_id, "❌ You are not authorized to enable YOLO mode.")
                else:
                    # Regular slash commands
                    timeout = self.config.get_user_timeout(person_id)
                    response = self._execute_command(text, session_id, timeout)
                    msg_id = self.send_message(room_id, response)

                    # Pin configuration commands (agent, runtime, model, session)
                    if msg_id and any(cmd_lower.startswith(cmd) for cmd in ["/agent set", "/runtime set", "/model set", "/session reset"]):
                        self.pin_message(msg_id, room_id)
                        print(f"[DEBUG] Pinned configuration command message: {cmd_lower[:30]}", file=sys.stderr)

                    # Evict cached SessionManager on session-affecting commands
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
                    response, status_msg_id = self._query_agent_with_status(
                        text, session_info["agent"], session_info["model"], person_id, room_id, timeout
                    )
                    self.send_response(room_id, response, status_msg_id)

        except Exception as e:
            print(f"Error handling message: {e}", file=sys.stderr)
            if room_id:
                self.send_message(room_id, f"❌ Error: {str(e)[:100]}")

    def _execute_command(self, command: str, session_id: str, timeout: int = 300) -> str:
        """Execute slash command via agent_manager.execute() with timeout support"""
        result_container = {"response": None, "done": False}

        def run_command():
            try:
                if self.use_api:
                    result_container["response"] = self._execute_via_api(command, session_id, session_id, "webex")
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
    ) -> tuple:
        """Query agent with status updates at 30s intervals.

        Returns (response_text, status_msg_id) where status_msg_id tracks
        the status message for potential editing with the final response.
        """
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
        status_msg_id = None  # Track the status message
        while not result_container["done"] and elapsed < timeout:
            # Send typing indicator every 5 seconds to keep it alive
            if elapsed % 5 == 0:
                self.send_typing(room_id)

            if elapsed == 30:
                # First status at 30s - send new message
                status_msg_id = self.send_message(room_id, status_msgs[0])
                self.send_typing(room_id)
                status_idx = 1
            elif elapsed > 30 and (elapsed - 30) % 30 == 0:
                # Edit status message every 30s with new message
                msg = status_msgs[status_idx % len(status_msgs)]
                if status_msg_id:
                    self.edit_message(status_msg_id, room_id, msg)
                else:
                    status_msg_id = self.send_message(room_id, msg)
                self.send_typing(room_id)
                status_idx += 1

            time.sleep(1)
            elapsed += 1

        query_thread.join(timeout=5)
        return (result_container["response"] or "Error: Query timed out", status_msg_id)

    def _query_agent(
        self, query: str, agent: str, model: str, person_id: str, timeout: int = 300
    ) -> str:
        """Query the agent_manager with user session tied to person ID"""
        try:
            session_id = f"webex_{person_id}"

            print(f"[DEBUG] Using persistent session_mgr for: {session_id}", file=sys.stderr)

            if self.use_api:
                result = self._execute_via_api(query, session_id, session_id, "webex")
            else:
                session_mgr = self.get_session_manager(session_id)
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
                    
                    # Fix 1: Support configurable payload unwrapping for gateway wrappers
                    payload_key = self.config.config.get("rabbitmq_payload_key")
                    if payload_key and payload_key in message_data and isinstance(message_data[payload_key], dict):
                        message_data = message_data[payload_key]
                        print(f"[DEBUG] Unwrapped payload from key '{payload_key}'", file=sys.stderr)
                    
                    self.handle_message(message_data)
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                except Exception as e:
                    print(f"Error processing queue message: {e}", file=sys.stderr)
                    ch.basic_nack(delivery_tag=method.delivery_tag)

            if not self.connect_rabbitmq():
                print("Failed to connect to RabbitMQ, exiting...", file=sys.stderr)
                return

            # Start background cleanup task (removes files older than 5 minutes)
            self.start_cleanup_background_task(interval_seconds=300)

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
