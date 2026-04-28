#!/usr/bin/env python3
"""
WebEX Connector for N8N Copilot Shim
Bridges WebEX messages from RabbitMQ queue with the agent_manager.py
Mirrors Telegram listener architecture but uses RabbitMQ as message source
"""

import json
import logging
import mimetypes
import os
import re
import ssl
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import unquote

import pika
import requests

import agent_manager
import audio_transcriber
from base_connector import BaseConfig, BaseConnector

logger = logging.getLogger(__name__)


class WebEXConfig(BaseConfig):
    """Manages WebEX connector configuration."""

    def __init__(self, config_file: str = "webex_config.json"):
        super().__init__(config_file)

    def _default_config(self) -> Dict:
        """Return default WebEX configuration."""
        return {
            "token": os.environ.get("WEBEX_BOT_TOKEN", ""),
            "rabbitmq_host": os.environ.get("RABBITMQ_HOST", "192.168.0.85"),
            "rabbitmq_port": int(os.environ.get("RABBITMQ_PORT", "5672")),
            "rabbitmq_user": os.environ.get("RABBITMQ_USER", "admin"),
            "rabbitmq_password": os.environ.get("RABBITMQ_PASSWORD", ""),
            "rabbitmq_queue": os.environ.get("RABBITMQ_QUEUE", "webex"),
            "rabbitmq_vhost": "/",
            "rabbitmq_queue_passive": False,  # Set True for brokers where bot only has CONSUME permission  # noqa: E501
            "allowed_users": [],  # List of WebEX person IDs allowed to chat
            "user_pairings": {},  # Maps WebEX person ID to session info
            "enable_auto_pair": False,  # Auto-pair new users
            "default_agent": os.environ.get("COPILOT_DEFAULT_AGENT", "orchestrator"),
            "default_model": os.environ.get("COPILOT_DEFAULT_MODEL", "gpt-5-mini"),
            "pinned_users": {},  # Maps person_id (str) to {"agent": "name"}
            "yolo_allowed_users": [],  # Person IDs permitted to enable /mode yolo; empty = all allowed
        }


