#!/usr/bin/env python3
"""Dispatch wee-dev work queue items from GitHub Issues.

Reads issues labelled ``wee-dev`` from leprachuan/Wee-Orchestrator and
dispatches them as background tasks via the Wee Orchestrator API.

GitHub Issues are the single source of truth for queue state.  Label
transitions drive the entire state machine — no local lock file needed.

State machine (via GitHub labels):
  [open + wee-dev]     → default: queued
  wee-dev:in-progress  → wee-dev is implementing
  wee-dev:qa-review    → wee-qa is reviewing
  wee-dev:qa-failed    → wee-qa rejected; wee-dev needs to rework
  wee-dev:approved     → (closed) QA passed; merged to dev

Dispatching: subprocess via agent_manager.py (Issue #74 — avoids session
leakage that occurred with the background-tasks API).
"""

import argparse
import json
import os
import ssl
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib import request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO = "leprachuan/Wee-Orchestrator"
LOCK_PATH = Path("/opt/wee-dev/WORK_QUEUE.lock.json")
ENV_PATH = Path("/opt/n8n-copilot-shim/.env")
BACKGROUND_TASKS_URL = "https://127.0.0.1:8000/api/v1/background-tasks"
USER_IDENTITY = "8193231291"
AUTH_CHANNEL = "telegram"
OWNER_LOGIN = "leprachuan"

RUNNING_STATUSES = frozenset({"created", "queued", "pending", "running", "in_progress"})

AGENT_MANAGER_PATH = Path("/opt/n8n-copilot-shim/agent_manager.py")
AGENTS_CONFIG_PATH = Path("/opt/n8n-copilot-shim/agents.json")
DISPATCH_LOG_DIR = Path("/tmp/wee-dispatch-logs")

# Labels required in the repository (name → colour hex without #)
REQUIRED_LABELS = {
    "wee-dev": "0075ca",
    "wee-dev:in-progress": "e4e669",
    "wee-dev:qa-review": "d93f0b",
    "wee-dev:qa-failed": "e11d48",
    "wee-dev:approved": "0e8a16",
    "wee-dev:needs-approval": "f9a825",
    "wee-dev:queued": "cccccc",
}

# Priority: first label found wins
STATUS_LABEL_PRIORITY = [
    ("wee-dev:in-progress", "in-progress"),
    ("wee-dev:qa-review", "qa-review"),
    ("wee-dev:qa-failed", "qa-failed"),
    ("wee-dev:queued", "queued"),
]

# Statuses where work is actively happening (lock should exist)
ACTIVE_STATUSES = {"in-progress", "qa-review", "qa-failed"}
# Statuses where the dispatcher can pick up the item
ACTIONABLE_STATUSES = {"queued", "qa-failed"}

# Stalled item timeout: if an item is in qa-review/in-progress/qa-failed
# for longer than this duration, re-dispatch to recover from hangs
STALL_TIMEOUT_MINUTES = 30

DRY_RUN = False
FORCE = False
SKIP_BLOCKED = False

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(f"[{now_iso()}] {msg}")


def parse_iso_datetime(iso_str):
    """Parse ISO 8601 datetime string to datetime object."""
    try:
        if "Z" in iso_str:
            iso_str = iso_str.replace("Z", "+00:00")
        return datetime.fromisoformat(iso_str)
    except (ValueError, AttributeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------


def gh(*args: str, check: bool = True) -> str:
    """Run a ``gh`` CLI command and return stdout."""
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=check
    )
    return result.stdout


def ensure_labels() -> None:
    """Create any missing required labels in the repository."""
    for label, color in REQUIRED_LABELS.items():
        gh(
            "label",
            "create",
            label,
            "--repo",
            REPO,
            "--color",
            color,
            "--force",
            check=False,
        )


def fetch_github_issues() -> list[dict]:
    """Fetch open GitHub issues labelled ``wee-dev`` from the repository."""
    raw = gh(
        "issue",
        "list",
        "--repo",
        REPO,
        "--label",
        "wee-dev",
        "--state",
        "open",
        "--json",
        "number,title,body,labels,author",
        "--limit",
        "50",
    )
    issues = json.loads(raw)
    return [_issue_to_work_item(i) for i in issues if _passes_safety_gate(i)]


def _issue_to_work_item(issue: dict) -> dict:
    label_names = {lb["name"] for lb in issue.get("labels", [])}
    status = "queued"
    for label, st in STATUS_LABEL_PRIORITY:
        if label in label_names:
            status = st
            break
    return {
        "number": issue["number"],
        "id": f"#{issue['number']}",
        "title": issue["title"],
        "body": issue.get("body") or "",
        "status": status,
        "labels": label_names,
        "author": issue.get("author", {}).get("login", ""),
    }


def _passes_safety_gate(issue: dict) -> bool:
    return issue.get("author", {}).get("login", "") == OWNER_LOGIN


def add_label(issue_number: int, label: str) -> None:
    if DRY_RUN:
        log(f"[dry-run] Would add label '{label}' to #{issue_number}")
        return
    gh("issue", "edit", str(issue_number), "--repo", REPO, "--add-label", label)


