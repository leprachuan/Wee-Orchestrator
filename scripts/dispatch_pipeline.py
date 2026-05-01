#!/usr/bin/env python3
"""Unified wee-dev/wee-qa pipeline dispatcher.

Manages the full GitHub Issues → wee-dev → wee-qa pipeline:
- GitHub labels are the sole state machine (no lock file)
- pipeline_state.json tracks running background task IDs to prevent double-dispatch
- wee-dev and wee-qa can run in parallel (for different issues)
- Dispatcher owns all label transitions

Label state machine:
  (no status label)      = queued, ready for wee-dev
  wee-dev:in-progress    = wee-dev working
  wee-dev:qa-review      = wee-dev done, ready for wee-qa
  wee-dev:qa-failed      = QA rejected, needs wee-dev fix
  wee-dev:approved       = QA passed (wee-dev merges + closes)
  wee-dev:needs-approval = filed by non-owner, awaiting approval
"""

import argparse
import json
import os
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
OWNER_LOGIN = "leprachuan"
PIPELINE_STATE_PATH = Path("/opt/wee-dev/pipeline_state.json")
ENV_PATH = Path("/opt/n8n-copilot-shim/.env")
BACKGROUND_TASKS_URL = "https://127.0.0.1:8000/api/v1/background-tasks"
AGENTS_CONFIG_PATH = Path("/opt/n8n-copilot-shim/agents.json")
USER_IDENTITY = "8193231291"
AUTH_CHANNEL = "telegram"

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
}

# Stall timeout: if in-progress/qa-review with no running task for this long, re-dispatch
STALL_TIMEOUT_MINUTES = 30

DRY_RUN = False


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(f"[{now_iso()}] {msg}")


def parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


def minutes_since(iso_str: str) -> float | None:
    t = parse_iso(iso_str)
    if t is None:
        return None
    return (datetime.now(timezone.utc) - t).total_seconds() / 60


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------