class WebEXConnector(BaseConnector):
    """Main WebEX connector class — listens to RabbitMQ queue."""

    connector_name = "WebEX connector"
    channel_name = "webex"

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

        # Save token to config if only provided via env/arg
        if token and not config_token:
            self.config.config["token"] = token
            self.config.save()

        # API mode configuration (WebEX uses api_url for both Copilot API and RabbitMQ)
        self.api_url = os.getenv("API_URL", "http://127.0.0.1:8001")

        self.rabbitmq_connection = None
        self.rabbitmq_channel = None
        self.cleanup_thread = None
        self._init_shared_state()

        if not self.config.config.get("allowed_users"):
            print(
                "⚠️  WARNING: allowed_users is empty — ALL WebEx users can interact with this bot!",
                file=sys.stderr,
            )

    def _wait_for_active_requests(
        self, component: str = "WebEX connector", timeout: Optional[float] = None
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

    def _stop_consuming(self):
        """Stop RabbitMQ consumption on the current channel if possible."""
        try:
            if self.rabbitmq_channel and getattr(
                self.rabbitmq_channel, "is_open", True
            ):
                self.rabbitmq_channel.stop_consuming()
        except Exception as e:
            print(f"[WARN] Failed to stop RabbitMQ consumption: {e}", file=sys.stderr)

    def _stop_consuming_async(self):
        """Request consumer shutdown from any thread."""
        try:
            if self.rabbitmq_connection and not self.rabbitmq_connection.is_closed:
                self.rabbitmq_connection.add_callback_threadsafe(self._stop_consuming)
            else:
                self._stop_consuming()
        except Exception:
            self._stop_consuming()

    @property
    def _safe_file_dirs(self):
        """Allowed download directories for WebEX file sends."""
        return [
            Path("/opt/n8n-copilot-shim-dev/webex_downloads").resolve(),
            Path("/tmp/webui_ai_media").resolve(),
        ]

    @property
    def _max_file_bytes(self) -> int:
        return 100 * 1024 * 1024  # 100 MB (WebEX limit)

    @property
    def _copilot_api_url(self) -> str:
        return self.api_url

    def _make_session_id(self, user_id) -> str:
        return f"webex_{user_id}"

    def _get_user_identity(self, user_id) -> str:
        return f"webex_{user_id}"

    def _send_channel_status(self, channel_id, text: str):
        return self.send_message(channel_id, text)

    def _edit_channel_status(self, channel_id, msg_id, text: str):
        # WebEX edit_message has reversed arg order: (msg_id, room_id, text)
        self.edit_message(msg_id, channel_id, text)

    def _send_channel_typing(self, channel_id):
        self.send_typing(channel_id)

    def _request_shutdown(self, reason: str = "shutdown"):
        """Override to also stop RabbitMQ consumption on shutdown."""
        super()._request_shutdown(reason)
        self._stop_consuming_async()

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

            # Create session with our session_id so it's properly tracked
            create_resp = requests.post(
                f"{self.api_url}/api/v1/sessions/create",
                headers=headers,
                json={"session_id": session_id},
                timeout=10,
            )
            if create_resp.status_code != 200:
                print(
                    f"[WARN] Session create failed ({create_resp.status_code}): {create_resp.text}",
                    file=sys.stderr,
                )

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
                print(
                    f"[WARN] API request failed ({resp.status_code}): {resp.text}",
                    file=sys.stderr,
                )
                session_mgr = self.get_session_manager(session_id)
                return session_mgr.execute(query, session_id)
        except Exception as e:
            print(
                f"[WARN] API request exception: {e}, falling back to direct mode",
                file=sys.stderr,
            )
            session_mgr = self.get_session_manager(session_id)
            return session_mgr.execute(query, session_id)

    def connect_rabbitmq(self) -> bool:
        """Connect to RabbitMQ with optional SSL and DNS override support"""
        try:
            credentials = pika.PlainCredentials(
                self.config.config["rabbitmq_user"],
                self.config.config["rabbitmq_password"],
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
                retry_delay=2,
            )
            result = {}
            connected = threading.Event()

            def establish_connection():
                connection = None
                try:
                    connection = pika.BlockingConnection(parameters)
                    if self.shutdown_event.is_set():
                        try:
                            connection.close()
                        except Exception:
                            pass
                        result["connected"] = False
                        return

                    channel = connection.channel()
                    if self.shutdown_event.is_set():
                        try:
                            connection.close()
                        except Exception:
                            pass
                        result["connected"] = False
                        return

                    passive = self.config.config.get("rabbitmq_queue_passive", False)
                    if passive:
                        channel.queue_declare(
                            queue=self.config.config["rabbitmq_queue"], passive=True
                        )
                    else:
                        channel.queue_declare(
                            queue=self.config.config["rabbitmq_queue"], durable=True
                        )
                    channel.basic_qos(prefetch_count=1)

                    if self.shutdown_event.is_set():
                        try:
                            connection.close()
                        except Exception:
                            pass
                        result["connected"] = False
                        return

                    result["connection"] = connection
                    result["channel"] = channel
                    result["connected"] = True
                except Exception as e:
                    result["error"] = e
                    result["connected"] = False
                finally:
                    connected.set()

            worker = threading.Thread(
                target=establish_connection,
                name="webex-rabbitmq-connect",
                daemon=True,
            )
            worker.start()

            while not connected.wait(timeout=0.1):
                if self.shutdown_event.is_set():
                    print(
                        "[INFO] RabbitMQ connection attempt interrupted by shutdown",
                        file=sys.stderr,
                    )
                    return False

            if result.get("connected"):
                self.rabbitmq_connection = result["connection"]
                self.rabbitmq_channel = result["channel"]
            else:
                error = result.get("error")
                if error:
                    print(f"❌ Error connecting to RabbitMQ: {error}", file=sys.stderr)
                return False

            ssl_info = " (SSL/TLS enabled)" if use_ssl else ""
            print(
                f"✅ Connected to RabbitMQ on {host_ip}:{port}{ssl_info}",
                file=sys.stderr,
            )
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
                "Content-Type": "application/json",
            }

            # Split into chunks if too long
            max_len = 4000  # WebEX message length limit
            chunks = (
                [text[i : i + max_len] for i in range(0, len(text), max_len)]
                if text
                else ["No response"]
            )

            last_msg_id = None
            for chunk in chunks:
                # Fix 4: Determine if destination is email or room ID
                if "@" in room_id:
                    # Email address - use toPersonEmail
                    data = {"toPersonEmail": room_id, "text": chunk, "markdown": chunk}
                else:
                    # Room ID - use roomId
                    data = {"roomId": room_id, "text": chunk, "markdown": chunk}

                response = requests.post(
                    "https://webexapis.com/v1/messages",
                    headers=headers,
                    json=data,
                    timeout=10,
                )

                if response.status_code != 200:
                    print(
                        f"[WARN] WebEX send failed ({response.status_code}): {response.text[:200]}",
                        file=sys.stderr,
                    )
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
                "Content-Type": "application/json",
            }

            data = {"roomId": room_id, "text": text, "markdown": text}

            print(
                f"[DEBUG] Attempting to edit message {message_id} in room {room_id} with text: {text[:100]}",
                file=sys.stderr,
            )
            response = requests.put(
                f"https://webexapis.com/v1/messages/{message_id}",
                headers=headers,
                json=data,
                timeout=10,
            )

            print(
                f"[DEBUG] Edit response status: {response.status_code}", file=sys.stderr
            )
            if response.status_code == 200:
                print(f"[DEBUG] Message edit successful", file=sys.stderr)
                return True
            else:
                print(
                    f"[WARN] WebEX edit failed ({response.status_code}): {response.text[:200]}",
                    file=sys.stderr,
                )
                return False
        except Exception as e:
            print(f"[ERROR] Error editing WebEX message: {e}", file=sys.stderr)
            return False

    def send_typing(self, room_id: str) -> bool:
        """Send typing indicator to WebEX room. Returns True if successful."""
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            }

            data = {"roomId": room_id}

            response = requests.post(
                "https://webexapis.com/v1/messages/typing",
                headers=headers,
                json=data,
                timeout=10,
            )

            if response.status_code == 204:
                return True
            else:
                # Typing indicator might not be supported by all WebEX instances
                print(
                    f"[DEBUG] WebEX typing indicator: {response.status_code}",
                    file=sys.stderr,
                )
                return False
        except Exception as e:
            print(f"[DEBUG] Typing indicator not available: {e}", file=sys.stderr)
            return False

    def send_file(
        self, room_id: str, file_path: str, caption: str = ""
    ) -> Optional[str]:
        """Send a file to WebEX room via multipart upload. Returns message ID."""
        try:
            # Security validation
            if not self._is_safe_file_path(file_path):
                self.send_message(
                    room_id, f"⚠️ Cannot send file: {file_path} (security check failed)"
                )
                return None

            print(
                f"[DEBUG] Attempting to send file to WebEX: {file_path}",
                file=sys.stderr,
            )

            # Detect MIME type
            mime_type, _ = mimetypes.guess_type(file_path)
            if not mime_type:
                mime_type = "application/octet-stream"

            headers = {
                "Authorization": f"Bearer {self.token}",
            }

            # Upload file via multipart
            with open(file_path, "rb") as f:
                files = {"files": (Path(file_path).name, f, mime_type)}
                data = {"roomId": room_id}

                if caption:
                    data["text"] = caption

                response = requests.post(
                    "https://webexapis.com/v1/messages",
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=60,  # Longer timeout for file uploads
                )

                if response.status_code != 200:
                    print(
                        f"[WARN] WebEX file send failed ({response.status_code}): {response.text[:200]}",
                        file=sys.stderr,
                    )
                    self.send_message(
                        room_id, f"⚠️ Failed to send file: {Path(file_path).name}"
                    )
                    return None

                result = response.json()
                if result and "id" in result:
                    return result["id"]
        except Exception as e:
            print(f"Error sending file to WebEX: {e}", file=sys.stderr)
            self.send_message(room_id, f"⚠️ Error sending file: {str(e)}")
        return None

    def _send_image_url(
        self, room_id: str, url: str, caption: str = ""
    ) -> Optional[str]:
        """Send an image to WebEX room via external URL. Returns message ID."""
        try:
            print(
                f"[DEBUG] Sending image URL to WebEX: {url[:100]}",
                file=sys.stderr,
                flush=True,
            )
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
            print(
                f"[DEBUG] WebEX image URL response: {response.status_code}",
                file=sys.stderr,
                flush=True,
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("id")
            else:
                print(
                    f"[WARN] WebEX image URL send failed ({response.status_code}): {response.text[:200]}",
                    file=sys.stderr,
                    flush=True,
                )
                # Fallback: send as markdown link
                self.send_message(
                    room_id, f"[📷 Image]({url})" + (f" - {caption}" if caption else "")
                )
        except Exception as e:
            print(
                f"[ERROR] Exception sending image URL to WebEX: {e}",
                file=sys.stderr,
                flush=True,
            )
            self.send_message(
                room_id, f"[📷 Image]({url})" + (f" - {caption}" if caption else "")
            )
        return None

    def _send_image_file(
        self, room_id: str, file_path: str, caption: str = ""
    ) -> Optional[str]:
        """Send a local image file to WebEX room via multipart upload. Returns message ID.

        Converts PNG images to JPEG for reliable inline preview in WebEx client.
        """
        try:
            print(
                f"[DEBUG] Uploading local image to WebEX: {file_path} (size={os.path.getsize(file_path)})",
                file=sys.stderr,
                flush=True,
            )
            headers = {"Authorization": f"Bearer {self.token}"}
            data = {"roomId": room_id}
            if caption:
                data["text"] = caption

            # Convert PNG to JPEG for reliable WebEx inline preview
            file_ext = Path(file_path).suffix.lower()
            if file_ext == ".png":
                try:
                    import io

                    from PIL import Image

                    img = Image.open(file_path)
                    if img.mode == "RGBA":
                        # Flatten alpha onto white background
                        bg = Image.new("RGB", img.size, (255, 255, 255))
                        bg.paste(img, mask=img.split()[3])
                        img = bg
                    elif img.mode != "RGB":
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85)
                    buf.seek(0)
                    jpeg_name = Path(file_path).stem + ".jpg"
                    print(
                        f"[DEBUG] Converted PNG->JPEG: {os.path.getsize(file_path)} -> {buf.getbuffer().nbytes} bytes",
                        file=sys.stderr,
                        flush=True,
                    )
                    files = {"files": (jpeg_name, buf, "image/jpeg")}
                    response = requests.post(
                        "https://webexapis.com/v1/messages",
                        headers=headers,
                        data=data,
                        files=files,
                        timeout=60,
                    )
                except ImportError:
                    print(
                        f"[DEBUG] PIL not available, sending PNG as-is",
                        file=sys.stderr,
                        flush=True,
                    )
                    with open(file_path, "rb") as f:
                        files = {"files": (Path(file_path).name, f, "image/png")}
                        response = requests.post(
                            "https://webexapis.com/v1/messages",
                            headers=headers,
                            data=data,
                            files=files,
                            timeout=60,
                        )
            else:
                mime_type, _ = mimetypes.guess_type(file_path)
                if not mime_type:
                    mime_type = "image/jpeg"
                with open(file_path, "rb") as f:
                    files = {"files": (Path(file_path).name, f, mime_type)}
                    response = requests.post(
                        "https://webexapis.com/v1/messages",
                        headers=headers,
                        data=data,
                        files=files,
                        timeout=60,
                    )

            print(
                f"[DEBUG] WebEX image upload response: {response.status_code}",
                file=sys.stderr,
                flush=True,
            )
            if response.status_code == 200:
                result = response.json()
                msg_id = result.get("id")
                print(
                    f"[DEBUG] WebEX image sent successfully, msg_id={msg_id}",
                    file=sys.stderr,
                    flush=True,
                )
                return msg_id
            else:
                print(
                    f"[WARN] WebEX image file send failed ({response.status_code}): {response.text[:500]}",
                    file=sys.stderr,
                    flush=True,
                )
                self.send_message(
                    room_id, f"⚠️ Failed to send image: {Path(file_path).name}"
                )
        except Exception as e:
            print(
                f"[ERROR] Exception sending image file to WebEX: {e}",
                file=sys.stderr,
                flush=True,
            )
            self.send_message(room_id, f"⚠️ Error sending image: {str(e)}")
        return None

    def get_person_info(self, person_id: str) -> Optional[Dict]:
        """Get WebEX person info by ID"""
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            response = requests.get(
                f"https://webexapis.com/v1/people/{person_id}",
                headers=headers,
                timeout=10,
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            print(f"Error getting person info: {e}", file=sys.stderr)
        return None

    def download_file(self, file_url: str, person_id: str) -> Optional[tuple]:
        """Download file from WebEX and store it. Returns (file_path, filename) tuple or None."""
        try:
            headers = {"Authorization": f"Bearer {self.token}"}

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
                    filename = unquote(filename).replace("+", " ")
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
                print(
                    f"[DEBUG] File not scannable, retrying with allow=unscannable",
                    file=sys.stderr,
                )
                response = requests.get(
                    f"{file_url}?allow=unscannable", headers=headers, timeout=30
                )
                if response.status_code == 200:
                    # Repeat save logic above
                    downloads_dir = Path("/opt/n8n-copilot-shim-dev/webex_downloads")
                    downloads_dir.mkdir(exist_ok=True)

                    filename = "file"
                    content_disp = response.headers.get("Content-Disposition", "")
                    if "filename=" in content_disp:
                        filename = (
                            content_disp.split("filename=")[1].strip('"').strip("'")
                        )
                        # URL-decode the filename (handles %E2%80%AF, +, etc.)
                        filename = unquote(filename).replace("+", " ")
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
                    print(
                        f"[DEBUG] Unscannable file saved to: {local_path}",
                        file=sys.stderr,
                    )
                    return (str(local_path), filename)

            print(
                f"[WARN] File download failed: HTTP {response.status_code}",
                file=sys.stderr,
            )
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
                                file_age = current_time - datetime.fromtimestamp(
                                    tmp_file.stat().st_mtime
                                )
                                if file_age > max_age:
                                    tmp_file.unlink()
                                    print(
                                        f"[DEBUG] Cleaned up old temp file: {tmp_file}",
                                        file=sys.stderr,
                                    )
                            except Exception as e:
                                logger.debug(f"Failed to clean up temp file: {e}")

                    # Clean up downloaded files in webex_downloads/
                    downloads_dir = Path("/opt/n8n-copilot-shim-dev/webex_downloads")
                    if downloads_dir.exists():
                        for file in downloads_dir.glob("*_*"):
                            try:
                                file_age = current_time - datetime.fromtimestamp(
                                    file.stat().st_mtime
                                )
                                if file_age > max_age:
                                    file.unlink()
                                    print(
                                        f"[DEBUG] Cleaned up old download: {file}",
                                        file=sys.stderr,
                                    )
                            except Exception as e:
                                logger.debug(f"Failed to clean up download file: {e}")

                    # Sleep before next cleanup cycle
                    time.sleep(interval_seconds)
                except Exception as e:
                    print(f"Error in cleanup thread: {e}", file=sys.stderr)
                    time.sleep(interval_seconds)

        self.cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
        self.cleanup_thread.start()
        print(
            f"[DEBUG] Started background cleanup task (interval: {interval_seconds}s)",
            file=sys.stderr,
        )

    def pin_message(self, message_id: str, room_id: str) -> bool:
        """Pin a message in WebEX room and set as banner. Returns True if successful."""
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            }

            # WebEX pinning: PUT /v1/messages/{id}/pin with roomId in body
            data = {"roomId": room_id}
            response = requests.put(
                f"https://webexapis.com/v1/messages/{message_id}/pin",
                headers=headers,
                json=data,
                timeout=10,
            )

            if response.status_code in [200, 204]:
                print(f"[DEBUG] Message pinned successfully", file=sys.stderr)
                return True
            else:
                print(
                    f"[DEBUG] WebEX pin failed ({response.status_code}): {response.text[:200]}",
                    file=sys.stderr,
                )
                return False
        except Exception as e:
            print(f"[DEBUG] Pin error: {e}", file=sys.stderr)
            return False

    def unpin_all_messages(self, room_id: str) -> bool:
        """Unpin all messages in a room. Returns True if successful."""
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            }

            response = requests.put(
                f"https://webexapis.com/v1/rooms/{room_id}/unpinAllMessages",
                headers=headers,
                timeout=10,
            )

            if response.status_code in [200, 204]:
                print(f"[DEBUG] All messages unpinned successfully", file=sys.stderr)
                return True
            else:
                print(
                    f"[DEBUG] WebEX unpin all failed ({response.status_code})",
                    file=sys.stderr,
                )
                return False
        except Exception as e:
            print(f"[DEBUG] Unpin error: {e}", file=sys.stderr)
            return False

    def send_response(
        self, room_id: str, text: str, status_msg_id: Optional[str] = None
    ):
        """Send response, detecting image URLs and file paths.

        Mirrors Telegram pattern: extracts images and files, sends text portion first,
        then sends media items. If status_msg_id exists, edits it with text portion.
        """
        print(
            f"[DEBUG OUTBOUND] send_response -> room_id={room_id} text_snippet={repr(text[:200])} status_msg_id={status_msg_id}",
            file=sys.stderr,
        )

        # Extract images first, then files from remaining text
        image_data, text_after_images = self.extract_image_urls(text)
        file_data, remaining_text = self.extract_file_paths(text_after_images)

        # Handle text portion
        if remaining_text.strip():
            if status_msg_id:
                print(
                    f"[DEBUG OUTBOUND] send_response editing status message {status_msg_id} for room_id={room_id}",
                    file=sys.stderr,
                )
                self.edit_message(status_msg_id, room_id, remaining_text)
                status_msg_id = None
            else:
                print(
                    f"[DEBUG OUTBOUND] send_response sending text to room_id={room_id}",
                    file=sys.stderr,
                )
                self.send_message(room_id, remaining_text)
        elif status_msg_id and (image_data or file_data):
            # No text, just media - delete status message by editing to empty
            try:
                # WebEX doesn't support deleting messages, so edit to indicate completion
                self.edit_message(status_msg_id, room_id, "✓")
                print(
                    f"[DEBUG OUTBOUND] Edited status message to checkmark for room_id={room_id}",
                    file=sys.stderr,
                )
            except Exception as e:
                print(f"[WARN] Could not edit status message: {e}", file=sys.stderr)
            status_msg_id = None
        elif status_msg_id:
            # No text, no media - edit status message with default text
            print(
                f"[DEBUG OUTBOUND] send_response editing status message {status_msg_id} to checkmark for room_id={room_id}",
                file=sys.stderr,
            )
            self.edit_message(status_msg_id, room_id, "✓")

        # Send images
        for url, caption in image_data:
            # Strip any residual ANSI codes from URL
            url = re.sub(r"\x1b\[[0-9;]*m", "", url)
            print(
                f"[DEBUG OUTBOUND] send_response sending image URL={url} caption={repr(caption[:100])} to room_id={room_id}",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"[DEBUG OUTBOUND] -> url repr: {repr(url)}",
                file=sys.stderr,
                flush=True,
            )
            if url.startswith(("http://", "https://")):
                # External URL: send as file URL attachment
                print(
                    f"[DEBUG OUTBOUND] -> dispatching _send_image_url",
                    file=sys.stderr,
                    flush=True,
                )
                self._send_image_url(room_id, url, caption)
            else:
                # Local file path - check with retry for potential race condition
                file_found = os.path.isfile(url)
                if not file_found:
                    # Retry after short delay in case file is still being written
                    print(
                        f"[DEBUG OUTBOUND] -> file not found on first check, retrying in 2s...",
                        file=sys.stderr,
                        flush=True,
                    )
                    print(
                        f"[DEBUG OUTBOUND] -> dir exists: {os.path.isdir(os.path.dirname(url))}, dir contents: {os.listdir(os.path.dirname(url)) if os.path.isdir(os.path.dirname(url)) else 'N/A'}",
                        file=sys.stderr,
                        flush=True,
                    )
                    time.sleep(2)
                    file_found = os.path.isfile(url)
                    print(
                        f"[DEBUG OUTBOUND] -> after retry: isfile={file_found}",
                        file=sys.stderr,
                        flush=True,
                    )
                if file_found:
                    print(
                        f"[DEBUG OUTBOUND] -> dispatching _send_image_file (size={os.path.getsize(url)})",
                        file=sys.stderr,
                        flush=True,
                    )
                    result = self._send_image_file(room_id, url, caption)
                    print(
                        f"[DEBUG OUTBOUND] -> _send_image_file returned: {result}",
                        file=sys.stderr,
                        flush=True,
                    )
                else:
                    # Unresolved path: send as text link
                    print(
                        f"[DEBUG OUTBOUND] -> image path not found after retry, sending text fallback",
                        file=sys.stderr,
                        flush=True,
                    )
                    self.send_message(
                        room_id,
                        f"[Image]({url})" + (f" - {caption}" if caption else ""),
                    )

        # Send files
        for file_path, caption in file_data:
            print(
                f"[DEBUG OUTBOUND] send_response sending file path={file_path} caption={repr(caption[:100])} to room_id={room_id}",
                file=sys.stderr,
            )
            self.send_file(room_id, file_path, caption)


    @staticmethod
    def _unwrap_payload(payload: Dict, payload_key: Optional[str] = None, max_depth: int = 4) -> Dict:
        """
        Unwrap nested payload structures from RabbitMQ messages.

        Supports:
        1. Single-level unwrap: payload_key="data"
        2. Dotted path unwrap: payload_key="data.message_data"
        3. Auto-unwrap: looks for nested message_data dict and unwraps if present

        Args:
            payload: The raw message dict from RabbitMQ
            payload_key: Optional key path to unwrap (supports dot notation)
            max_depth: Maximum nesting depth to unwrap (safety limit)

        Returns:
            The unwrapped payload dict
        """
        if not isinstance(payload, dict):
            return payload

        current = payload
        depth = 0

        # Step 1: Unwrap via payload_key if provided
        if payload_key:
            # Support dotted paths: "data.message_data" -> ["data", "message_data"]
            keys = payload_key.split(".")
            for key in keys:
                if isinstance(current, dict) and key in current and isinstance(current[key], dict):
                    current = current[key]
                    depth += 1
                else:
                    # Key not found or not a dict, stop unwrapping
                    break

        # Step 2: Auto-unwrap nested message_data if present
        # This handles cases where after primary unwrap, there's still a nested message_data
        while (
            isinstance(current, dict)
            and "message_data" in current
            and isinstance(current["message_data"], dict)
            and depth < max_depth
        ):
            current = current["message_data"]
            depth += 1

        return current

    def handle_message(self, message_data: Dict) -> bool:
        """Process incoming WebEX message from RabbitMQ."""
        if not self._begin_active_request():
            print(
                "[INFO] Ignoring WebEX message while shutdown is in progress",
                file=sys.stderr,
            )
            return False
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

                    # Check if this is an audio file → transcribe it
                    if audio_transcriber.is_audio_file(file_name):
                        print(
                            f"[DEBUG] Audio file detected: {file_name}, transcribing...",
                            file=sys.stderr,
                        )
                        transcribed, backend = audio_transcriber.transcribe(file_path)
                        if transcribed:
                            if text:
                                text = f"{text}\n\n[Voice transcription ({backend})]: {transcribed}"
                            else:
                                text = transcribed
                            print(
                                f"[DEBUG] Transcribed audio via {backend}: {text[:100]}...",
                                file=sys.stderr,
                            )
                        else:
                            self.send_message(
                                room_id,
                                "⚠️ Could not transcribe audio file. Please send as text instead.",
                            )
                            return True
                    else:
                        # Non-audio file - handle normally
                        # Sanitize filename - remove spaces and special chars for shell safety
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
                            print(
                                f"[DEBUG] File copied to temp: {tmp_file_path}",
                                file=sys.stderr,
                            )
                            file_path = tmp_file_path
                        except Exception as e:
                            print(
                                f"[WARN] Could not copy to /tmp: {e}, using original path",
                                file=sys.stderr,
                            )

                        # Add file path to query
                        if not text:
                            text = f"Please analyze this file: {file_path}"
                        else:
                            text = f"{text}\n\nFile to analyze: {file_path}"
                        print(f"[DEBUG] File query: {text[:200]}", file=sys.stderr)
                else:
                    self.send_message(room_id, "❌ Failed to download file")
                    return True

            if not person_id or not room_id:
                print(f"[DEBUG] Incomplete message: {message_data}", file=sys.stderr)
                return True

            if not self.config.is_user_allowed(person_id):
                self.send_message(room_id, "❌ You are not authorized to use this bot.")
                return True

            if not text:
                print(f"[DEBUG] Incomplete message: {message_data}", file=sys.stderr)
                return True

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
                    if len(parts) == 1 or (
                        len(parts) > 1 and parts[1].lower() == "current"
                    ):
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
                                    response = (
                                        "Timeout must be at most 3600 seconds (1 hour)"
                                    )
                                else:
                                    self.config.set_user_timeout(person_id, new_timeout)
                                    response = (
                                        f"✅ Timeout set to {new_timeout} seconds"
                                    )
                            except ValueError:
                                response = (
                                    "Invalid timeout. Use: /timeout set <seconds>"
                                )
                    else:
                        response = "Invalid timeout command. Use: /timeout current or /timeout set <seconds>"
                    msg_id = self.send_message(room_id, response)
                # Block pinned users from changing their agent
                elif cmd_lower.startswith("/agent set") and self.config.is_user_pinned(
                    person_id
                ):
                    pinned_agent = self.config.get_pinned_agent(person_id)
                    self.send_message(
                        room_id,
                        f"❌ Your agent is pinned to **{pinned_agent}** by an administrator. You cannot change agents.",
                    )
                # Block pinned users from changing their runtime (if a runtime is pinned)
                elif cmd_lower.startswith(
                    "/runtime set"
                ) and self.config.get_pinned_runtime(person_id):
                    pinned_runtime = self.config.get_pinned_runtime(person_id)
                    self.send_message(
                        room_id,
                        f"❌ Your runtime is pinned to **{pinned_runtime}** by an administrator. You cannot change runtimes.",
                    )
                # Block pinned users from changing their model (if a model is pinned)
                elif cmd_lower.startswith(
                    "/model set"
                ) and self.config.get_pinned_model(person_id):
                    pinned_model = self.config.get_pinned_model(person_id)
                    self.send_message(
                        room_id,
                        f"❌ Your model is pinned to **{pinned_model}** by an administrator. You cannot change models.",
                    )
                # Block unauthorized users from enabling yolo mode
                elif cmd_lower.startswith(
                    "/mode yolo"
                ) and not self.config.is_yolo_allowed(person_id):
                    self.send_message(
                        room_id, "❌ You are not authorized to enable YOLO mode."
                    )
                else:
                    # Regular slash commands
                    timeout = self.config.get_user_timeout(person_id)
                    response = self._execute_command(
                        text, session_id, timeout, user_identity=person_id
                    )
                    msg_id = self.send_message(room_id, response)

                    # Pin configuration commands (agent, runtime, model, session)
                    if msg_id and any(
                        cmd_lower.startswith(cmd)
                        for cmd in [
                            "/agent set",
                            "/runtime set",
                            "/model set",
                            "/session reset",
                        ]
                    ):
                        self.pin_message(msg_id, room_id)
                        print(
                            f"[DEBUG] Pinned configuration command message: {cmd_lower[:30]}",
                            file=sys.stderr,
                        )

                    # Evict cached SessionManager on session-affecting commands
                    if cmd_lower.startswith("/session reset") or cmd_lower.startswith(
                        "/runtime set"
                    ):
                        self._evict_session_manager(session_id)
            else:
                # Check for bash command (!)
                if text.startswith("!"):
                    timeout = self.config.get_user_timeout(person_id)
                    response = self._execute_command(
                        text, session_id, timeout, user_identity=person_id
                    )
                    self.send_message(room_id, response)
                else:
                    # Route regular messages to agent_manager with status updates
                    timeout = self.config.get_user_timeout(person_id)
                    response, status_msg_id = self._query_agent_with_status(
                        text,
                        session_info["agent"],
                        session_info["model"],
                        person_id,
                        room_id,
                        timeout,
                    )
                    self.send_response(room_id, response, status_msg_id)
            return True

        except Exception as e:
            print(f"Error handling message: {e}", file=sys.stderr)
            if room_id:
                self.send_message(room_id, f"❌ Error: {str(e)[:100]}")
            return not self.shutdown_event.is_set()
        finally:
            self._finish_active_request()

    def listen_to_queue(self, poll_interval: int = 1):
        """Listen to RabbitMQ queue for WebEX messages"""
        self._install_signal_handlers()
        self.shutdown_event.clear()
        self.running = True
        print(
            f"Starting WebEX connector, listening to queue: {self.config.config['rabbitmq_queue']}"
        )

        try:

            def callback(ch, method, properties, body):
                """Handle a RabbitMQ delivery and ack only after processing."""
                try:
                    message_data = json.loads(body.decode())
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    print(
                        f"[ERROR] Discarding malformed RabbitMQ message (bad JSON): {e}",
                        file=sys.stderr,
                    )
                    print(
                        f"[ERROR] Raw body (first 500 chars): {body[:500]}",
                        file=sys.stderr,
                    )
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    return

                try:
                    print(
                        f"[DEBUG] Received WebEX message: {message_data}",
                        file=sys.stderr,
                    )

                    # Support configurable payload unwrapping for gateway wrappers
                    # Supports dotted paths (e.g., "data.message_data") and auto-unwraps nested message_data
                    payload_key = self.config.config.get("rabbitmq_payload_key")
                    message_data = self._unwrap_payload(message_data, payload_key)
                    if payload_key:
                        print(
                            f"[DEBUG] Unwrapped payload using key '{payload_key}'",
                            file=sys.stderr,
                        )

                    handled = self.handle_message(message_data)
                    if handled:
                        ch.basic_ack(delivery_tag=method.delivery_tag)
                    else:
                        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                except Exception as e:
                    import traceback

                    tb_str = traceback.format_exc()
                    print(
                        "[ERROR] Exception processing WebEX message:", file=sys.stderr
                    )
                    print(tb_str, file=sys.stderr)
                    if self.shutdown_event.is_set():
                        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                    else:
                        ch.basic_ack(delivery_tag=method.delivery_tag)

            retry_delay = 30
            max_delay = 300
            while self.running and not self.connect_rabbitmq():
                print(
                    f"Failed to connect to RabbitMQ, retrying in {retry_delay}s...",
                    file=sys.stderr,
                )
                if self.shutdown_event.wait(retry_delay):
                    break
                retry_delay = min(retry_delay * 2, max_delay)
            if not self.running:
                return

            # Start background cleanup task (removes files older than 5 minutes)
            self.start_cleanup_background_task(interval_seconds=300)

            self.rabbitmq_channel.basic_consume(
                queue=self.config.config["rabbitmq_queue"], on_message_callback=callback
            )

            print(
                f"[✅] Listening to RabbitMQ queue: {self.config.config['rabbitmq_queue']}",
                file=sys.stderr,
            )
            self.rabbitmq_channel.start_consuming()

        except KeyboardInterrupt:
            self._request_shutdown("KeyboardInterrupt")
        finally:
            self._wait_for_active_requests()
            self.disconnect_rabbitmq()

    def stop(self):
        """Stop the connector"""
        self._request_shutdown("stop()")
        self._wait_for_active_requests()
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