def remove_label(issue_number: int, label: str) -> None:
    if DRY_RUN:
        log(f"[dry-run] Would remove label '{label}' from #{issue_number}")
        return
    gh(
        "issue",
        "edit",
        str(issue_number),
        "--repo",
        REPO,
        "--remove-label",
        label,
        check=False,
    )


def add_comment(issue_number: int, body: str) -> None:
    if DRY_RUN:
        log(f"[dry-run] Would comment on #{issue_number}: {body[:80]}")
        return
    gh("issue", "comment", str(issue_number), "--repo", REPO, "--body", body)


def close_issue(issue_number: int, comment: str) -> None:
    if DRY_RUN:
        log(f"[dry-run] Would close #{issue_number} with comment")
        return
    add_comment(issue_number, comment)
    gh("issue", "close", str(issue_number), "--repo", REPO)


def transition_status(issue_number: int, old_label: str, new_label: str) -> None:
    remove_label(issue_number, old_label)
    add_label(issue_number, new_label)


# ---------------------------------------------------------------------------
# Lock file helpers
# ---------------------------------------------------------------------------


def read_lock() -> dict | None:
    if not LOCK_PATH.exists():
        return None
    try:
        return json.loads(LOCK_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_lock(payload: dict) -> None:
    payload = {**payload, "lock_path": str(LOCK_PATH), "updated_at": now_iso()}
    LOCK_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def clear_lock() -> None:
    if LOCK_PATH.exists():
        LOCK_PATH.unlink()


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def load_api_key() -> str:
    if ENV_PATH.exists():
        with open(ENV_PATH) as f:
            for line in f:
                if line.startswith("API_SHARED_KEY="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("API_SHARED_KEY not found in .env")


def api_request(method: str, url: str, api_key: str, body: dict | None = None) -> dict:
    """Make an authenticated HTTP request to the orchestrator API."""
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    data = json.dumps(body).encode() if body else None
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer shared_{api_key}",
        "X-User-Identity": USER_IDENTITY,
        "X-Auth-Channel": AUTH_CHANNEL,
    }
    req = request.Request(url, data=data, headers=headers, method=method)
    with request.urlopen(req, context=ssl_ctx, timeout=30) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# Process / PID helpers
# ---------------------------------------------------------------------------


def _is_pid_alive(pid: int) -> bool:
    """Return True if the process with *pid* is still running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Dispatch via subprocess (Issue #74: avoid session leakage)
# ---------------------------------------------------------------------------


def dispatch_via_subprocess(
    agent: str,
    prompt: str,
    model: str,
    timeout: int,
    session_id: str | None = None,
) -> int:
    """Spawn agent_manager.py as a detached subprocess.

    Bypasses the public background-tasks API to avoid session leakage
    (Issue #74).  The child process inherits no parent session context.
    Returns the PID of the spawned process.
    """
    sid = session_id or str(uuid.uuid4())
    DISPATCH_LOG_DIR.mkdir(exist_ok=True)
    log_file = DISPATCH_LOG_DIR / f"{agent}-{sid[:8]}.log"

    cmd = [
        sys.executable,
        str(AGENT_MANAGER_PATH),
        "--agent",
        agent,
        "--runtime",
        "claude",
        "--model",
        model,
        "--config",
        str(AGENTS_CONFIG_PATH),
        prompt,
        sid,
    ]
    with open(log_file, "a") as lf:
        proc = subprocess.Popen(
            cmd,
            stdout=lf,
            stderr=lf,
            start_new_session=True,
        )
    log(f"Spawned {agent} subprocess PID={proc.pid} log={log_file}")
    return proc.pid


# ---------------------------------------------------------------------------
# Running task checks
# ---------------------------------------------------------------------------


def has_running_wee_dev_task() -> bool:
    """Return True if a wee-dev subprocess from the lock file is still alive."""
    lock = read_lock()
    if lock is None:
        return False
    pid = lock.get("wee_dev_pid")
    return pid is not None and _is_pid_alive(int(pid))


def has_running_wee_qa_task() -> bool:
    """Return True if a wee-qa subprocess from the lock file is still alive."""
    lock = read_lock()
    if lock is None:
        return False
    pid = lock.get("wee_qa_pid")
    return pid is not None and _is_pid_alive(int(pid))


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------


def dispatch_wee_dev(item: dict) -> dict:
    """Dispatch wee-dev via detached subprocess (bypasses public API, see issue #74)."""
    prompt = (
        f"Work on GitHub issue #{item['number']} in {REPO}: {item['title']}.\n\n"
        f"Issue body:\n{item['body'][:2000]}\n\n"
        "Read the full issue on GitHub for details. Implement the fix/feature "
        "on the dev host (192.168.1.100) in /opt/n8n-copilot-shim-dev/. "
        "Follow /opt/wee-dev/AGENTS.md. When implementation is complete, "
        "add the label 'wee-dev:qa-review' to the issue — the dispatcher will "
        "pick it up and dispatch wee-qa. Update the GitHub issue labels and add "
        "comments as you progress. Do not work on more than this one issue."
    )
    if DRY_RUN:
        log(f"[dry-run] Would dispatch wee-dev for {item['id']}: {item['title']}")
        return {"pid": -1}
    pid = dispatch_via_subprocess("wee-dev", prompt, "claude-opus-4.6", 3600)
    return {"pid": pid}


def dispatch_wee_qa(item: dict) -> dict:
    """Dispatch wee-qa via detached subprocess (bypasses public API, see issue #74)."""
    prompt = (
        f"QA review for GitHub issue #{item['number']} in {REPO}: "
        f"{item['title']}. "
        f"Issue body:\n{item['body'][:1000]}\n\n"
        "Review the implementation on the dev host (192.168.1.100). "
        "Follow /opt/wee-dev/AGENTS.md for QA procedures."
    )
    if DRY_RUN:
        log(f"[dry-run] Would dispatch wee-qa for {item['id']}: {item['title']}")
        return {"pid": -1}
    pid = dispatch_via_subprocess("wee-qa", prompt, "claude-sonnet-4.6", 1800)
    return {"pid": pid}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def first_with_status(items: list[dict], statuses: set[str]) -> dict | None:
    for item in items:
        if item["status"] in statuses:
            return item
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dispatch wee-dev work from GitHub Issues"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without making changes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force dispatch even if a task appears to be running",
    )
    parser.add_argument(
        "--skip-blocked",
        action="store_true",
        help="Skip items with blocking dependencies (only dispatch ready items)",
    )
    args = parser.parse_args()

    global DRY_RUN, FORCE, SKIP_BLOCKED
    DRY_RUN = args.dry_run
    FORCE = args.force
    SKIP_BLOCKED = args.skip_blocked

    if DRY_RUN:
        log("=== DRY RUN MODE ===")

    # 1. Ensure labels exist
    log("Ensuring required GitHub labels exist...")
    ensure_labels()

    # 2. Fetch issues from GitHub
    log(f"Fetching open issues labelled 'wee-dev' from {REPO}...")
    items = fetch_github_issues()
    log(f"Found {len(items)} open wee-dev issue(s)")

    if not items:
        log("No open wee-dev issues. Queue is empty.")
        clear_lock()
        return 0

    # 3. Categorise
    active_item = first_with_status(items, ACTIVE_STATUSES)
    actionable = [i for i in items if i["status"] in ACTIONABLE_STATUSES]

    # 4. Handle qa-review state
    if active_item and active_item["status"] == "qa-review":
        if has_running_wee_qa_task():
            log(f"wee-qa is actively reviewing {active_item['id']} — no action.")
            return 0
        # wee-qa not running — stall recovery or re-dispatch
        log(f"wee-qa stalled on {active_item['id']} — re-dispatching")
        try:
            result = dispatch_wee_qa(active_item)
        except OSError as exc:
            log(f"Failed to re-dispatch wee-qa subprocess: {exc}")
            return 1
        write_lock(
            {
                "state": "qa-review",
                "reason": "wee-qa re-dispatched by stall detector.",
                "work_item_id": active_item["id"],
                "work_item_title": active_item["title"],
                "github_issue": active_item["number"],
                "wee_qa_pid": result.get("pid"),
            }
        )
        log(f"Re-dispatched wee-qa PID={result.get('pid')} for {active_item['id']}.")
        return 0

    # 5. Handle in-progress state
    if active_item and active_item["status"] == "in-progress":
        if has_running_wee_dev_task() and not FORCE:
            log(f"wee-dev is actively working on {active_item['id']} — no action.")
            return 0
        log(f"wee-dev stalled on {active_item['id']} — re-dispatching")
        try:
            result = dispatch_wee_dev(active_item)
        except OSError as exc:
            log(f"Failed to re-dispatch wee-dev subprocess: {exc}")
            return 1
        write_lock(
            {
                "state": "in-progress",
                "reason": "wee-dev re-dispatched by stall detector.",
                "work_item_id": active_item["id"],
                "work_item_title": active_item["title"],
                "github_issue": active_item["number"],
                "wee_dev_pid": result.get("pid"),
            }
        )
        return 0

    # 6. No active item — dispatch next actionable
    if not actionable:
        log("No actionable issues (queued or qa-failed). Nothing to dispatch.")
        return 0

    next_item = actionable[0]
    log(f"Dispatching: {next_item['id']} — {next_item['title']}")

    try:
        result = dispatch_wee_dev(next_item)
    except OSError as exc:
        log(f"Failed to dispatch wee-dev subprocess: {exc}")
        return 1

    write_lock(
        {
            "state": "in-progress",
            "reason": "wee-dev dispatched by scheduler.",
            "work_item_id": next_item["id"],
            "work_item_title": next_item["title"],
            "github_issue": next_item["number"],
            "wee_dev_pid": result.get("pid"),
        }
    )
    add_label(next_item["number"], "wee-dev:in-progress")
    add_comment(
        next_item["number"],
        f"wee-dev dispatched (PID={result.get('pid')}). Working on this now.",
    )
    log(f"Dispatched wee-dev PID={result.get('pid')} for {next_item['id']}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log(f"dispatch_wee_dev_work_queue failed: {exc}")
        raise
