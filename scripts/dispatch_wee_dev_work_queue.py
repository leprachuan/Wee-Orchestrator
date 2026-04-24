#!/usr/bin/env python3
"""Dispatch wee-dev work queue items from GitHub Issues.

Reads issues labelled ``wee-dev`` from leprachuan/Wee-Orchestrator and
dispatches them as background tasks via the Wee Orchestrator API.

Replaces the former WORK_QUEUE.md / FEATURE_QUEUE.md markdown-based system.
GitHub Issues are the source of truth as of 2026-04-04.
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

RUNNING_STATUSES = {"created", "queued", "pending", "running", "in_progress"}

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

DRY_RUN = False

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(f"[{now_iso()}] {msg}")


# ---------------------------------------------------------------------------
# GitHub helpers  (all use ``gh`` CLI which handles auth)
# ---------------------------------------------------------------------------


def gh(*args: str, check: bool = True) -> str:
    """Run a ``gh`` command and return stdout."""
    cmd = ["gh"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"gh command failed ({result.returncode}): "
            f"{' '.join(cmd)}\n{result.stderr.strip()}"
        )
    return result.stdout.strip()


def ensure_labels() -> None:
    """Create any missing labels in the repository."""
    existing_raw = gh(
        "label", "list", "--repo", REPO, "--limit", "200", "--json", "name"
    )
    existing = {lbl["name"] for lbl in json.loads(existing_raw)}
    for name, colour in REQUIRED_LABELS.items():
        if name not in existing:
            if DRY_RUN:
                log(f"[dry-run] Would create label {name!r} (#{colour})")
                continue
            gh(
                "label",
                "create",
                name,
                "--repo",
                REPO,
                "--color",
                colour,
                "--description",
                f"wee-dev work queue status: {name}",
                "--force",
            )
            log(f"Created label {name!r}")


def fetch_github_issues() -> list[dict]:
    """Fetch open issues labelled ``wee-dev`` and return work-item dicts."""
    raw = gh(
        "issue",
        "list",
        "--repo",
        REPO,
        "--label",
        "wee-dev",
        "--state",
        "open",
        "--limit",
        "100",
        "--json",
        "number,title,labels,body,author,createdAt,updatedAt",
    )
    issues = json.loads(raw)
    items = []
    for issue in issues:
        items.append(_issue_to_work_item(issue))
    # Sort by issue number ascending (oldest first — FIFO)
    items.sort(key=lambda x: x["number"])
    return items


def _issue_to_work_item(issue: dict) -> dict:
    """Map a GitHub issue JSON object to the internal work-item dict."""
    label_names = {lbl["name"] for lbl in issue.get("labels", [])}
    status = "queued"  # default for open issues
    for label, mapped_status in STATUS_LABEL_PRIORITY:
        if label in label_names:
            status = mapped_status
            break

    is_bug = "bug" in label_names
    is_enhancement = "enhancement" in label_names or "feature" in label_names

    return {
        "number": issue["number"],
        "id": f"#{issue['number']}",
        "title": issue["title"],
        "status": status,
        "labels": label_names,
        "body": issue.get("body", "") or "",
        "user_login": (issue.get("author") or {}).get("login", ""),
        "is_bug": is_bug,
        "is_enhancement": is_enhancement,
        "created_at": issue.get("createdAt", ""),
        "updated_at": issue.get("updatedAt", ""),
    }


def add_label(issue_number: int, label: str) -> None:
    if DRY_RUN:
        log(f"[dry-run] Would add label {label!r} to #{issue_number}")
        return
    gh(
        "issue",
        "edit",
        str(issue_number),
        "--repo",
        REPO,
        "--add-label",
        label,
    )


def remove_label(issue_number: int, label: str) -> None:
    if DRY_RUN:
        log(f"[dry-run] Would remove label {label!r} from #{issue_number}")
        return
    gh(
        "issue",
        "edit",
        str(issue_number),
        "--repo",
        REPO,
        "--remove-label",
        label,
        check=False,  # label may not be present — that's OK
    )


def add_comment(issue_number: int, body: str) -> None:
    if DRY_RUN:
        log(f"[dry-run] Would comment on #{issue_number}: {body[:80]}...")
        return
    gh(
        "issue",
        "comment",
        str(issue_number),
        "--repo",
        REPO,
        "--body",
        body,
    )


def close_issue(issue_number: int, comment: str) -> None:
    if DRY_RUN:
        log(f"[dry-run] Would close #{issue_number}")
        return
    gh(
        "issue",
        "close",
        str(issue_number),
        "--repo",
        REPO,
        "--comment",
        comment,
    )


def transition_status(
    issue_number: int, from_label: str | None, to_label: str, note: str
) -> None:
    """Remove ``from_label`` (if any) and add ``to_label``, plus a comment."""
    if from_label:
        remove_label(issue_number, from_label)
    add_label(issue_number, to_label)
    add_comment(issue_number, note)


# ---------------------------------------------------------------------------
# Safety gate
# ---------------------------------------------------------------------------


def passes_safety_gate(item: dict) -> bool:
    """Return True if the issue is safe to auto-dispatch.

    Only issues filed by the repo owner (leprachuan) or explicitly approved
    (``wee-dev:approved`` label) may be dispatched automatically.
    """
    if "wee-dev:approved" in item["labels"]:
        log(f"  {item['id']} has wee-dev:approved — cleared for dispatch")
        return True
    if item["user_login"] == OWNER_LOGIN:
        log(f"  {item['id']} filed by {OWNER_LOGIN} — cleared for dispatch")
        return True

    # Not approved — tag and skip
    log(
        f"  {item['id']} filed by {item['user_login']!r} "
        f"(not {OWNER_LOGIN}) — needs approval"
    )
    if "wee-dev:needs-approval" not in item["labels"]:
        add_label(item["number"], "wee-dev:needs-approval")
        add_comment(
            item["number"],
            "⏳ Awaiting owner approval before wee-dev picks this up. "
            f"Filed by @{item['user_login']}; only issues from "
            f"@{OWNER_LOGIN} or labelled `wee-dev:approved` are "
            "auto-dispatched.",
        )
    return False


# ---------------------------------------------------------------------------
# Lock management  (preserves existing concurrency guard)
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


def create_lock(payload: dict) -> bool:
    """Atomically create the lock file.  Returns False if already held."""
    payload = {
        **payload,
        "lock_path": str(LOCK_PATH),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    return True


def clear_lock() -> None:
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Wee Orchestrator API helpers
# ---------------------------------------------------------------------------


def load_api_key() -> str:
    for line in ENV_PATH.read_text().splitlines():
        if line.startswith("API_SHARED_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("API_SHARED_KEY not found in /opt/n8n-copilot-shim/.env")


def api_request(
    method: str, url: str, api_key: str, payload: dict | None = None
) -> dict:
    headers = {
        "Authorization": f"Bearer shared_{api_key}",
        "X-User-Identity": USER_IDENTITY,
        "X-Auth-Channel": AUTH_CHANNEL,
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, headers=headers, data=data, method=method)
    ctx = ssl._create_unverified_context()
    with request.urlopen(req, context=ctx) as response:
        return json.load(response)


def _is_pid_alive(pid: int) -> bool:
    """Return True if the process with *pid* is still running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def dispatch_via_subprocess(
    agent: str,
    prompt: str,
    model: str,
    timeout: int,
    session_id: str | None = None,
) -> int:
    """Spawn agent_manager.py as a detached subprocess, bypassing the public API.

    Using the public background-tasks API causes session leakage (issue #74).
    This function calls agent_manager.py directly with start_new_session=True
    so the child is fully detached and does not inherit the parent session context.
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
        "copilot",
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


def get_running_agents(api_key: str) -> set[str]:
    """Return agent names that have at least one active task (API-based)."""
    data = api_request("GET", BACKGROUND_TASKS_URL, api_key)
    running: set[str] = set()
    for task in data.get("tasks", []):
        if str(task.get("status", "")).lower() in RUNNING_STATUSES:
            agent = task.get("agent")
            if agent:
                running.add(agent)
    return running


def _is_bg_task_running(task_id: str) -> bool:
    """Check if a background task is still running via the orchestrator API."""
    try:
        api_key = load_api_key()
        url = f"{BACKGROUND_TASKS_URL}/{task_id}"
        data = api_request("GET", url, api_key)
        status = str(data.get("status", "")).lower()
        return status in RUNNING_STATUSES
    except Exception:
        # API unreachable — assume task may still be running (fail-safe)
        return True


def has_running_wee_dev_task() -> bool:
    """Return True if a wee-dev task from the lock file is still running.

    Checks both PID (subprocess dispatch) and task_id (API dispatch).
    Fixed in Issue #148: previously only checked PID, causing the gate
    to miss API-dispatched tasks on the dev host.
    """
    lock = read_lock()
    if lock is None:
        return False
    # Check PID first (subprocess dispatch on prod)
    pid = lock.get("wee_dev_pid")
    if pid is not None and _is_pid_alive(int(pid)):
        return True
    # Check background task ID (API dispatch on dev)
    task_id = lock.get("wee_dev_task_id")
    if task_id is not None:
        return _is_bg_task_running(task_id)
    return False


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
        "dispatch wee-qa for review. Update the GitHub issue labels and add "
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
        "Changes are on dev host 192.168.1.100 in /opt/n8n-copilot-shim-dev/. "
        "Run tests, check code quality, verify the implementation matches the "
        "issue requirements. If QA passes, add label wee-dev:qa-review → close "
        "the issue with the commit SHA. If QA fails, add label wee-dev:qa-failed "
        "and comment with the failures."
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
    args = parser.parse_args()

    global DRY_RUN
    DRY_RUN = args.dry_run
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
    # --- Stalled qa-review detection ---
    if active_item and active_item["status"] == "qa-review":
        if has_running_wee_qa_task():
            write_lock(
                {
                    "state": "qa-review",
                    "reason": "Waiting for wee-qa to finish review.",
                    "work_item_id": active_item["id"],
                    "work_item_title": active_item["title"],
                    "github_issue": active_item["number"],
                }
            )
            log(f"wee-qa is actively reviewing {active_item['id']} — no action.")
            return 0

        # No wee-qa running — stalled; re-dispatch
        log(
            f"Stalled qa-review detected for {active_item['id']} "
            f"— re-dispatching wee-qa"
        )
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
        log(
            f"Re-dispatched wee-qa subprocess PID={result.get('pid')} "
            f"for {active_item['id']}."
        )
        return 0

    # --- Check if wee-dev is already running ---
    wee_dev_running = has_running_wee_dev_task()
    if wee_dev_running:
        lock_payload: dict = {
            "state": "wee-dev-running",
            "reason": "wee-dev already has a running or queued background task.",
        }
        if active_item:
            lock_payload["work_item_id"] = active_item["id"]
            lock_payload["work_item_title"] = active_item["title"]
            lock_payload["github_issue"] = active_item["number"]
        write_lock(lock_payload)
        log("wee-dev already has a running or queued background task.")
        return 0

    # --- Stalled in-progress / qa-failed detection (Issue #148 fix) ---
    # When an active item is in-progress (wee-dev process died) or qa-failed,
    # always re-dispatch THAT item — never fall through to pick a new one.
    next_item = None
    if active_item and active_item["status"] == "in-progress":
        log(
            f"Stalled in-progress detected for {active_item['id']} "
            f"— re-dispatching wee-dev for the SAME issue"
        )
        next_item = active_item
    elif active_item and active_item["status"] == "qa-failed":
        log(
            f"qa-failed detected for {active_item['id']} "
            f"— re-dispatching wee-dev to address review feedback"
        )
        next_item = active_item

    # --- Catch-all QA gate (Issue #148) ---
    # If there is ANY active item (in-progress, qa-review, qa-failed), do NOT
    # pick up a new queued issue. wee-dev must work serially: finish one issue
    # completely (through QA approval + merge) before starting the next.
    if next_item is None and active_item and active_item["status"] in ACTIVE_STATUSES:
        log(
            f"QA gate: active item {active_item['id']} is in "
            f"{active_item['status']!r} state — blocking new issue dispatch. "
            f"wee-dev must wait for QA verdict before dequeuing."
        )
        write_lock(
            {
                "state": "qa-gate-blocked",
                "reason": (
                    f"QA gate blocked: {active_item['id']} is {active_item['status']}. "
                    f"wee-dev must wait for QA approval before picking up a new issue."
                ),
                "work_item_id": active_item["id"],
                "work_item_title": active_item["title"],
                "github_issue": active_item["number"],
            }
        )
        return 0

    # --- Nothing to do ---
    if next_item is None and not actionable and not active_item:
        log("No actionable issues (all may be in-progress/qa-review).")
        return 0

    if (
        next_item is None
        and not actionable
        and active_item
        and active_item["status"] not in ACTIONABLE_STATUSES
    ):
        log(
            f"Active item {active_item['id']} is"
            f" {active_item['status']} — nothing to dispatch."
        )
        return 0

    # --- Pick next work item ---
    # At this point, next_item is set only for stalled in-progress/qa-failed.
    # Otherwise, pick from actionable queue (all active items were gated above).
    if next_item is None:
        if active_item and active_item["status"] in ACTIONABLE_STATUSES:
            next_item = active_item
        elif actionable:
            next_item = actionable[0]
        elif active_item:
            next_item = active_item
        else:
            log("No item to dispatch.")
            return 0

    # --- Safety gate ---
    if not passes_safety_gate(next_item):
        log(f"Skipping {next_item['id']} — awaiting approval.")
        # Try the next actionable item
        for candidate in actionable:
            if candidate["number"] == next_item["number"]:
                continue
            if passes_safety_gate(candidate):
                next_item = candidate
                break
        else:
            log("No approved actionable issues remain.")
            return 0

    log(f"Dispatching: {next_item['id']} — {next_item['title']}")

    # --- Clean stale lock and create new one ---
    existing_lock = read_lock()
    if existing_lock:
        clear_lock()

    if not create_lock(
        {
            "state": "dispatching",
            "reason": "Dispatching wee-dev for the next work item.",
            "work_item_id": next_item["id"],
            "work_item_title": next_item["title"],
            "github_issue": next_item["number"],
        }
    ):
        log("Work queue lock already exists; skipping dispatch.")
        return 0

    # --- Update issue labels → in-progress ---
    current_status_label = None
    for label, _ in STATUS_LABEL_PRIORITY:
        if label in next_item["labels"]:
            current_status_label = label
            break
    transition_status(
        next_item["number"],
        current_status_label,
        "wee-dev:in-progress",
        f"🚀 wee-dev is picking up this issue ({now_iso()}).",
    )

    # --- Dispatch ---
    try:
        result = dispatch_wee_dev(next_item)
    except OSError as exc:
        clear_lock()
        log(f"Failed to dispatch wee-dev subprocess: {exc}")
        return 1
    except Exception:
        clear_lock()
        raise

    write_lock(
        {
            "state": "wee-dev-running",
            "reason": "wee-dev dispatched from the work queue runner.",
            "work_item_id": next_item["id"],
            "work_item_title": next_item["title"],
            "github_issue": next_item["number"],
            "wee_dev_pid": result.get("pid"),
        }
    )
    log(
        f"Dispatched wee-dev subprocess PID={result.get('pid')} "
        f"for issue {next_item['id']}."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"dispatch_wee_dev_work_queue failed: {exc}")
        raise