def gh(*args: str, check: bool = True) -> str:
    cmd = ["gh"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def ensure_labels() -> None:
    existing = {lbl["name"] for lbl in json.loads(
        gh("label", "list", "--repo", REPO, "--limit", "200", "--json", "name")
    )}
    for name, colour in REQUIRED_LABELS.items():
        if name not in existing:
            if DRY_RUN:
                log(f"[dry-run] Would create label {name!r}")
                continue
            gh("label", "create", name, "--repo", REPO, "--color", colour,
               "--description", f"wee pipeline: {name}", "--force")
            log(f"Created label {name!r}")


def fetch_issue_comments(issue_number: int) -> str:
    """Fetch all comments for an issue and format them."""
    try:
        raw = gh(
            "issue", "view", str(issue_number), "--repo", REPO,
            "--json", "comments",
        )
        data = json.loads(raw)
        comments = data.get("comments", [])
        if not comments:
            return ""
        
        formatted = []
        for comment in comments:
            author = comment.get("author", {}).get("login", "unknown")
            body = comment.get("body", "")
            formatted.append(f"**@{author}:**\n{body}")
        return "\n\n---\n\n".join(formatted)
    except Exception as e:
        log(f"WARNING: Failed to fetch comments for #{issue_number}: {e}")
        return ""


def fetch_issues() -> list[dict]:
    """Fetch all open issues labelled wee-dev, sorted oldest first."""
    raw = gh(
        "issue", "list", "--repo", REPO, "--label", "wee-dev",
        "--state", "open", "--limit", "100",
        "--json", "number,title,labels,body,author,createdAt",
    )
    issues = json.loads(raw)
    items = []
    for issue in issues:
        label_names = {lbl["name"] for lbl in issue.get("labels", [])}
        status = _resolve_status(label_names)
        items.append({
            "number": issue["number"],
            "id": f"#{issue['number']}",
            "title": issue["title"],
            "labels": label_names,
            "status": status,
            "body": (issue.get("body") or "")[:2000],
            "author": (issue.get("author") or {}).get("login", ""),
        })
    items.sort(key=lambda x: x["number"])
    return items


def _resolve_status(labels: set[str]) -> str:
    for label, status in [
        ("wee-dev:in-progress", "in-progress"),
        ("wee-dev:qa-review", "qa-review"),
        ("wee-dev:qa-failed", "qa-failed"),
        ("wee-dev:approved", "approved"),
        ("wee-dev:queued", "queued"),
    ]:
        if label in labels:
            return status
    return "queued"


def add_label(issue: int, label: str) -> None:
    if DRY_RUN:
        log(f"[dry-run] add label {label!r} to #{issue}")
        return
    gh("issue", "edit", str(issue), "--repo", REPO, "--add-label", label)


def remove_label(issue: int, label: str) -> None:
    if DRY_RUN:
        log(f"[dry-run] remove label {label!r} from #{issue}")
        return
    gh("issue", "edit", str(issue), "--repo", REPO, "--remove-label", label, check=False)


def add_comment(issue: int, body: str) -> None:
    if DRY_RUN:
        log(f"[dry-run] comment on #{issue}: {body[:60]}...")
        return
    gh("issue", "comment", str(issue), "--repo", REPO, "--body", body)


def transition(issue: int, from_label: str | None, to_label: str, comment: str) -> None:
    if from_label:
        remove_label(issue, from_label)
    add_label(issue, to_label)
    add_comment(issue, comment)


# ---------------------------------------------------------------------------
# Pipeline state
# ---------------------------------------------------------------------------


def load_state() -> dict:
    if PIPELINE_STATE_PATH.exists():
        try:
            return json.loads(PIPELINE_STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict) -> None:
    PIPELINE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PIPELINE_STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def get_issue_state(state: dict, issue_num: int) -> dict:
    return state.get(str(issue_num), {})


def set_issue_field(state: dict, issue_num: int, key: str, value) -> None:
    k = str(issue_num)
    if k not in state:
        state[k] = {}
    state[k][key] = value
    state[k]["last_updated"] = now_iso()


def cleanup_closed_issues(state: dict, open_numbers: set[int]) -> dict:
    """Remove entries for issues no longer open."""
    closed = [k for k in state if int(k) not in open_numbers]
    for k in closed:
        log(f"Cleaning up state for closed/missing issue #{k}")
        del state[k]
    return state


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def _load_api_key() -> str:
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if line.startswith("API_SHARED_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError(f"API_SHARED_KEY not found in {ENV_PATH}")


def get_agent_dispatch_config(agent_name: str) -> dict:
    config = json.loads(AGENTS_CONFIG_PATH.read_text())
    agent = next((a for a in config.get("agents", []) if a.get("name") == agent_name), None)
    if agent is None:
        raise RuntimeError(f"Agent {agent_name!r} not found in agents.json")

    # Legacy dispatch_config block
    if agent.get("dispatch_config"):
        cfg = dict(agent["dispatch_config"])
        if "fallback_runtime" not in cfg:
            cfg["fallback_runtime"] = agent.get("fallback_runtime")
        if "fallback_model" not in cfg:
            cfg["fallback_model"] = agent.get("fallback_model")
        return cfg

    # Canonical flat fields
    default_perm = "elevated" if agent_name in ("wee-dev", "wee-qa") else "restricted"
    permission_mode = (
        agent.get("permission_mode")
        or agent.get("permissions", {}).get("mode")
        or default_perm
    )
    yolo = agent.get("yolo")
    if yolo is None:
        yolo = agent_name in ("wee-dev", "wee-qa")
    return {
        "runtime": agent.get("primary_runtime", "copilot"),
        "model": agent.get("primary_model") or None,
        "fallback_runtime": agent.get("fallback_runtime"),
        "fallback_model": agent.get("fallback_model"),
        "permission_mode": permission_mode,
        "yolo": bool(yolo),
        "timeout": 3600,
    }


def dispatch_via_api(agent: str, prompt: str, cfg: dict) -> str:
    """Dispatch a background task and return the task_id."""
    import hashlib
    import hmac

    api_key = _load_api_key()
    body = {
        "prompt": prompt,
        "agent": agent,
        "runtime": cfg["runtime"],
        "timeout": cfg.get("timeout", 3600),
        "notify": False,
    }
    if cfg.get("model"):
        body["model"] = cfg["model"]
    if cfg.get("permission_mode"):
        body["permission_mode"] = cfg["permission_mode"]
    if cfg.get("yolo"):
        body["yolo"] = cfg["yolo"]
    if cfg.get("fallback_runtime"):
        body["fallback_runtime"] = cfg["fallback_runtime"]
    if cfg.get("fallback_model") and cfg["fallback_model"] != "auto":
        body["fallback_model"] = cfg["fallback_model"]

    payload_json = json.dumps(body, sort_keys=True)
    signature = hmac.new(
        api_key.encode(), payload_json.encode(), hashlib.sha256
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer shared_{api_key}",
        "X-User-Identity": USER_IDENTITY,
        "X-Auth-Channel": AUTH_CHANNEL,
        "X-Wee-Executor-Signature": signature,
    }
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    req = request.Request(
        BACKGROUND_TASKS_URL,
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    with request.urlopen(req, context=ssl_ctx, timeout=30) as resp:
        result = json.loads(resp.read())
        task_id = result.get("task_id", "")
        if not task_id:
            raise RuntimeError(f"No task_id in response: {result}")
        return task_id


def is_task_running(task_id: str) -> bool:
    """Return True if the background task is still active."""
    try:
        api_key = _load_api_key()
        url = f"{BACKGROUND_TASKS_URL}/{task_id}"
        headers = {"Authorization": f"Bearer shared_{api_key}"}
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        req = request.Request(url, headers=headers)
        with request.urlopen(req, context=ssl_ctx, timeout=15) as resp:
            data = json.loads(resp.read())
            return data.get("status", "").lower() in RUNNING_STATUSES
    except Exception:
        return False  # API unreachable → assume not running (fail-open for re-dispatch)


# ---------------------------------------------------------------------------
# Safety gate
# ---------------------------------------------------------------------------


def passes_safety_gate(item: dict) -> bool:
    if "wee-dev:approved" in item["labels"]:
        return True
    if item["author"] == OWNER_LOGIN:
        return True
    log(f"  {item['id']} filed by {item['author']!r} — needs approval")
    if "wee-dev:needs-approval" not in item["labels"]:
        add_label(item["number"], "wee-dev:needs-approval")
        add_comment(item["number"],
                    f"⏳ Awaiting owner approval before wee-dev picks this up. "
                    f"Filed by @{item['author']}; only @{OWNER_LOGIN} issues are auto-dispatched.")
    return False


# ---------------------------------------------------------------------------
# wee-dev dispatch
# ---------------------------------------------------------------------------


def build_wee_dev_prompt(item: dict) -> str:
    # Fetch comments if available
    comments = fetch_issue_comments(item["number"])
    comments_section = f"\n\n## Issue Comments (context from previous discussion):\n{comments}" if comments else ""
    
    return (
        f"Work on GitHub issue #{item['number']} in {REPO}: {item['title']}.\n\n"
        f"## Issue Description:\n{item['body']}"
        f"{comments_section}\n\n"
        "## Task:\n"
        "1. Read the full issue body and all comments above to understand requirements and context.\n"
        "2. Implement the fix/feature on the dev host (192.168.1.100) in /opt/n8n-copilot-shim-dev/.\n"
        "3. Follow /opt/wee-dev/AGENTS.md for all git workflow rules.\n"
        "4. Leave clear notes on this GitHub issue as you work (use /comment command or GitHub UI).\n"
        "   - **IMPORTANT: Never put secrets, API keys, passwords, or credentials in GitHub issues.**\n"
        "   - Do leave implementation notes, decisions made, test results, and commit SHAs.\n"
        "5. When implementation is complete and tests pass, add the label 'wee-dev:qa-review' to the issue.\n"
        "6. The dispatcher will pick it up for wee-qa. Do not dispatch wee-qa yourself.\n"
        "7. Do not work on more than one issue at a time."
    )


def dispatch_wee_dev(item: dict, state: dict) -> None:
    cfg = get_agent_dispatch_config("wee-dev")
    prompt = build_wee_dev_prompt(item)

    if DRY_RUN:
        log(f"[dry-run] Would dispatch wee-dev for {item['id']} (runtime={cfg['runtime']})")
        return

    task_id = dispatch_via_api("wee-dev", prompt, cfg)
    log(f"Dispatched wee-dev task_id={task_id} for {item['id']}")
    set_issue_field(state, item["number"], "wee_dev_task_id", task_id)
    set_issue_field(state, item["number"], "wee_dev_dispatched_at", now_iso())


# ---------------------------------------------------------------------------
# wee-qa dispatch
# ---------------------------------------------------------------------------


def build_wee_qa_prompt(item: dict) -> str:
    # Fetch comments if available
    comments = fetch_issue_comments(item["number"])
    comments_section = f"\n\n## Issue Discussion:\n{comments}" if comments else ""
    
    return (
        f"QA review for GitHub issue #{item['number']} in {REPO}: {item['title']}.\n\n"
        f"## Issue Description:\n{item['body']}"
        f"{comments_section}\n\n"
        "## Task:\n"
        "1. Read the full issue and discussion above to understand what was implemented.\n"
        "2. The implementation is on dev host 192.168.1.100 in /opt/n8n-copilot-shim-dev/.\n"
        "3. Run the full test suite, check code quality (flake8/black), verify requirements are met.\n"
        "4. Leave notes on this GitHub issue as you work.\n"
        "   - **IMPORTANT: Never put secrets, API keys, passwords, or credentials in GitHub issues.**\n"
        "   - Do leave test results, code quality findings, and specific pass/fail details.\n"
        "5. When QA is complete:\n"
        "   - If APPROVED: Add label 'wee-dev:approved', remove 'wee-dev:qa-review', post comment: 'VERDICT: APPROVE' + test count + summary.\n"
        "   - If REJECTED: Add label 'wee-dev:qa-failed', remove 'wee-dev:qa-review', post comment: 'VERDICT: REJECT' + specific failures.\n"
        "6. Do NOT merge or close the issue — wee-dev handles that after approval."
    )


def dispatch_wee_qa(item: dict, state: dict) -> None:
    cfg = get_agent_dispatch_config("wee-qa")
    prompt = build_wee_qa_prompt(item)

    if DRY_RUN:
        log(f"[dry-run] Would dispatch wee-qa for {item['id']} (runtime={cfg['runtime']})")
        return

    task_id = dispatch_via_api("wee-qa", prompt, cfg)
    log(f"Dispatched wee-qa task_id={task_id} for {item['id']}")
    set_issue_field(state, item["number"], "wee_qa_task_id", task_id)
    set_issue_field(state, item["number"], "wee_qa_dispatched_at", now_iso())


# ---------------------------------------------------------------------------
# Main pipeline logic
# ---------------------------------------------------------------------------


def run_pipeline(items: list[dict], state: dict) -> None:
    # -----------------------------------------------------------------------
    # Determine what is actively running vs stalled (label stuck after task done)
    # -----------------------------------------------------------------------
    
    stalled_qa = []   # qa-review items whose task completed without transitioning label
    stalled_dev = []  # in-progress items whose task completed without transitioning label

    for item in items:
        issue_state = get_issue_state(state, item["number"])

        if item["status"] == "in-progress":
            task_id = issue_state.get("wee_dev_task_id")
            dispatched_at = issue_state.get("wee_dev_dispatched_at")
            mins = minutes_since(dispatched_at) if dispatched_at else None
            if task_id and is_task_running(task_id):
                log(f"wee-dev running task={task_id} for {item['id']} — skipping dispatch")
                return  # Serial: active task running
            if mins is not None and mins < STALL_TIMEOUT_MINUTES:
                log(f"wee-dev task for {item['id']} ended {mins:.1f}min ago — waiting for label transition")
                return  # Too recent to re-dispatch
            stalled_dev.append(item)
            log(f"wee-dev task for {item['id']} stalled (completed without label transition)")

        elif item["status"] == "qa-review":
            task_id = issue_state.get("wee_qa_task_id")
            dispatched_at = issue_state.get("wee_qa_dispatched_at")
            mins = minutes_since(dispatched_at) if dispatched_at else None
            if task_id and is_task_running(task_id):
                log(f"wee-qa running task={task_id} for {item['id']} — skipping dispatch")
                return  # Serial: active task running
            if mins is not None and mins < STALL_TIMEOUT_MINUTES:
                log(f"wee-qa task for {item['id']} ended {mins:.1f}min ago — waiting for label transition")
                return  # Too recent to re-dispatch
            stalled_qa.append(item)
            log(f"wee-qa task for {item['id']} stalled (completed without label transition)")

    # -----------------------------------------------------------------------
    # Priority 1: Re-dispatch wee-qa for stalled qa-review items
    # -----------------------------------------------------------------------
    if stalled_qa:
        item = stalled_qa[0]
        log(f"Re-dispatching wee-qa for stalled {item['id']}: {item['title']}")
        try:
            dispatch_wee_qa(item, state)
        except Exception as exc:
            log(f"ERROR: Failed to re-dispatch wee-qa for {item['id']}: {exc}")
        return  # Serial: one dispatch per cycle

    # -----------------------------------------------------------------------
    # wee-dev slot: one task at a time (handles stalled, qa-failed, queued)
    # -----------------------------------------------------------------------
    in_progress = [i for i in items if i["status"] == "in-progress"]
    qa_failed = [i for i in items if i["status"] == "qa-failed"]
    queued = [i for i in items if i["status"] == "queued"]


    wee_dev_dispatched = False

    if in_progress:
        item = in_progress[0]
        issue_state = get_issue_state(state, item["number"])
        task_id = issue_state.get("wee_dev_task_id")

        if task_id and is_task_running(task_id):
            log(f"wee-dev already running task={task_id} for {item['id']} — skipping")
            wee_dev_dispatched = True
        else:
            # Not running — check stall timeout before re-dispatching
            dispatched_at = issue_state.get("wee_dev_dispatched_at")
            mins = minutes_since(dispatched_at) if dispatched_at else None
            if mins is not None and mins < STALL_TIMEOUT_MINUTES:
                log(
                    f"wee-dev task for {item['id']} ended within {mins:.1f}min — "
                    f"may have just finished; skipping re-dispatch"
                )
                wee_dev_dispatched = True
            else:
                log(f"Re-dispatching wee-dev for stalled {item['id']} (in-progress, no running task)")
                try:
                    dispatch_wee_dev(item, state)
                    wee_dev_dispatched = True
                except Exception as exc:
                    log(f"ERROR: Failed to re-dispatch wee-dev: {exc}")

    elif qa_failed:
        item = qa_failed[0]
        log(f"QA-failed: re-dispatching wee-dev for {item['id']}")
        current_label = "wee-dev:qa-failed"
        transition(item["number"], current_label, "wee-dev:in-progress",
                   f"🔄 wee-dev re-dispatched to address QA feedback ({now_iso()}).")
        try:
            dispatch_wee_dev(item, state)
            wee_dev_dispatched = True
        except Exception as exc:
            log(f"ERROR: Failed to dispatch wee-dev for qa-failed: {exc}")
            # Roll back label
            transition(item["number"], "wee-dev:in-progress", current_label,
                       "⚠️ Dispatcher failed to re-dispatch wee-dev; reverted to qa-failed.")

    elif queued:
        # Pick next queued issue (FIFO, safety gate)
        for candidate in queued:
            if not passes_safety_gate(candidate):
                continue
            log(f"Dispatching wee-dev for queued {candidate['id']}: {candidate['title']}")
            transition(candidate["number"], None, "wee-dev:in-progress",
                       f"🚀 wee-dev picking up this issue ({now_iso()}).")
            try:
                dispatch_wee_dev(candidate, state)
                wee_dev_dispatched = True
            except Exception as exc:
                log(f"ERROR: Failed to dispatch wee-dev: {exc}")
                # Roll back label
                remove_label(candidate["number"], "wee-dev:in-progress")
            break
        else:
            log("No approved queued issues to dispatch.")

    if not wee_dev_dispatched and not in_progress and not qa_failed and not queued:
        log("No wee-dev work to do.")

    # -----------------------------------------------------------------------
    # wee-qa slot: only runs if wee-dev did NOT dispatch this cycle (serial)
    # -----------------------------------------------------------------------
    if wee_dev_dispatched:
        log("wee-dev dispatched this cycle — skipping wee-qa (serial enforcement)")
        return

    qa_review = [i for i in items if i["status"] == "qa-review"]

    if not qa_review:
        log("No issues awaiting QA review.")
        return

    for item in qa_review:
        issue_state = get_issue_state(state, item["number"])
        task_id = issue_state.get("wee_qa_task_id")

        if task_id and is_task_running(task_id):
            log(f"wee-qa already running task={task_id} for {item['id']} — skipping")
            return  # Only one wee-qa at a time

        log(f"Dispatching wee-qa for {item['id']}: {item['title']}")
        try:
            dispatch_wee_qa(item, state)
        except Exception as exc:
            log(f"ERROR: Failed to dispatch wee-qa for {item['id']}: {exc}")
        return  # Only dispatch one wee-qa per cycle


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified wee-dev/wee-qa pipeline dispatcher")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    global DRY_RUN
    DRY_RUN = args.dry_run
    if DRY_RUN:
        log("=== DRY RUN MODE ===")

    log("Ensuring GitHub labels exist...")
    ensure_labels()

    log(f"Fetching open wee-dev issues from {REPO}...")
    items = fetch_issues()
    log(f"Found {len(items)} open wee-dev issue(s)")

    if not items:
        log("Queue is empty — nothing to dispatch.")
        return 0

    # Log summary
    by_status: dict[str, list] = {}
    for item in items:
        by_status.setdefault(item["status"], []).append(item["id"])
    for status, ids in sorted(by_status.items()):
        log(f"  {status}: {', '.join(ids)}")

    state = load_state()
    open_numbers = {i["number"] for i in items}
    state = cleanup_closed_issues(state, open_numbers)

    run_pipeline(items, state)

    if not DRY_RUN:
        save_state(state)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"dispatch_pipeline failed: {exc}")
        raise
