#!/usr/bin/env python3
"""
Notification Manager for Background Task Completion Notifications.

Stores WebUI notifications in ~/.copilot/notifications.json.
Routes notifications to appropriate channels:
  - webui: stored for polling by the WebUI notification center
  - telegram: sends via TelegramConnector
  - webex: sends via WebEXConnector
"""

import json
import os
import threading
import time
from typing import Optional
from uuid import uuid4


_NOTIF_FILE = os.path.join(os.path.expanduser("~"), ".copilot", "notifications.json")
_PREFS_FILE = os.path.join(os.path.expanduser("~"), ".copilot", "notification_prefs.json")
_MAX_NOTIFICATIONS = 200


class NotificationManager:
    """Manages task completion notifications with channel-aware routing."""

    def __init__(self, notif_file: str = _NOTIF_FILE, prefs_file: str = _PREFS_FILE):
        self._path = notif_file
        self._prefs_path = prefs_file
        self._lock = threading.Lock()
        self._prefs_lock = threading.Lock()
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

    # ---- Per-identity notification preferences ----

    @staticmethod
    def _normalize_identity(identity: str) -> str:
        """Return a canonical identity key by stripping channel prefixes.

        Handles compound Telegram identities (``telegram_botid_userid``)
        by extracting the bare numeric user-id so it matches the value
        sent in ``X-User-Identity`` headers by background task creators.
        """
        if not identity:
            return identity
        # Special keys like "_global" pass through unchanged
        if identity.startswith("_"):
            return identity
        # Handle compound Telegram identity: telegram_<botid>_<userid>
        if identity.startswith("telegram_"):
            parts = identity.split("_")
            # telegram_<userid> → userid, telegram_<botid>_<userid> → userid
            return parts[-1]
        for prefix in ("webex_", "webui_", "api_"):
            if identity.startswith(prefix):
                return identity[len(prefix):]
        return identity

    def _load_prefs(self) -> dict:
        try:
            with open(self._prefs_path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_prefs(self, prefs: dict):
        import tempfile

        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(self._prefs_path), suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(prefs, f, indent=2)
            os.replace(tmp_path, self._prefs_path)

        except Exception as e:
            print(f"[NotificationManager] FAILED to save prefs: {e}")

    def set_user_pref(self, identity: str, channel: str, preference: str):
        """Store notification preference for a user identity.

        ``identity`` is the raw identity (e.g. Telegram chat-id or email).
        ``channel`` is the originating channel (telegram, webex, webui, etc.).
        ``preference`` is "all" or "off".

        The preference is stored under the normalized bare identity so it
        applies regardless of which channel created the background task.
        """
        bare = self._normalize_identity(identity)
        with self._prefs_lock:
            prefs = self._load_prefs()
            prefs[bare] = {
                "preference": preference,
                "channel": channel,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            self._save_prefs(prefs)

    def get_user_pref(self, identity: str) -> str:
        """Return notification preference for an identity ("all" or "off")."""
        bare = self._normalize_identity(identity)
        with self._prefs_lock:
            prefs = self._load_prefs()
        entry = prefs.get(bare)
        if isinstance(entry, dict):
            return entry.get("preference", "all")
        return "all"

    def is_muted(self, identity: str) -> bool:
        """Convenience: True when external notifications should be suppressed."""
        return self.get_user_pref(identity) == "off"

    def _load(self) -> list:
        try:
            with open(self._path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self, notifications: list):
        import tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(self._path), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(notifications, f, indent=2, default=str)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def create_notification(
        self,
        task_id: str,
        description: str,
        status: str,
        channel: str,
        user_key: str,
        output_preview: Optional[str] = None,
        error: Optional[str] = None,
        skip_external: bool = False,
    ) -> dict:
        """Create a notification and route it appropriately."""
        notif_id = f"notif_{uuid4().hex[:12]}"
        notification = {
            "notification_id": notif_id,
            "task_id": task_id,
            "description": description,
            "status": status,          # "completed" or "failed"
            "channel": channel,        # "webui", "telegram", "webex", etc.
            "user_key": user_key,
            "output_preview": (output_preview or "")[:500] if output_preview else None,
            "error": (error or "")[:500] if error else None,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "read": False,
        }

        # Always store for WebUI polling (regardless of originating channel)
        with self._lock:
            notifications = self._load()
            notifications.append(notification)
            # Keep only the most recent MAX_NOTIFICATIONS
            if len(notifications) > _MAX_NOTIFICATIONS:
                notifications = notifications[-_MAX_NOTIFICATIONS:]
            self._save(notifications)

        # Route to external channels if the task was created from telegram/webex
        # Defense-in-depth: check global mute AND per-identity mute even if
        # skip_external was not explicitly set (covers identity mismatch and
        # late-mute edge cases).
        if not skip_external:
            if self.is_muted("_global"):
                skip_external = True
            elif self.is_muted(user_key):
                skip_external = True

        if not skip_external:
            print(f"[NotificationManager] Broadcasting to all external channels for task {task_id}")
            self._notify_telegram_broadcast(notification)
            self._notify_webex_broadcast(notification)
        else:
            print(f"[NotificationManager] Skipping external notification for {task_id} (skip_external=True)")

        return notification

    def list_notifications(self, user_key: str, unread_only: bool = False) -> list:
        """Return notifications for a user, newest first."""
        with self._lock:
            notifications = self._load()
        user_notifs = [n for n in notifications if n.get("user_key") == user_key]
        if unread_only:
            user_notifs = [n for n in user_notifs if not n.get("read", False)]
        return list(reversed(user_notifs))

    def mark_read(self, notification_id: str, user_key: str) -> bool:
        """Mark a notification as read. Returns True if found and updated."""
        with self._lock:
            notifications = self._load()
            found = False
            for n in notifications:
                if n["notification_id"] == notification_id and n.get("user_key") == user_key:
                    n["read"] = True
                    found = True
                    break
            if found:
                self._save(notifications)
        return found

    def mark_all_read(self, user_key: str) -> int:
        """Mark all notifications for a user as read. Returns count updated."""
        with self._lock:
            notifications = self._load()
            count = 0
            for n in notifications:
                if n.get("user_key") == user_key and not n.get("read", False):
                    n["read"] = True
                    count += 1
            if count:
                self._save(notifications)
        return count

    def delete_notification(self, notification_id: str, user_key: str) -> bool:
        """Delete a notification. Returns True if found and deleted."""
        with self._lock:
            notifications = self._load()
            before = len(notifications)
            notifications = [
                n for n in notifications
                if not (n["notification_id"] == notification_id and n.get("user_key") == user_key)
            ]
            if len(notifications) < before:
                self._save(notifications)
                return True
        return False

    def delete_all_read(self, user_key: str) -> int:
        """Delete all read notifications for a user."""
        with self._lock:
            notifications = self._load()
            before = len(notifications)
            notifications = [
                n for n in notifications
                if not (n.get("user_key") == user_key and n.get("read", False))
            ]
            deleted = before - len(notifications)
            if deleted:
                self._save(notifications)
        return deleted

    def _notify_telegram(self, notification: dict):
        """Send completion notification via Telegram to the task's originating user."""
        import sys
        try:
            repo_root = os.path.dirname(os.path.abspath(__file__))
            sys.path.insert(0, repo_root)
            from telegram_connector import TelegramConnector
            config_path = os.path.join(repo_root, "telegram_config.json")
            if not os.path.exists(config_path):
                return
            with open(config_path) as f:
                cfg = json.load(f)
            connector = TelegramConnector(cfg)
            msg = _format_notification_message(notification)
            # Extract numeric chat_id from user_key (format: telegram_<id>)
            user_key = notification.get("user_key", "")
            chat_id = user_key.replace("telegram_", "").strip()
            if chat_id:
                connector.send_message(chat_id, msg)
        except Exception as e:
            import sys as _sys
            print(f"[NotificationManager] Telegram notify failed: {e}", file=_sys.stderr)

    def _notify_telegram_broadcast(self, notification: dict):
        """Send completion notification to ALL configured Telegram users."""
        import sys
        try:
            repo_root = os.path.dirname(os.path.abspath(__file__))
            sys.path.insert(0, repo_root)
            from telegram_connector import TelegramConnector
            config_path = os.path.join(repo_root, "telegram_config.json")
            if not os.path.exists(config_path):
                return
            with open(config_path) as f:
                cfg = json.load(f)
            allowed_users = cfg.get("allowed_users", [])
            if not allowed_users:
                return
            connector = TelegramConnector(cfg)
            msg = _format_notification_message(notification)
            for chat_id in allowed_users:
                try:
                    connector.send_message(chat_id, msg)
                except Exception as e:
                    print(f"[NotificationManager] Telegram broadcast to {chat_id} failed: {e}", file=sys.stderr)
        except Exception as e:
            import sys as _sys
            print(f"[NotificationManager] Telegram broadcast failed: {e}", file=_sys.stderr)

    def _notify_webex(self, notification: dict):
        """Send completion notification via WebEx to the task's originating user."""
        import sys
        try:
            repo_root = os.path.dirname(os.path.abspath(__file__))
            sys.path.insert(0, repo_root)
            from webex_connector import WebEXConnector
            config_path = os.path.join(repo_root, "webex_config.json")
            if not os.path.exists(config_path):
                return
            with open(config_path) as f:
                cfg = json.load(f)
            connector = WebEXConnector(cfg)
            msg = _format_notification_message(notification)
            # Extract room/person id from user_key (format: webex_<id>)
            user_key = notification.get("user_key", "")
            person_id = user_key.replace("webex_", "").strip()
            if person_id:
                connector.send_message(person_id, msg)
        except Exception as e:
            import sys as _sys
            print(f"[NotificationManager] WebEx notify failed: {e}", file=_sys.stderr)

    def _notify_webex_broadcast(self, notification: dict):
        """Send completion notification to ALL configured WebEx users."""
        import sys
        try:
            repo_root = os.path.dirname(os.path.abspath(__file__))
            sys.path.insert(0, repo_root)
            from webex_connector import WebEXConnector
            config_path = os.path.join(repo_root, "webex_config.json")
            if not os.path.exists(config_path):
                return
            with open(config_path) as f:
                cfg = json.load(f)
            allowed_users = cfg.get("allowed_users", [])
            if not allowed_users:
                return
            connector = WebEXConnector(cfg)
            msg = _format_notification_message(notification)
            for person_id in allowed_users:
                try:
                    connector.send_message(person_id, msg)
                except Exception as e:
                    print(f"[NotificationManager] WebEx broadcast to {person_id} failed: {e}", file=sys.stderr)
        except Exception as e:
            import sys as _sys
            print(f"[NotificationManager] WebEx broadcast failed: {e}", file=_sys.stderr)


def _format_notification_message(notification: dict) -> str:
    """Format notification as human-readable text for external channels."""
    status = notification.get("status", "unknown")
    task_id = notification.get("task_id", "?")
    description = notification.get("description", "Background task")
    created_at = notification.get("created_at", "")

    if status == "completed":
        icon = "✅"
        title = "Task Completed"
        preview = notification.get("output_preview")
        body = f"\n\n📤 *Preview:*\n{preview[:300]}" if preview else ""
    else:
        icon = "❌"
        title = "Task Failed"
        error = notification.get("error")
        body = f"\n\n⚠️ *Error:*\n{error[:300]}" if error else ""

    return (
        f"{icon} *{title}*\n"
        f"📋 *Task:* `{task_id}`\n"
        f"📝 *Description:* {description[:200]}\n"
        f"🕐 *At:* {created_at}"
        f"{body}"
    )
