#!/usr/bin/env python3
"""
kanban_push.py — Reliable Kanban board updater for Wee Canvas.

Maintains phase state in a JSON file and re-renders the full board on every
call. This is more reliable than c.update() per item because:
  1. Items move between columns correctly (Planned → In Progress → Done)
  2. Titles are always preserved (no [unknown: undefined] bug)
  3. Log lines accumulate correctly

Usage:
  # Initialize (call once before dispatching the background task):
  python3 kanban_push.py init <session_id> '<phases_json>'
    phases_json = '[{"id":"p1","title":"Phase 1: Setup"}, ...]'

  # Update a phase status + append a log line:
  python3 kanban_push.py update <session_id> <phase_id> <status> '<log_line>'
    status = running | done | error | pending

  # Mark final completion:
  python3 kanban_push.py complete <session_id> '<log_line>'

State file: /tmp/kanban_<session_id>.json
"""

import json
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


STATE_DIR = "/tmp"


def state_path(session_id):
    return os.path.join(STATE_DIR, f"kanban_{session_id}.json")


def load_state(session_id):
    p = state_path(session_id)
    if os.path.exists(p):
        return json.load(open(p))
    return None


def save_state(session_id, state):
    json.dump(state, open(state_path(session_id), "w"), indent=2)


def push_board(session_id, state):
    """Re-render the full board from current state."""
    from canvas import Canvas

    c = Canvas(session_id=session_id)

    phases = state["phases"]
    log_lines = state.get("log_lines", [])
    title = state.get("title", "Background Task")
    runtime = state.get("runtime", "")
    model = state.get("model", "")
    timeout = state.get("timeout", "")

    # Sort phases into columns based on status
    done_items = [p for p in phases if p.get("status") == "done"]
    running_items = [p for p in phases if p.get("status") == "running"]
    error_items = [p for p in phases if p.get("status") == "error"]
    todo_items = [p for p in phases if p.get("status", "pending") == "pending"]

    overall_status = state.get("overall_status", "🟡 Running...")

    components = [
        {"type": "heading", "level": 1, "text": title},
        {
            "type": "text",
            "text": f"Runtime: {runtime} · Model: {model} · Timeout: {timeout}",
            "muted": True,
        },
        {"type": "divider"},
        {
            "type": "row",
            "children": [
                {
                    "type": "metric",
                    "id": "status-metric",
                    "label": "Status",
                    "value": overall_status,
                },
                {
                    "type": "metric",
                    "id": "runtime-metric",
                    "label": "Runtime",
                    "value": runtime,
                },
                {
                    "type": "metric",
                    "id": "model-metric",
                    "label": "Model",
                    "value": model,
                },
                {
                    "type": "metric",
                    "id": "timeout-metric",
                    "label": "Timeout",
                    "value": timeout,
                },
            ],
        },
        {"type": "divider"},
        {"type": "heading", "level": 3, "text": "Phases"},
        {
            "type": "board",
            "columns": [
                {
                    "id": "col-todo",
                    "title": "⏳ Planned",
                    "items": [{"id": p["id"], "title": p["title"]} for p in todo_items],
                },
                {
                    "id": "col-running",
                    "title": "🔄 In Progress",
                    "items": [
                        {
                            "id": p["id"],
                            "title": p["title"],
                            "status": p.get("status", "running"),
                        }
                        for p in running_items + error_items
                    ],
                },
                {
                    "id": "col-done",
                    "title": "✅ Done",
                    "items": [
                        {"id": p["id"], "title": p["title"], "status": "done"}
                        for p in done_items
                    ],
                },
            ],
        },
    ]

    if log_lines:
        components += [
            {"type": "divider"},
            {
                "type": "log",
                "id": "build-log",
                "label": "📋 Build Log",
                "lines": log_lines[-30:],
            },
        ]

    c.render(components)


def cmd_init(
    session_id,
    phases_json,
    title="Background Task",
    runtime="copilot",
    model="",
    timeout="60 min",
    first_phase_running=True,
):
    """Initialize kanban state and render initial board."""
    phases = json.loads(phases_json)

    # Optionally mark first phase as running
    if first_phase_running and phases:
        phases[0]["status"] = "running"

    state = {
        "session_id": session_id,
        "title": title,
        "runtime": runtime,
        "model": model,
        "timeout": timeout,
        "phases": phases,
        "log_lines": [f'[INIT] {datetime.now().strftime("%H:%M:%S")} Task started'],
        "overall_status": "🟡 Running...",
    }
    save_state(session_id, state)
    push_board(session_id, state)
    print(f"[kanban_push] Initialized with {len(phases)} phases → canvas {session_id}")


def cmd_update(session_id, phase_id, status, log_line=""):
    """Update a phase status and optionally append a log line."""
    state = load_state(session_id)
    if not state:
        print(
            f"[kanban_push] ERROR: no state found for session {session_id}. Run init first."
        )
        sys.exit(1)

    # Update matching phase
    for p in state["phases"]:
        if p["id"] == phase_id:
            p["status"] = status
            break

    if log_line:
        ts = datetime.now().strftime("%H:%M:%S")
        state["log_lines"].append(f"[{ts}] {log_line}")

    save_state(session_id, state)
    push_board(session_id, state)
    print(
        f"[kanban_push] {phase_id} → {status}" + (f" | {log_line}" if log_line else "")
    )


def cmd_complete(session_id, log_line="All phases complete"):
    """Mark task as complete (update status metric, final log)."""
    state = load_state(session_id)
    if not state:
        print(f"[kanban_push] ERROR: no state for {session_id}")
        sys.exit(1)

    state["overall_status"] = "✅ Complete"
    ts = datetime.now().strftime("%H:%M:%S")
    state["log_lines"].append(f"[{ts}] {log_line}")
    save_state(session_id, state)
    push_board(session_id, state)
    print(f"[kanban_push] Task marked complete → canvas {session_id}")


def cmd_log(session_id, log_line):
    """Append a log line without changing any phase status."""
    state = load_state(session_id)
    if not state:
        print(f"[kanban_push] ERROR: no state for {session_id}")
        sys.exit(1)
    ts = datetime.now().strftime("%H:%M:%S")
    state["log_lines"].append(f"[{ts}] {log_line}")
    save_state(session_id, state)
    push_board(session_id, state)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    session_id = sys.argv[2]

    if cmd == "init":
        # kanban_push.py init <session_id> <phases_json> [title] [runtime] [model] [timeout]
        phases_json = sys.argv[3] if len(sys.argv) > 3 else "[]"
        title = sys.argv[4] if len(sys.argv) > 4 else "Background Task"
        runtime = sys.argv[5] if len(sys.argv) > 5 else "copilot"
        model = sys.argv[6] if len(sys.argv) > 6 else ""
        timeout = sys.argv[7] if len(sys.argv) > 7 else "60 min"
        cmd_init(session_id, phases_json, title, runtime, model, timeout)

    elif cmd == "update":
        # kanban_push.py update <session_id> <phase_id> <status> [log_line]
        phase_id = sys.argv[3]
        status = sys.argv[4]
        log_line = sys.argv[5] if len(sys.argv) > 5 else ""
        cmd_update(session_id, phase_id, status, log_line)

    elif cmd == "complete":
        # kanban_push.py complete <session_id> [log_line]
        log_line = sys.argv[3] if len(sys.argv) > 3 else "All phases complete"
        cmd_complete(session_id, log_line)

    elif cmd == "log":
        # kanban_push.py log <session_id> <message>
        log_line = sys.argv[3] if len(sys.argv) > 3 else ""
        cmd_log(session_id, log_line)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
