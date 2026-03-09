#!/usr/bin/env python3
"""
Session cleanup daemon for Copilot CLI
Removes stale/dead sessions that cause "already in use" errors.

Session lifecycle policy:
  - Sessions idle < 24 hours are NEVER deleted.
  - Sessions referenced in running-queries are NEVER deleted.
  - Sessions referenced in any n8n-session-map are NEVER deleted.
  - Only truly orphaned sessions older than 24h are cleaned up.
"""

import json
import os
import shutil
import time
from pathlib import Path
from datetime import datetime


def _ts() -> str:
    """ISO-8601 timestamp for log lines."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_json(path: Path) -> dict:
    """Safely load a JSON file, returning {} on any error."""
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as exc:
        print(f"[{_ts()}] Warning: could not read {path}: {exc}")
        return {}


def _referenced_backend_sessions(*map_files: Path) -> set:
    """Collect all backend session_ids referenced across session maps."""
    referenced = set()
    for mf in map_files:
        data = _load_json(mf)
        for _key, entry in data.items():
            if isinstance(entry, dict):
                sid = entry.get("session_id")
                if sid:
                    referenced.add(sid)
            elif isinstance(entry, str):
                referenced.add(entry)
    return referenced


def cleanup_sessions():
    """Remove stale sessions from session-state directory and references."""
    copilot_home = Path.home() / ".copilot"
    session_state_dir = copilot_home / "session-state"

    # Both prod and dev session maps — never delete sessions referenced by either
    session_map_files = [
        copilot_home / "n8n-session-map.json",
        copilot_home / "n8n-session-map-dev.json",
    ]

    # Both prod and dev running-queries
    running_queries_files = [
        copilot_home / "running-queries.json",
        copilot_home / "running-queries-dev.json",
    ]

    if not session_state_dir.exists():
        return

    # Gather all session IDs actively referenced in session maps
    referenced_sessions = _referenced_backend_sessions(*session_map_files)

    # Gather all session IDs in running-queries
    active_query_sessions: set = set()
    for rq_file in running_queries_files:
        rq_data = _load_json(rq_file)
        active_query_sessions.update(rq_data.keys())

    # 24-hour stale threshold — sessions idle for less are kept
    current_time = time.time()
    stale_threshold = int(os.environ.get("SESSION_STALE_THRESHOLD", "86400"))  # 24h default

    removed_sessions = []
    skipped_referenced = 0
    skipped_active = 0
    skipped_fresh = 0

    for session_dir in session_state_dir.iterdir():
        if not session_dir.is_dir():
            continue

        session_id = session_dir.name

        # NEVER delete sessions referenced in any session map
        if session_id in referenced_sessions:
            skipped_referenced += 1
            continue

        # NEVER delete sessions with active running queries
        if session_id in active_query_sessions:
            skipped_active += 1
            continue

        # Check modification time against stale threshold
        try:
            mtime = session_dir.stat().st_mtime
            age_seconds = current_time - mtime
            if age_seconds <= stale_threshold:
                skipped_fresh += 1
                continue

            # Safe to remove — stale and unreferenced
            shutil.rmtree(session_dir)
            removed_sessions.append(session_id)
            age_hours = age_seconds / 3600
            print(
                f"[{_ts()}] Removed stale session: {session_id} "
                f"(idle {age_hours:.1f}h, threshold {stale_threshold/3600:.0f}h)"
            )
        except Exception as exc:
            print(f"[{_ts()}] Error cleaning session {session_id}: {exc}")

    # Clean up orphaned references in session maps (only if backend dir is gone
    # AND we didn't just delete it — i.e. it was already missing)
    for session_map_file in session_map_files:
        if not session_map_file.exists():
            continue
        try:
            session_map = _load_json(session_map_file)
            if not session_map:
                continue

            initial_count = len(session_map)
            keys_to_remove = []
            for key in list(session_map.keys()):
                entry = session_map[key]
                backend_id = entry.get("session_id") if isinstance(entry, dict) else entry
                if not backend_id:
                    continue

                # Only remove map entries for sessions WE deleted this cycle
                # Do NOT remove entries just because the backend dir is missing —
                # the session may be using a different storage backend (claude projects, etc.)
                if backend_id in removed_sessions:
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                del session_map[key]

            if keys_to_remove:
                with open(session_map_file, "w") as f:
                    json.dump(session_map, f, indent=2)
                print(
                    f"[{_ts()}] Cleaned {len(keys_to_remove)} entries from "
                    f"{session_map_file.name}"
                )
        except Exception as exc:
            print(f"[{_ts()}] Error cleaning {session_map_file.name}: {exc}")

    # Summary log
    total_dirs = sum(1 for d in session_state_dir.iterdir() if d.is_dir())
    print(
        f"[{_ts()}] Cleanup summary: "
        f"removed={len(removed_sessions)} "
        f"skipped_referenced={skipped_referenced} "
        f"skipped_active={skipped_active} "
        f"skipped_fresh={skipped_fresh} "
        f"remaining={total_dirs} "
        f"threshold={stale_threshold/3600:.0f}h"
    )


if __name__ == "__main__":
    print(f"[{_ts()}] Session cleanup daemon started (PID {os.getpid()})")
    # Run cleanup every 5 minutes
    while True:
        try:
            cleanup_sessions()
        except Exception as exc:
            print(f"[{_ts()}] Cleanup error: {exc}")
        time.sleep(300)  # 5 minutes
