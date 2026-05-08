#!/usr/bin/env python3
"""
Cross-runtime session handoff for Wee Orchestrator.

When a user switches runtimes mid-session (e.g. /runtime set copilot while on claude),
this module exports a transcript of the previous session and writes a compact handoff
summary so the new runtime starts with full context.

Lifecycle:
  1. /runtime set <new_runtime> triggers detect_runtime_change()
  2. If runtime changed and history exists → export_transcript() + write_handoff_summary()
  3. On the user's first message to new runtime → build_agent_context_prompt() calls
     get_handoff_context(), which returns the summary and deletes it (one-time use)
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Optional

# Configure handoff logging
logger = logging.getLogger(__name__)
HANDOFF_LOG_DIR = Path(os.path.expanduser("~")) / ".copilot" / "logs"
HANDOFF_LOG_FILE = HANDOFF_LOG_DIR / "handoff.log"


def _setup_handoff_logger():
    """Initialize handoff logger with file handler."""
    HANDOFF_LOG_DIR.mkdir(parents=True, exist_ok=True)

    handoff_logger = logging.getLogger("session_handoff")

    # Only add handler if not already configured
    if not handoff_logger.handlers:
        handler = logging.FileHandler(HANDOFF_LOG_FILE)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        handoff_logger.addHandler(handler)
        handoff_logger.setLevel(logging.INFO)

    return handoff_logger


_handoff_logger = _setup_handoff_logger()


class SessionHandoff:
    MAX_TRANSCRIPT_MESSAGES = 50
    MAX_SUMMARY_MESSAGES = 20
    # Retry config for flush-timing race
    HISTORY_RETRY_ATTEMPTS = 3
    HISTORY_RETRY_DELAY = 0.5  # seconds between retries

    def __init__(self):
        home = os.path.expanduser("~")
        self.copilot_home = Path(home) / ".copilot"
        self.chat_history_path = self.copilot_home / "chat-history.json"
        self.session_map_path = self.copilot_home / "n8n-session-map.json"
        self.session_state_dir = self.copilot_home / "session-state"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_runtime_change(self, n8n_session_id: str, new_runtime: str) -> bool:
        """Return True if new_runtime differs from the currently stored runtime."""
        try:
            with open(self.session_map_path) as f:
                session_map = json.load(f)
            entry = session_map.get(n8n_session_id)
            if not entry or not isinstance(entry, dict):
                return False
            current_runtime = entry.get("runtime", "")
            changed = current_runtime != new_runtime and current_runtime != ""
            if changed:
                _handoff_logger.info(
                    f"Runtime change detected: {current_runtime} → {new_runtime} "
                    f"(n8n_session={n8n_session_id})"
                )
            return changed
        except Exception as e:
            _handoff_logger.warning(
                f"Error detecting runtime change for {n8n_session_id}: {e}"
            )
            return False

    def export_transcript(self, n8n_session_id: str, session_id: str) -> Optional[str]:
        """
        Read the last MAX_TRANSCRIPT_MESSAGES messages from chat history for this
        session_id, format as readable markdown, and write to
        ~/.copilot/session-state/{session_id}/transcript.md.
        Returns the file path, or None if no history was found.
        """
        # Chat history is indexed by n8n_session_id, not the internal copilot session_id
        messages = self._get_session_messages(n8n_session_id)
        if not messages:
            _handoff_logger.debug(
                f"No chat history to export for session_id={session_id} "
                f"(n8n_session={n8n_session_id})"
            )
            return None

        recent = messages[-self.MAX_TRANSCRIPT_MESSAGES :]

        # Gather session metadata from session map
        runtime, model, started_ts = self._get_session_meta(n8n_session_id)
        started_str = self._fmt_ts(started_ts) if started_ts else "unknown"

        lines = [
            "# Session Transcript",
            f"**Session ID:** {session_id}",
            f"**Started:** {started_str}",
            f"**Runtime:** {runtime}",
            "",
            "---",
            "",
        ]

        for msg in recent:
            role = msg.get("role", "unknown").capitalize()
            content = msg.get("content", "")
            ts = self._fmt_ts(msg.get("timestamp"))
            lines.append(f"**{role} [{ts}]:** {content}")
            lines.append("")

        out_path = self._session_state_path(session_id) / "transcript.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines), encoding="utf-8")

        _handoff_logger.debug(
            f"Transcript exported: {out_path} | "
            f"session_id={session_id} | messages={len(recent)}"
        )

        return str(out_path)

    def write_handoff_summary(
        self,
        n8n_session_id: str,
        new_session_id: str,
        prev_session_id: str,
        prev_runtime: str,
        new_runtime: str,
    ) -> Optional[str]:
        """
        Build a compact handoff block from the last MAX_SUMMARY_MESSAGES messages
        and write it to ~/.copilot/session-state/{new_session_id}/handoff.md.
        Also writes handoff_meta.json with prev_runtime and transcript path.
        Returns the handoff.md file path, or None if no history to summarise.
        """
        # Log handoff initiation
        _handoff_logger.info(
            f"HANDOFF INITIATED: {prev_runtime} → {new_runtime} | "
            f"prev_session={prev_session_id} new_session={new_session_id} | "
            f"n8n_session={n8n_session_id}"
        )

        # Chat history is indexed by n8n_session_id, not the internal copilot session_id
        messages = self._get_session_messages_with_retry(n8n_session_id)
        history_unavailable = not messages
        if history_unavailable:
            _handoff_logger.warning(
                f"No chat history found for n8n_session={n8n_session_id} after "
                f"{self.HISTORY_RETRY_ATTEMPTS} retries. "
                f"Writing fallback handoff note so the new runtime is informed."
            )

        if history_unavailable:
            # Write a minimal fallback note so the new runtime always has context
            switched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            fallback_lines = [
                "## Runtime Handoff Summary",
                f"**Previous Runtime:** {prev_runtime} | **New Runtime:** {new_runtime}",
                f"**Switched At:** {switched_at}",
                "",
                "> **Note:** Chat history could not be read at handoff time (likely a",
                "> flush-timing issue). No conversation context is available from the",
                "> previous runtime. Ask the user to recap if needed.",
            ]
            out_dir = self._session_state_path(new_session_id)
            out_dir.mkdir(parents=True, exist_ok=True)
            handoff_path = out_dir / "handoff.md"
            handoff_path.write_text("\n".join(fallback_lines), encoding="utf-8")
            meta = {
                "prev_runtime": prev_runtime,
                "new_runtime": new_runtime,
                "transcript_path": "",
                "switched_at": switched_at,
            }
            (out_dir / "handoff_meta.json").write_text(
                json.dumps(meta, indent=2), encoding="utf-8"
            )
            _handoff_logger.info(
                f"HANDOFF FALLBACK WRITTEN: {handoff_path} "
                f"(history unavailable for n8n_session={n8n_session_id})"
            )
            return str(handoff_path)

        recent = messages[-self.MAX_SUMMARY_MESSAGES :]
        switched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Summarise user messages (last 3-5)
        user_msgs = [m for m in recent if m.get("role") == "user"][-5:]
        user_summary_lines = []
        for m in user_msgs:
            snippet = (m.get("content") or "")[:200]
            if len(m.get("content", "")) > 200:
                snippet += "…"
            user_summary_lines.append(f"- {snippet}")
        user_summary = (
            "\n".join(user_summary_lines)
            if user_summary_lines
            else "(no user messages found)"
        )

        # Extract key file paths/directories mentioned
        all_text = " ".join(m.get("content", "") for m in recent)
        paths = self._extract_paths(all_text)
        paths_str = (
            "\n".join(f"- {p}" for p in paths[:10]) if paths else "(none detected)"
        )

        # Last assistant response (first 300 chars)
        last_assistant = next(
            (m for m in reversed(recent) if m.get("role") == "assistant"), None
        )
        last_response = ""
        if last_assistant:
            raw = last_assistant.get("content") or ""
            last_response = raw[:300] + ("…" if len(raw) > 300 else "")

        transcript_path = str(
            self._session_state_path(new_session_id) / "transcript.md"
        )

        summary_lines = [
            "## Runtime Handoff Summary",
            f"**Previous Runtime:** {prev_runtime} | **New Runtime:** {new_runtime}",
            f"**Switched At:** {switched_at}",
            "",
            "### What Was Being Worked On",
            user_summary,
            "",
            "### Key Context",
            f"- Files/paths mentioned:\n{paths_str}",
            "- Decisions made: (see full transcript for details)",
            "- Current task status: in progress",
            "",
            "### Last Assistant Response",
            last_response if last_response else "(none)",
            "",
            "### Full Transcript",
            f"Available at: {transcript_path}",
            "(Read this file if you need deeper context about the session)",
        ]

        out_dir = self._session_state_path(new_session_id)
        out_dir.mkdir(parents=True, exist_ok=True)

        handoff_path = out_dir / "handoff.md"
        handoff_path.write_text("\n".join(summary_lines), encoding="utf-8")

        meta = {
            "prev_runtime": prev_runtime,
            "new_runtime": new_runtime,
            "transcript_path": transcript_path,
            "switched_at": switched_at,
        }
        (out_dir / "handoff_meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )

        # Log successful handoff summary creation
        _handoff_logger.info(
            f"HANDOFF SUMMARY WRITTEN: {handoff_path} | "
            f"transcript={transcript_path} | "
            f"messages_included={len(recent)}"
        )

        return str(handoff_path)

    def get_handoff_context(
        self, n8n_session_id: str, session_id: str
    ) -> Optional[dict]:
        """
        Check if a handoff.md exists for this session_id.
        If yes, read it, delete it (and meta), and return a dict:
          {"content": str, "transcript_path": str, "prev_runtime": str}
        If no, return None.
        """
        out_dir = self._session_state_path(session_id)
        handoff_path = out_dir / "handoff.md"
        meta_path = out_dir / "handoff_meta.json"

        if not handoff_path.exists():
            _handoff_logger.debug(
                f"No handoff context found for session_id={session_id}"
            )
            return None

        try:
            content = handoff_path.read_text(encoding="utf-8")
            meta: dict = {}
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))

            prev_runtime = meta.get("prev_runtime", "unknown")
            new_runtime = meta.get("new_runtime", "unknown")

            # Log successful handoff context retrieval
            _handoff_logger.info(
                f"HANDOFF CONTEXT LOADED: session_id={session_id} | "
                f"{prev_runtime} → {new_runtime} | "
                f"switched_at={meta.get('switched_at', 'unknown')}"
            )

        except Exception as e:
            _handoff_logger.error(
                f"Error reading handoff context for session_id={session_id}: {e}"
            )
            return None
        finally:
            # Always delete after first read (one-time use)
            try:
                handoff_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                _handoff_logger.debug(
                    f"Handoff files cleaned up for session_id={session_id}"
                )
            except Exception as e:
                _handoff_logger.warning(
                    f"Error cleaning up handoff files for session_id={session_id}: {e}"
                )

        return {
            "content": content,
            "transcript_path": meta.get("transcript_path", ""),
            "prev_runtime": meta.get("prev_runtime", "unknown"),
        }

    @staticmethod
    def format_handoff_prompt(
        handoff_content: str, transcript_path: str, prev_runtime: str
    ) -> str:
        """Return a formatted string to prepend to the first message to the new runtime."""
        return (
            f"[RUNTIME HANDOFF — Continuing from {prev_runtime}]\n"
            f"Previous session summary:\n"
            f"{handoff_content}\n"
            f"Full transcript available at: {transcript_path} (read if needed for deeper context)\n"
            f"---\n\n"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session_state_path(self, session_id: str) -> Path:
        return self.session_state_dir / session_id

    def _get_session_messages_with_retry(self, session_id: str) -> list:
        """Retry _get_session_messages to handle chat-history flush timing."""
        for attempt in range(self.HISTORY_RETRY_ATTEMPTS):
            messages = self._get_session_messages(session_id)
            if messages:
                if attempt > 0:
                    _handoff_logger.info(
                        f"Chat history available after {attempt} retries "
                        f"(n8n_session={session_id})"
                    )
                return messages
            if attempt < self.HISTORY_RETRY_ATTEMPTS - 1:
                _handoff_logger.debug(
                    f"Chat history not yet available for n8n_session={session_id} "
                    f"(attempt {attempt + 1}/{self.HISTORY_RETRY_ATTEMPTS}), retrying..."
                )
                time.sleep(self.HISTORY_RETRY_DELAY)
        return []

    def _get_session_messages(self, session_id: str) -> list:
        """Scan chat-history.json for the matching session_id, return its messages."""
        try:
            with open(self.chat_history_path) as f:
                data = json.load(f)
        except Exception:
            return []

        for user_key, user_data in data.items():
            if not isinstance(user_data, dict):
                continue
            for session in user_data.get("sessions", []):
                if session.get("session_id") == session_id:
                    return session.get("messages", [])
        return []

    def _get_session_meta(self, n8n_session_id: str):
        """Return (runtime, model, created_at_timestamp) for a session."""
        try:
            with open(self.session_map_path) as f:
                session_map = json.load(f)
            entry = session_map.get(n8n_session_id, {})
            if isinstance(entry, dict):
                return (
                    entry.get("runtime", "unknown"),
                    entry.get("model", "unknown"),
                    None,
                )
        except Exception:
            pass
        return ("unknown", "unknown", None)

    @staticmethod
    def _fmt_ts(ts) -> str:
        """Format a unix timestamp as a readable string."""
        if not ts:
            return "unknown"
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
        except Exception:
            return "unknown"

    @staticmethod
    def _extract_paths(text: str) -> list:
        """Heuristically extract file system paths from text."""
        import re

        pattern = (
            r'(?<!\w)(/(?:opt|home|usr|var|etc|tmp|root|mnt|srv|run)[^\s\'"`,;)>\]]*)'
        )
        found = re.findall(pattern, text)
        # Deduplicate while preserving order
        seen = set()
        result = []
        for p in found:
            p = p.rstrip(".,;:")
            if p not in seen:
                seen.add(p)
                result.append(p)
        return result
