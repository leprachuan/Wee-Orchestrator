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

Stall recovery: if a label indicates active work but no background task
is running for that agent, the dispatcher re-dispatches automatically on
the next 15-minute cycle.
"""

import argparse
import json
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO = "leprachuan/Wee-Orchestrator"
ENV_PATH = Path("/opt/n8n-copilot-shim/.env")
AGENTS_CONFIG_PATH = Path("/opt/n8n-copilot-shim/agents.json")
BACKGROUND_TASKS_URL = "https://127.0.0.1:8000/api/v1/background-tasks"
RUNTIME_STATE_PATH = Path("/opt/RUNTIME_STATE.md")
USER_IDENTITY = "8193231291"
AUTH_CHANNEL = "telegram"
OWNER_LOGIN = "leprachuan"

RUNNING_STATUSES = {"created", "queued", "pending", "running", "in_progress"}

# Labels required in the repository (name → colour hex without #)
REQUIRED_LABELS = {
    "wee-dev": "0075ca",
    "wee-dev:in-progress": "e4e669",
    "wee-dev:qa-review": "d93f0b",
    "wee-dev:qa-failed": "e11d48",
    "wee-dev:approved": "0e8a16",
    "wee-dev:needs-approval": "f9a825",
    "wee-dev:queued": "cccccc",
    "URGENT": "b60205",  # bright red — dispatched before bugs, before enhancements
}

# Priority: first label found wins when mapping an issue to a status string
STATUS_LABEL_PRIORITY = [
    ("wee-dev:in-progress", "in-progress"),
    ("wee-dev:qa-failed", "qa-failed"),
    ("wee-dev:qa-review", "qa-review"),
    ("wee-dev:queued", "queued"),
]
STATUS_LABELS = [label for label, _ in STATUS_LABEL_PRIORITY]

# Statuses where work is actively happening (stall detection applies)
ACTIVE_STATUSES = {"in-progress", "qa-review", "qa-failed"}

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


def is_issue_blocked(issue_number: int) -> tuple[bool, str]:
    """Check /opt/RUNTIME_STATE.md for a block on a specific issue.
    
    Returns (is_blocked, reason).  Looks for entries like:
      | RS-NNN | `wee-dev:issue-190` | ... | YYYY-MM-DD HH:MM UTC | ... |
    A row matches only when its ``Blocked Until`` timestamp is still in the future.
    """
    if not RUNTIME_STATE_PATH.exists():
        return False, ""
    now = datetime.now(timezone.utc)
    for line in RUNTIME_STATE_PATH.read_text().splitlines():
        if "|" not in line:
            continue
        cols = [c.strip() for c in line.split("|")]
        cols = [c for c in cols if c]  # drop empty strings from leading/trailing |
        if len(cols) < 4:
            continue
        # Column index 1 is Runtime/Service — check for issue-specific blocks
        runtime_service = cols[1]
        if f"`wee-dev:issue-{issue_number}`" != runtime_service:
            continue
        blocked_until_str = cols[3]
        try:
            blocked_until = datetime.strptime(
                blocked_until_str, "%Y-%m-%d %H:%M UTC"
            ).replace(tzinfo=timezone.utc)
            if blocked_until > now:
                reason = cols[5] if len(cols) > 5 else "Blocked (no reason given)"
                return True, reason
        except ValueError:
            pass
    return False, ""


def is_runtime_blocked(runtime: str) -> tuple[bool, str]:
    """Check /opt/RUNTIME_STATE.md for an active block on ``runtime``.

    Returns (is_blocked, blocked_until_str).  Parses table rows of the form:
      | RS-NNN | `runtime` | description | YYYY-MM-DD HH:MM UTC | ... |
    Only column 2 (Runtime/Service) is checked — fallback text in later
    columns (which may mention other runtimes) is intentionally ignored.
    A row matches only when its ``Blocked Until`` timestamp is still in the future.
    """
    if not RUNTIME_STATE_PATH.exists():
        return False, ""
    now = datetime.now(timezone.utc)
    for line in RUNTIME_STATE_PATH.read_text().splitlines():
        if "|" not in line:
            continue
        cols = [c.strip() for c in line.split("|")]
        cols = [c for c in cols if c]  # drop empty strings from leading/trailing |
        if len(cols) < 4:
            continue
        # Column index 1 is Runtime/Service — only match here, not in fallback text
        if cols[1] != f"`{runtime}`":
            continue
        blocked_until_str = cols[3]
        try:
            blocked_until = datetime.strptime(
                blocked_until_str, "%Y-%m-%d %H:%M UTC"
            ).replace(tzinfo=timezone.utc)
            if blocked_until > now:
                return True, blocked_until_str
        except ValueError:
            pass
    return False, ""


# Runtime fallback priority (matches AGENTS.md)
_RUNTIME_FALLBACKS: dict[str, list[str]] = {
    "copilot": ["claude-sdk"],
    "claude-sdk": ["copilot"],
    "devin": ["claude-sdk", "copilot"],
    "openrouter/:free": ["openrouter/free", "claude-sdk"],
}


def resolve_runtime_with_fallback(configured_runtime: str) -> str:
    """Return the first non-blocked runtime from the fallback chain.

    If the configured runtime is clear, returns it unchanged.
    If blocked, walks the fallback chain from AGENTS.md.
    If every option is blocked, raises RuntimeError.
    """
    if not FORCE:
        blocked, until = is_runtime_blocked(configured_runtime)
        if blocked:
            log(
                f"Runtime '{configured_runtime}' blocked until {until}. "
                f"Trying fallbacks..."
            )
            for fallback in _RUNTIME_FALLBACKS.get(configured_runtime, []):
                fb_blocked, fb_until = is_runtime_blocked(fallback)
                if not fb_blocked:
                    log(f"Using fallback runtime '{fallback}'")
                    return fallback
                log(f"Fallback '{fallback}' also blocked until {fb_until}")
            raise RuntimeError(
                f"All runtimes for '{configured_runtime}' are blocked "
                f"(including fallbacks). Cannot dispatch."
            )
    return configured_runtime


def append_deferred_task(task_description: str, reason: str) -> None:
    """Append a task to the Deferred Tasks table in RUNTIME_STATE.md.
    
    Creates the table if it doesn't exist. Appends a row with:
      | task_description | (agent) | (runtime) | (model) | (prompt) | reason |
    
    Note: agent, runtime, model, prompt are left empty since we're deferring
    before dispatch. The heartbeat will fill these in when re-dispatching.
    """
    if not RUNTIME_STATE_PATH.exists():
        return
    
    content = RUNTIME_STATE_PATH.read_text()
    
    # Check if Deferred Tasks table exists
    if "## Deferred Tasks" not in content:
        # Add the table header
        content += "\n## Deferred Tasks\n\n"
        content += "Tasks that were intentionally NOT dispatched due to an active incident. Heartbeat re-dispatches these when the incident clears.\n\n"
        content += "| Task Description | Agent | Original Runtime | Model | Prompt Summary | Defer Reason |\n"
        content += "|-----------------|-------|-----------------|-------|---------------|-------------|"
    
    # Append the row (leave agent/runtime/model/prompt empty)
    # Use single | for consistency with standard markdown tables
    row = f"\n| {task_description} | | | | | {reason} |"
    content += row
    
    RUNTIME_STATE_PATH.write_text(content)


# ---------------------------------------------------------------------------
# Agent config helpers
# ---------------------------------------------------------------------------


def load_agents_config() -> dict:
    """Load agent configurations from agents.json."""
    if not AGENTS_CONFIG_PATH.exists():
        raise RuntimeError(
            f"agents.json not found at {AGENTS_CONFIG_PATH}"
        )
    with open(AGENTS_CONFIG_PATH) as f:
        return json.load(f)


def get_agent_dispatch_config(agent_name: str) -> dict:
    """Get dispatch configuration for a specific agent.

    Returns a dict with keys: runtime, model, vendor, permission_mode, yolo, timeout
    """
    config = load_agents_config()
    for agent in config.get("agents", []):
        if agent.get("name") == agent_name:
            dispatch_cfg = agent.get("dispatch_config", {})
            if not dispatch_cfg:
                raise RuntimeError(
                    f"No dispatch_config found for agent {agent_name!r}"
                )
            return dispatch_cfg
    raise RuntimeError(f"Agent {agent_name!r} not found in agents.json")


# ---------------------------------------------------------------------------
# GitHub helpers  (all use ``gh`` CLI which handles auth)
# ---------------------------------------------------------------------------


def gh(*args: str, check: bool = True) -> str:
    """Run a ``gh`` command and return stdout."""
    cmd = ["gh"] + list(args)
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=60
    )
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
    existing = {l["name"] for l in json.loads(existing_raw)}
    for name, colour in REQUIRED_LABELS.items():
        if name not in existing:
            if DRY_RUN:
                log(f"[dry-run] Would create label {name!r} (#{colour})")
                continue
            gh(
                "label", "create", name,
                "--repo", REPO,
                "--color", colour,
                "--description", f"wee-dev work queue status: {name}",
                "--force",
            )
            log(f"Created label {name!r}")


def fetch_github_issues() -> list[dict]:
    """Fetch open issues labelled ``wee-dev`` and return work-item dicts."""
    raw = gh(
        "issue", "list",
        "--repo", REPO,
        "--label", "wee-dev",
        "--state", "open",
        "--limit", "100",
        "--json",
        "number,title,labels,body,author,createdAt,updatedAt",
    )
    issues = json.loads(raw)
    items = [_issue_to_work_item(issue) for issue in issues]
    # Priority tier: URGENT (0) → bug (1) → enhancement (2), then FIFO by issue number
    items.sort(key=lambda x: (0 if x["is_urgent"] else 1 if x["is_bug"] else 2, x["number"]))
    return items


def _issue_to_work_item(issue: dict) -> dict:
    """Map a GitHub issue JSON object to the internal work-item dict."""
    label_names = {l["name"] for l in issue.get("labels", [])}
    status = "queued"  # default for open issues with no status label
    for label, mapped_status in STATUS_LABEL_PRIORITY:
        if label in label_names:
            status = mapped_status
            break

    is_urgent = "URGENT" in label_names
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
        "is_urgent": is_urgent,
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
        "issue", "edit", str(issue_number),
        "--repo", REPO,
        "--add-label", label,
    )


def remove_label(issue_number: int, label: str) -> None:
    if DRY_RUN:
        log(f"[dry-run] Would remove label {label!r} from #{issue_number}")
        return
    gh(
        "issue", "edit", str(issue_number),
        "--repo", REPO,
        "--remove-label", label,
        check=False,  # label may not be present — that's OK
    )


def add_comment(issue_number: int, body: str) -> None:
    if DRY_RUN:
        log(f"[dry-run] Would comment on #{issue_number}: {body[:80]}...")
        return
    gh(
        "issue", "comment", str(issue_number),
        "--repo", REPO,
        "--body", body,
    )


def close_issue(issue_number: int, comment: str) -> None:
    if DRY_RUN:
        log(f"[dry-run] Would close #{issue_number}")
        return
    gh(
        "issue", "close", str(issue_number),
        "--repo", REPO,
        "--comment", comment,
    )


def transition_status(
    issue_number: int, from_label: str | None, to_label: str, note: str
) -> None:
    """Remove ``from_label`` (if any) and add ``to_label``, plus a comment."""
    if from_label:
        remove_label(issue_number, from_label)
    add_label(issue_number, to_label)
    add_comment(issue_number, note)


def current_status_label(item: dict) -> str | None:
    """Return the wee-dev:* status label currently on ``item``, or None."""
    for label, _ in STATUS_LABEL_PRIORITY:
        if label in item["labels"]:
            return label
    return None


def normalize_status_labels(item: dict) -> dict:
    """Ensure only one wee-dev status label is present on ``item``.

    If multiple status labels are found, keep the canonical highest-priority
    label and remove the stale ones from GitHub so future cycles see a clean
    queue state.
    """
    present = [label for label in STATUS_LABELS if label in item["labels"]]
    if len(present) <= 1:
        return item

    keep = current_status_label(item)
    stale = [label for label in present if label != keep]
    log(
        f"Normalizing status labels on {item['id']}: keeping {keep}, "
        f"removing {', '.join(stale)}"
    )
    for label in stale:
        remove_label(item["number"], label)
        item["labels"].discard(label)
    return item


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
# Wee Orchestrator API helpers
# ---------------------------------------------------------------------------


def load_api_key() -> str:
    for line in ENV_PATH.read_text().splitlines():
        if line.startswith("API_SHARED_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("API_SHARED_KEY not found in /opt/n8n-copilot-shim/.env")


API_TIMEOUT_SECONDS = 10   # abort if API doesn't respond in time
MAX_QUEUED_PER_AGENT = 2   # if this many tasks are already queued, something is wrong — bail


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
    with request.urlopen(req, context=ctx, timeout=API_TIMEOUT_SECONDS) as response:
        return json.load(response)


def list_tasks() -> list[dict]:
    """Fetch all background tasks from the API. Raises on any failure."""
    api_key = load_api_key()
    data = api_request("GET", BACKGROUND_TASKS_URL, api_key)
    return data.get("tasks", [])


def has_running_agent(agent_name: str, tasks: list[dict]) -> bool:
    """Return True if ``agent_name`` has at least one active background task.

    Accepts a pre-fetched task list so we only call the API once per cycle.
    """
    return any(
        t.get("agent") == agent_name
        and t.get("status", "").lower() in RUNNING_STATUSES
        for t in tasks
    )


def queued_count(agent_name: str, tasks: list[dict]) -> int:
    """Count tasks for ``agent_name`` in a queued/pending state."""
    return sum(
        1 for t in tasks
        if t.get("agent") == agent_name
        and t.get("status", "").lower() in {"created", "queued", "pending"}
    )


def dispatch_via_api(
    agent: str,
    prompt: str,
    model: str,
    timeout: int,
    runtime: str = "copilot",
    permission_mode: str | None = None,
    yolo: bool | None = None,
) -> str:
    """Dispatch a background task via the Wee Orchestrator API.

    Returns the task_id.
    """
    api_key = load_api_key()
    payload = {
        "prompt": prompt,
        "agent": agent,
        "runtime": runtime,
        "model": model,
        "timeout": timeout,
    }
    # Add permission_mode if provided (default to "elevated" for wee-dev/wee-qa)
    if permission_mode is not None:
        payload["permission_mode"] = permission_mode
    # Add yolo if provided (default to True for wee-dev/wee-qa)
    if yolo is not None:
        payload["yolo"] = yolo
    try:
        response = api_request("POST", BACKGROUND_TASKS_URL, api_key, payload)
        task_id = response.get("task_id")
        log(f"Dispatched {agent} via API: task_id={task_id}")
        return task_id
    except Exception as exc:
        log(f"Failed to dispatch {agent} via API: {exc}")
        raise


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------


def dispatch_wee_dev(item: dict) -> str:
    """Dispatch wee-dev for ``item`` and return the task_id."""
    dev_cfg = get_agent_dispatch_config("wee-dev")
    qa_cfg = get_agent_dispatch_config("wee-qa")

    runtime = resolve_runtime_with_fallback(dev_cfg["runtime"])

    prompt = (
        f"Work on GitHub issue #{item['number']} in {REPO}: {item['title']}.\n\n"
        f"Issue body:\n{item['body'][:2000]}\n\n"
        "Read the full issue on GitHub for details. Implement the fix/feature "
        "on the dev host (192.168.1.100) in /opt/n8n-copilot-shim-dev/. "
        "Follow /opt/wee-dev/AGENTS.md. "
        "When implementation is complete:\n"
        f"  1. Change the GitHub issue label from 'wee-dev:in-progress' to 'wee-dev:qa-review':\n"
        f"     gh issue edit {item['number']} --repo {REPO} "
        f"--remove-label 'wee-dev:in-progress' --add-label 'wee-dev:qa-review'\n"
        f"  2. Dispatch wee-qa for review via the background task API using EXACTLY these parameters:\n"
        f"     agent=wee-qa, runtime={qa_cfg['runtime']!r}, model={qa_cfg['model']!r}, "
        f"timeout={qa_cfg['timeout']}, permission_mode=elevated, yolo=true\n"
        f"     Include in your wee-qa prompt: 'This code was written by "
        f"{dev_cfg['model']} ({dev_cfg['vendor']}). You are reviewing as "
        f"{qa_cfg['vendor']} — apply cross-vendor adversarial scrutiny.'\n"
        f"  3. Comment on issue #{item['number']} that implementation is complete "
        f"and QA has been dispatched.\n"
        "Do not work on more than this one issue."
    )
    if DRY_RUN:
        log(f"[dry-run] Would dispatch wee-dev for {item['id']}: {item['title']} (runtime={runtime})")
        return "dry-run-task"
    return dispatch_via_api(
        "wee-dev",
        prompt,
        dev_cfg["model"],
        dev_cfg["timeout"],
        runtime=runtime,
        permission_mode=dev_cfg.get("permission_mode"),
        yolo=dev_cfg.get("yolo"),
    )


def dispatch_wee_qa(item: dict) -> str:
    """Dispatch wee-qa for ``item`` and return the task_id.

    Intentionally uses a different model vendor than wee-dev
    so the reviewer has no shared blind spots with the author.
    """
    dev_cfg = get_agent_dispatch_config("wee-dev")
    qa_cfg = get_agent_dispatch_config("wee-qa")

    runtime = resolve_runtime_with_fallback(qa_cfg["runtime"])

    prompt = (
        f"QA review for GitHub issue #{item['number']} in {REPO}: "
        f"{item['title']}.\n\n"
        f"⚠️ CROSS-VENDOR REVIEW: This code was written by {dev_cfg['model']} "
        f"({dev_cfg['vendor']}). You are {qa_cfg['model']} ({qa_cfg['vendor']}). "
        f"Apply your own independent judgment — do NOT give the benefit of the "
        f"doubt just because the code compiles or tests pass. Look for logic "
        f"errors, edge cases, and patterns that {dev_cfg['vendor']} models commonly "
        f"miss.\n\n"
        "Changes are on the dev host (192.168.1.100) in /opt/n8n-copilot-shim-dev/ "
        f"on branch issue/{item['number']}. "
        "Run tests, check code quality, verify the implementation matches the "
        "issue requirements. Follow /opt/wee-qa/AGENTS.md.\n\n"
        "If QA PASSES:\n"
        f"  1. On the dev host (192.168.1.100), open a PR from "
        f"issue/{item['number']} to dev:\n"
        f"     ssh root@192.168.1.100 \"cd /opt/n8n-copilot-shim-dev && "
        f"gh pr create --base dev --head issue/{item['number']} "
        f"--repo {REPO} "
        f"--title \\\"Fix #{item['number']}: {item['title']}\\\" "
        f"--body \\\"Closes #{item['number']}\\\"\"\n"
        "  2. Merge the PR (squash or merge commit):\n"
        f"     ssh root@192.168.1.100 \"gh pr merge --merge --delete-branch "
        f"--repo {REPO} issue/{item['number']}\"\n"
        f"  3. Close the GitHub issue:\n"
        f"     gh issue close {item['number']} --repo {REPO} "
        f"--comment '✅ QA approved. Merged to dev.'\n"
        f"  4. Comment on issue #{item['number']} with the merge commit SHA "
        f"and PR number.\n\n"
        "If QA FAILS:\n"
        f"  1. Add label 'wee-dev:qa-failed' to issue #{item['number']}:\n"
        f"     gh issue edit {item['number']} --repo {REPO} "
        f"--remove-label 'wee-dev:qa-review' --add-label 'wee-dev:qa-failed'\n"
        f"  2. Comment on the issue with a clear list of failures and what "
        f"wee-dev must fix.\n"
        f"  3. Immediately hand the issue back to wee-dev for another pass. "
        f"Do not leave the work sitting in qa-review after a REJECT. "
        f"Dispatch wee-dev via the background task API using EXACTLY these parameters:\n"
        f"     agent=wee-dev, runtime={dev_cfg['runtime']!r}, model={dev_cfg['model']!r}, "
        f"timeout={dev_cfg['timeout']}, permission_mode=elevated, yolo=true\n"
        f"     Do NOT use 'copilot' unless {dev_cfg['runtime']!r} is explicitly blocked in "
        f"/opt/RUNTIME_STATE.md. Use the exact runtime and model above.\n"
    )
    if DRY_RUN:
        log(f"[dry-run] Would dispatch wee-qa for {item['id']}: {item['title']} (runtime={runtime})")
        return "dry-run-task"
    return dispatch_via_api(
        "wee-qa",
        prompt,
        qa_cfg["model"],
        qa_cfg["timeout"],
        runtime=runtime,
        permission_mode=qa_cfg.get("permission_mode"),
        yolo=qa_cfg.get("yolo"),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def first_with_status(
    items: list[dict], statuses: set[str]
) -> dict | None:
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
        help="Ignore runtime/issue blocks and force dispatch (USE WITH CAUTION)",
    )
    parser.add_argument(
        "--skip-blocked",
        action="store_true",
        help="Skip blocked issues and continue to next queued issue (do not defer)",
    )
    args = parser.parse_args()

    global DRY_RUN, FORCE, SKIP_BLOCKED
    DRY_RUN = args.dry_run
    FORCE = args.force
    SKIP_BLOCKED = args.skip_blocked
    if DRY_RUN:
        log("=== DRY RUN MODE ===")
    if FORCE:
        log("=== FORCE MODE: ignoring runtime/issue blocks ===")
    if SKIP_BLOCKED:
        log("=== SKIP-BLOCKED MODE: blocked issues will be skipped ===")

    # 1. Ensure labels exist
    log("Ensuring required GitHub labels exist...")
    ensure_labels()

    # 2. Fetch live task list once — all subsequent checks use this snapshot.
    #    If the API is unhealthy (slow/timeout), abort immediately rather than
    #    risk dispatching duplicate tasks into an already-overloaded system.
    log("Fetching live task list from API...")
    try:
        live_tasks = list_tasks()
    except Exception as exc:
        log(f"ABORT: API unavailable ({exc}). Not dispatching anything this cycle.")
        return 1

    log(f"API healthy — {len(live_tasks)} tasks in store.")

    # Safety valve: if too many tasks are already queued for any agent, something
    # is wrong. Bail out rather than add fuel to the fire.
    for agent_name in ("wee-dev", "wee-qa"):
        n = queued_count(agent_name, live_tasks)
        if n >= MAX_QUEUED_PER_AGENT:
            log(
                f"ABORT: {n} tasks already queued for {agent_name} "
                f"(limit={MAX_QUEUED_PER_AGENT}). Possible runaway dispatch — "
                f"not dispatching this cycle."
            )
            return 1

    # 3. Fetch issues from GitHub (GitHub is the only source of truth)
    log(f"Fetching open issues labelled 'wee-dev' from {REPO}...")
    items = fetch_github_issues()
    items = [normalize_status_labels(item) for item in items]
    log(f"Found {len(items)} open wee-dev issue(s)")

    if not items:
        log("No open wee-dev issues. Queue is empty.")
        return 0

    # 4. Handle active items before dequeuing anything new.
    active_items = [item for item in items if item["status"] in ACTIVE_STATUSES]

    for active_item in active_items:
        status = active_item["status"]

        if status == "qa-review":
            if has_running_agent("wee-qa", live_tasks):
                log(f"wee-qa is actively reviewing {active_item['id']} — no action.")
                return 0
            log(
                f"Stalled qa-review on {active_item['id']} "
                f"— re-dispatching wee-qa"
            )
            dispatch_wee_qa(active_item)
            return 0

        if status in ("in-progress", "qa-failed"):
            if has_running_agent("wee-dev", live_tasks):
                log(f"wee-dev is actively working on {active_item['id']} — no action.")
                return 0

            issue_num = active_item["number"]
            is_blocked, reason = is_issue_blocked(issue_num)
            if is_blocked:
                log(f"Issue #{issue_num} is blocked: {reason}")
                if FORCE:
                    log(
                        f"FORCE enabled: overriding block and re-dispatching "
                        f"{active_item['id']}"
                    )
                    dispatch_wee_dev(active_item)
                    return 0
                if SKIP_BLOCKED:
                    log(
                        f"Skipping blocked active item {active_item['id']} due to "
                        f"--skip-blocked"
                    )
                    continue
                log("Deferring re-dispatch — will retry when block expires.")
                append_deferred_task(
                    f"Issue #{issue_num}: {active_item['title']}",
                    reason,
                )
                continue

            reason = (
                "Stalled in-progress"
                if status == "in-progress"
                else "QA failed, rework needed"
            )
            log(f"{reason} on {active_item['id']} — re-dispatching wee-dev")
            dispatch_wee_dev(active_item)
            return 0

    # 4. No active item — pick up the next queued issue (FIFO)
    queued = [i for i in items if i["status"] == "queued"]
    if not queued:
        log("No queued issues. Nothing to dispatch.")
        return 0

    # Walk the queue until we find an approved item
    next_item = None
    for candidate in queued:
        if passes_safety_gate(candidate):
            next_item = candidate
            break

    if not next_item:
        log("No approved queued issues. Nothing to dispatch.")
        return 0

    log(f"Picking up: {next_item['id']} — {next_item['title']}")

    # Check if this specific issue is blocked in RUNTIME_STATE.md
    issue_num = next_item["number"]
    is_blocked, reason = is_issue_blocked(issue_num)
    if is_blocked:
        log(f"Issue #{issue_num} is blocked: {reason}")
        if FORCE:
            log(f"FORCE enabled: overriding block and dispatching {next_item['id']}")
            # proceed to dispatch despite the block
        elif SKIP_BLOCKED:
            log(f"Skipping blocked queued item {next_item['id']} due to --skip-blocked")
            # Try to find another approved queued item
            found = False
            for candidate in queued:
                if candidate["number"] == next_item["number"]:
                    continue
                if passes_safety_gate(candidate):
                    issue_num = candidate["number"]
                    reason = None
                    next_item = candidate
                    found = True
                    break
            if not found:
                log("No other approved queued items available.")
                return 0
        else:
            log(f"Deferring dispatch — will retry when block expires.")
            # Write deferred task to RUNTIME_STATE.md
            append_deferred_task(
                f"Issue #{issue_num}: {next_item['title']}",
                reason,
            )
            return 0

    # Transition label: queued → in-progress
    from_label = current_status_label(next_item)
    transition_status(
        next_item["number"],
        from_label,
        "wee-dev:in-progress",
        f"🚀 wee-dev is picking up this issue ({now_iso()}).",
    )

    try:
        dispatch_wee_dev(next_item)
    except Exception as exc:
        log(f"Dispatch failed: {exc} — reverting label to queued")
        # Roll back: in-progress → queued so the next cycle retries cleanly
        transition_status(
            next_item["number"],
            "wee-dev:in-progress",
            from_label or "wee-dev:queued",
            f"⚠️ Dispatch failed ({exc}). Reverted to queued — will retry next cycle.",
        )
        return 1

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"dispatch_wee_dev_work_queue failed: {exc}")
        raise
