#!/usr/bin/env python3
"""Unified wee-dev/wee-qa pipeline dispatcher.

Replaces dispatch_wee_dev_work_queue.py + dispatch_wee_qa_work_queue.py.

Design
------
GitHub labels are the ONLY source of truth for issue state.  This script is
the ONLY thing that modifies labels and dispatches agents.  Agents signal
completion via comments; they do NOT update labels.

Label state machine
-------------------
  QUEUED     : wee-dev label, no status label
  IN-PROGRESS: wee-dev:in-progress
  QA-REVIEW  : wee-dev:qa-review (wee-dev finished; no QA task yet)
  QA-RUNNING : wee-dev:qa-running (wee-qa task dispatched)
  QA-FAILED  : wee-dev:qa-failed (wee-qa rejected; needs rework)
  DONE       : issue closed + wee-dev:approved

Transitions (all made by this script, never by agents)
-------------------------------------------------------
  QUEUED       → IN-PROGRESS : dispatcher dispatches wee-dev
  IN-PROGRESS  → QA-REVIEW   : wee-dev completed + qa-review label present
  IN-PROGRESS  → QUEUED      : wee-dev task failed/timed-out (remove label)
  QA-REVIEW    → QA-RUNNING  : dispatcher dispatches wee-qa
  QA-RUNNING   → DONE        : wee-qa output contains VERDICT: APPROVE
  QA-RUNNING   → QA-FAILED   : wee-qa output contains VERDICT: REJECT
  QA-FAILED    → IN-PROGRESS : dispatcher re-dispatches wee-dev

Task tracking
-------------
/opt/wee-dev/pipeline_state.json maps issue_number (str) →
  {wee_dev_task_id, wee_qa_task_id, dispatched_at, ...}

This is a local cache only.  Labels remain authoritative.  If pipeline_state
is lost the dispatcher degrades gracefully: it re-dispatches on the next cycle
unless a task is still running.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib import request

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO = "leprachuan/Wee-Orchestrator"
OWNER_LOGIN = "leprachuan"

ENV_PATH = Path("/opt/n8n-copilot-shim/.env")
AGENTS_CONFIG_PATH = Path("/opt/n8n-copilot-shim/agents.json")
PIPELINE_STATE_PATH = Path("/opt/wee-dev/pipeline_state.json")

BACKGROUND_TASKS_URL = "https://127.0.0.1:8000/api/v1/background-tasks"
USER_IDENTITY = "8193231291"
AUTH_CHANNEL = "telegram"

# Labels
L_WEE_DEV = "wee-dev"
L_IN_PROGRESS = "wee-dev:in-progress"
L_QA_REVIEW = "wee-dev:qa-review"
L_QA_RUNNING = "wee-dev:qa-running"
L_QA_FAILED = "wee-dev:qa-failed"
L_APPROVED = "wee-dev:approved"
L_NEEDS_APPROVAL = "wee-dev:needs-approval"

# All status labels (exactly one should be present at a time, or none = queued)
STATUS_LABELS = {L_IN_PROGRESS, L_QA_REVIEW, L_QA_RUNNING, L_QA_FAILED, L_APPROVED}

REQUIRED_LABELS: dict[str, str] = {
    L_WEE_DEV: "0075ca",
    L_IN_PROGRESS: "e4e669",
    L_QA_REVIEW: "d93f0b",
    L_QA_RUNNING: "fbca04",
    L_QA_FAILED: "e11d48",
    L_APPROVED: "0e8a16",
    L_NEEDS_APPROVAL: "f9a825",
}

# Task statuses that mean the task is still active
RUNNING_STATUSES = {"created", "queued", "pending", "running", "in_progress"}

DRY_RUN = False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(f"[{now_iso()}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# GitHub helpers (all via `gh` CLI which handles auth)
# ---------------------------------------------------------------------------


def gh(*args: str, check: bool = True) -> str:
    cmd = ["gh"] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if check and r.returncode != 0:
        raise RuntimeError(
            f"gh failed ({r.returncode}): {' '.join(cmd)}\n{r.stderr.strip()}"
        )
    return r.stdout.strip()


def ensure_labels() -> None:
    existing_raw = gh("label", "list", "--repo", REPO, "--limit", "200", "--json", "name")
    existing = {lbl["name"] for lbl in json.loads(existing_raw)}
    for name, colour in REQUIRED_LABELS.items():
        if name not in existing:
            if DRY_RUN:
                log(f"[dry-run] Would create label {name!r}")
                continue
            gh(
                "label", "create", name, "--repo", REPO,
                "--color", colour, "--description", f"wee-dev pipeline: {name}",
                "--force",
            )
            log(f"Created label {name!r}")


def fetch_issues() -> list[dict]:
    """Fetch all open wee-dev issues, sorted oldest-first."""
    raw = gh(
        "issue", "list", "--repo", REPO,
        "--label", L_WEE_DEV,
        "--state", "open",
        "--limit", "100",
        "--json", "number,title,labels,body,author,createdAt",
    )
    issues = json.loads(raw)
    result = []
    for issue in issues:
        label_names = {lbl["name"] for lbl in issue.get("labels", [])}
        status_labels = label_names & STATUS_LABELS
        # Determine canonical status
        if L_APPROVED in label_names:
            status = "approved"
        elif L_QA_RUNNING in label_names:
            status = "qa-running"
        elif L_QA_FAILED in label_names:
            status = "qa-failed"
        elif L_QA_REVIEW in label_names:
            status = "qa-review"
        elif L_IN_PROGRESS in label_names:
            status = "in-progress"
        else:
            status = "queued"

        result.append({
            "number": issue["number"],
            "id": f"#{issue['number']}",
            "title": issue["title"],
            "body": issue.get("body", "") or "",
            "labels": label_names,
            "status": status,
            "user_login": (issue.get("author") or {}).get("login", ""),
            "created_at": issue.get("createdAt", ""),
        })
    result.sort(key=lambda x: x["number"])
    return result


def add_label(number: int, label: str) -> None:
    if DRY_RUN:
        log(f"  [dry-run] add label {label!r} → #{number}")
        return
    gh("issue", "edit", str(number), "--repo", REPO, "--add-label", label)


def remove_label(number: int, label: str) -> None:
    if DRY_RUN:
        log(f"  [dry-run] remove label {label!r} → #{number}")
        return
    gh("issue", "edit", str(number), "--repo", REPO, "--remove-label", label, check=False)


def set_status_label(number: int, new_label: Optional[str], old_labels: set[str]) -> None:
    """Atomically swap status labels: remove all status labels except new_label, then add new_label."""
    for lbl in old_labels & STATUS_LABELS:
        if lbl != new_label:
            remove_label(number, lbl)
    if new_label:
        add_label(number, new_label)


def comment(number: int, body: str) -> None:
    if DRY_RUN:
        log(f"  [dry-run] comment on #{number}: {body[:60]}...")
        return
    gh("issue", "comment", str(number), "--repo", REPO, "--body", body)


def close_issue(number: int, msg: str) -> None:
    if DRY_RUN:
        log(f"  [dry-run] close #{number}")
        return
    gh("issue", "close", str(number), "--repo", REPO, "--comment", msg)


def passes_safety_gate(issue: dict) -> bool:
    """Only dispatch issues filed by the owner or explicitly approved."""
    if L_APPROVED in issue["labels"] or "wee-dev:approved" in issue["labels"]:
        return True
    if issue["user_login"] == OWNER_LOGIN:
        return True
    if "wee-dev:needs-approval" not in issue["labels"]:
        add_label(issue["number"], L_NEEDS_APPROVAL)
        comment(
            issue["number"],
            f"⏳ This issue was filed by @{issue['user_login']} and requires "
            f"approval from @{OWNER_LOGIN} before wee-dev picks it up. "
            "Add the `wee-dev:approved` label to approve.",
        )
    return False


# ---------------------------------------------------------------------------
# Wee Orchestrator API helpers
# ---------------------------------------------------------------------------


def load_api_key() -> str:
    key = "shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU"
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if line.startswith("API_SHARED_KEY="):
                val = line.split("=", 1)[1].strip()
                key = val if val.startswith("shared_") else f"shared_{val}"
                break
    return key


def api_post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {load_api_key()}",
        "X-User-Identity": USER_IDENTITY,
        "X-Auth-Channel": AUTH_CHANNEL,
    }
    req = request.Request(
        f"https://127.0.0.1:8000{path}", data=data, headers=headers, method="POST"
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with request.urlopen(req, context=ctx, timeout=30) as resp:
        return json.loads(resp.read())


def api_get(path: str) -> dict:
    headers = {"Authorization": f"Bearer {load_api_key()}"}
    req = request.Request(f"https://127.0.0.1:8000{path}", headers=headers)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with request.urlopen(req, context=ctx, timeout=30) as resp:
        return json.loads(resp.read())


def get_task_status(task_id: str) -> Optional[dict]:
    """Return task dict or None if not found / API error."""
    try:
        return api_get(f"/api/v1/background-tasks/{task_id}")
    except Exception as exc:
        log(f"  Could not fetch task {task_id}: {exc}")
        return None


def is_task_running(task_id: Optional[str]) -> bool:
    if not task_id:
        return False
    info = get_task_status(task_id)
    if not info:
        return False
    return info.get("status") in RUNNING_STATUSES


def get_task_output(task_id: str) -> str:
    """Return concatenated recent_output from a completed task."""
    info = get_task_status(task_id)
    if not info:
        return ""
    lines = info.get("recent_output") or []
    return "\n".join(lines)


def get_agent_config(agent_name: str) -> dict:
    cfg = json.loads(AGENTS_CONFIG_PATH.read_text())
    agent = next((a for a in cfg.get("agents", []) if a.get("name") == agent_name), None)
    if not agent:
        raise RuntimeError(f"Agent {agent_name!r} not found in agents.json")
    default_perm = "elevated" if agent_name in ("wee-dev", "wee-qa") else "restricted"
    return {
        "runtime": agent.get("primary_runtime", "copilot"),
        "model": agent.get("primary_model", "auto"),
        "fallback_runtime": agent.get("fallback_runtime"),
        "fallback_model": agent.get("fallback_model"),
        "permission_mode": (
            agent.get("permission_mode")
            or agent.get("permissions", {}).get("mode")
            or default_perm
        ),
        "yolo": bool(agent.get("yolo", agent_name in ("wee-dev", "wee-qa"))),
        "timeout": 3600,
    }


def dispatch_agent(agent_name: str, prompt: str) -> str:
    """Dispatch a background task and return task_id."""
    cfg = get_agent_config(agent_name)

    # Guard against invalid runtime+model combos (e.g., runtime='claude' with OpenAI/GPT models).
    try:
        runtime = str(cfg.get("runtime") or "").lower()
        model = str(cfg.get("model") or "")
    except Exception:
        runtime = ""
        model = ""

    if ("gpt-" in model.lower() or model.lower().startswith("gpt") or "gemini" in model.lower() or "llama" in model.lower() or "o1" in model.lower() or "o3" in model.lower()) and runtime == "claude":
        fb_rt = cfg.get("fallback_runtime")
        fb_m = cfg.get("fallback_model")
        if fb_rt and fb_rt != runtime:
            log(
                f"Detected incompatible runtime/model for agent {agent_name}: {runtime}/{model}."
                f" Using fallback {fb_rt}/{fb_m or model} instead."
            )
            cfg["runtime"] = fb_rt
            if fb_m:
                cfg["model"] = fb_m
        else:
            # No explicit fallback; switch to copilot to avoid queuing impossible combo
            log(
                f"Detected incompatible runtime/model for agent {agent_name}: {runtime}/{model}."
                " No fallback configured — switching runtime to 'copilot'."
            )
            cfg["runtime"] = "copilot"

    if DRY_RUN:
        log(f"  [dry-run] Would dispatch {agent_name} ({cfg['runtime']}/{cfg['model']})")
        return "dry-run-task-id"

    result = api_post("/api/v1/background-tasks", {
        "prompt": prompt,
        "agent": agent_name,
        "runtime": cfg["runtime"],
        "model": cfg["model"],
        "timeout": cfg["timeout"],
        "yolo": cfg["yolo"],
        "permission_mode": cfg["permission_mode"],
    })
    return result["task_id"]


# ---------------------------------------------------------------------------
# Pipeline state  (local cache for task IDs — labels are authoritative)
# ---------------------------------------------------------------------------


def load_state() -> dict:
    if PIPELINE_STATE_PATH.exists():
        try:
            return json.loads(PIPELINE_STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: dict) -> None:
    if DRY_RUN:
        return
    PIPELINE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PIPELINE_STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def state_for(state: dict, issue_number: int) -> dict:
    return state.get(str(issue_number), {})


def set_task(state: dict, issue_number: int, agent: str, task_id: str) -> None:
    entry = state.setdefault(str(issue_number), {})
    if agent == "wee-dev":
        entry["wee_dev_task_id"] = task_id
        entry["wee_dev_dispatched_at"] = now_iso()
    elif agent == "wee-qa":
        entry["wee_qa_task_id"] = task_id
        entry["wee_qa_dispatched_at"] = now_iso()
    entry["last_updated"] = now_iso()


# ---------------------------------------------------------------------------
# Verdict parsing
# ---------------------------------------------------------------------------


def parse_verdict(output: str) -> Optional[str]:
    """Return 'APPROVE', 'REJECT', or None if no verdict found."""
    for line in output.splitlines():
        m = re.search(r"\bVERDICT\s*:\s*(APPROVE|REJECT)\b", line, re.IGNORECASE)
        if m:
            return m.group(1).upper()
    return None


# ---------------------------------------------------------------------------
# State handlers
# ---------------------------------------------------------------------------


def handle_queued(issue: dict, state: dict, active_dev: bool) -> bool:
    """Dispatch wee-dev for a queued issue if no other issue is in-progress."""
    if active_dev:
        log(f"  Skipping {issue['id']} (another issue is in-progress)")
        return False
    if not passes_safety_gate(issue):
        log(f"  Skipping {issue['id']} — needs owner approval")
        return False

    log(f"  → Dispatching wee-dev for {issue['id']}: {issue['title'][:60]}")
    prompt = (
        f"Work on GitHub issue #{issue['number']} in {REPO}: {issue['title']}.\n\n"
        f"Issue body:\n{issue['body'][:2000]}\n\n"
        "Read the full issue on GitHub for details. Implement the fix/feature "
        "on the dev host (192.168.1.100) in /opt/n8n-copilot-shim-dev/. "
        "Follow /opt/wee-dev/AGENTS.md.\n\n"
        "When your implementation is complete:\n"
        "1. Open a PR: issue/<number> → dev\n"
        "2. Add the label 'wee-dev:qa-review' to this GitHub issue\n"
        "3. Post a comment summarising what you did\n\n"
        "DO NOT dispatch wee-qa yourself — the pipeline dispatcher will detect "
        "the qa-review label and handle that automatically.\n\n"
        "Work on this one issue only."
    )
    task_id = dispatch_agent("wee-dev", prompt)
    set_task(state, issue["number"], "wee-dev", task_id)
    set_status_label(issue["number"], L_IN_PROGRESS, issue["labels"])
    comment(
        issue["number"],
        f"🚀 wee-dev dispatched by pipeline dispatcher (task `{task_id}`).",
    )
    log(f"  Dispatched wee-dev task_id={task_id}")
    return True  # signal: an in-progress slot is now occupied


def handle_in_progress(issue: dict, state: dict) -> str:
    """
    Check whether the in-progress wee-dev task is still running.
    Returns 'running', 'moved-to-qa', or 'reset'.
    """
    s = state_for(state, issue["number"])
    task_id = s.get("wee_dev_task_id")

    if is_task_running(task_id):
        log(f"  {issue['id']} — wee-dev task {task_id} still running, skipping")
        return "running"

    # Task completed (or no task_id in state — treat as unknown / possibly stale)
    if task_id:
        task_info = get_task_status(task_id)
        task_status = (task_info or {}).get("status", "unknown")
        log(f"  {issue['id']} — wee-dev task {task_id} is {task_status}")
    else:
        log(f"  {issue['id']} — no task_id in pipeline_state; treating as stale in-progress")
        task_status = "unknown"

    # Did wee-dev add qa-review label already?
    if L_QA_REVIEW in issue["labels"]:
        # wee-dev finished and signalled readiness for QA — just clean up in-progress
        remove_label(issue["number"], L_IN_PROGRESS)
        log(f"  {issue['id']} — wee-dev done, qa-review already set → ready for QA dispatch")
        return "moved-to-qa"

    # Task failed or finished without adding qa-review — reset to queued
    if task_status in ("failed", "error", "cancelled"):
        comment(
            issue["number"],
            f"⚠️ wee-dev task `{task_id}` ended with status `{task_status}`. "
            "Resetting to queued for next dispatcher cycle.",
        )
    else:
        comment(
            issue["number"],
            f"⚠️ wee-dev task `{task_id}` completed but did not add "
            "`wee-dev:qa-review`. Resetting to queued for next cycle.",
        )
    set_status_label(issue["number"], None, issue["labels"])  # remove in-progress
    log(f"  {issue['id']} — reset to queued")
    return "reset"


def handle_qa_review(issue: dict, state: dict, active_qa: bool) -> bool:
    """Dispatch wee-qa for an issue that wee-dev has finished."""
    if active_qa:
        log(f"  {issue['id']} — another issue already in qa-running, skipping")
        return False

    log(f"  → Dispatching wee-qa for {issue['id']}: {issue['title'][:60]}")
    prompt = (
        f"QA review for GitHub issue #{issue['number']} in {REPO}: "
        f"{issue['title']}.\n\n"
        "The implementation is on dev host 192.168.1.100 in "
        "/opt/n8n-copilot-shim-dev/.\n\n"
        "Your tasks:\n"
        "1. Find the PR for this issue on the dev branch\n"
        "2. Review the code changes\n"
        "3. Run static analysis (black, isort, flake8) and tests\n"
        "4. Verify the implementation matches the issue requirements\n"
        "5. Check for regression test if this is a bug fix\n\n"
        "Post your verdict as a comment on the issue using EXACTLY this format "
        "on its own line:\n"
        "  VERDICT: APPROVE\n"
        "  or\n"
        "  VERDICT: REJECT\n\n"
        "Follow with a structured summary of your findings.\n\n"
        "DO NOT modify labels — the pipeline dispatcher will update labels "
        "based on your VERDICT comment.\n\n"
        "Notify Foster via telegram-notify (ID: 8193231291) with a one-line "
        "summary of your verdict."
    )
    task_id = dispatch_agent("wee-qa", prompt)
    set_task(state, issue["number"], "wee-qa", task_id)
    set_status_label(issue["number"], L_QA_RUNNING, issue["labels"])
    comment(
        issue["number"],
        f"🔍 wee-qa dispatched by pipeline dispatcher (task `{task_id}`).",
    )
    log(f"  Dispatched wee-qa task_id={task_id}")
    return True  # qa slot now occupied


def handle_qa_running(issue: dict, state: dict) -> str:
    """
    Check whether the wee-qa task has produced a verdict.
    Returns 'running', 'approved', or 'rejected'.
    """
    s = state_for(state, issue["number"])
    task_id = s.get("wee_qa_task_id")

    if is_task_running(task_id):
        log(f"  {issue['id']} — wee-qa task {task_id} still running, skipping")
        return "running"

    # Task done — check output for verdict
    output = get_task_output(task_id) if task_id else ""
    verdict = parse_verdict(output)

    if verdict is None:
        # Also check GitHub comments for a verdict (wee-qa may have posted it)
        verdict = _check_github_comments_for_verdict(issue["number"])

    if verdict == "APPROVE":
        set_status_label(issue["number"], L_APPROVED, issue["labels"])
        close_issue(
            issue["number"],
            f"✅ QA approved by wee-qa (task `{task_id}`). Closing issue.",
        )
        log(f"  {issue['id']} — QA APPROVED → closed")
        return "approved"
    elif verdict == "REJECT":
        set_status_label(issue["number"], L_QA_FAILED, issue["labels"])
        comment(
            issue["number"],
            f"❌ wee-qa rejected this implementation (task `{task_id}`). "
            "Flagged as `wee-dev:qa-failed` — dispatcher will re-queue on next cycle.",
        )
        log(f"  {issue['id']} — QA REJECTED → qa-failed")
        return "rejected"
    else:
        # No verdict found — wee-qa may have stalled; reset to qa-review for re-dispatch
        log(f"  {issue['id']} — no verdict found in wee-qa output; resetting to qa-review")
        set_status_label(issue["number"], L_QA_REVIEW, issue["labels"])
        comment(
            issue["number"],
            f"⚠️ wee-qa task `{task_id}` completed without a clear VERDICT. "
            "Resetting to `wee-dev:qa-review` for re-dispatch on next cycle.",
        )
        return "no-verdict"


def handle_qa_failed(issue: dict, state: dict, active_dev: bool) -> bool:
    """Re-dispatch wee-dev to fix QA failures."""
    if active_dev:
        log(f"  {issue['id']} — qa-failed but another issue is in-progress, deferring")
        return False
    if not passes_safety_gate(issue):
        return False

    log(f"  → Re-dispatching wee-dev for qa-failed {issue['id']}: {issue['title'][:50]}")
    prompt = (
        f"Fix QA failures for GitHub issue #{issue['number']} in {REPO}: "
        f"{issue['title']}.\n\n"
        f"Issue body:\n{issue['body'][:1500]}\n\n"
        "wee-qa previously rejected this implementation. Read the QA verdict "
        "comment on the GitHub issue to understand what needs to be fixed.\n\n"
        "Fix the issues on the dev host (192.168.1.100) in "
        "/opt/n8n-copilot-shim-dev/. Push to the same branch and update the PR.\n\n"
        "When fixes are complete, add the label 'wee-dev:qa-review' to the issue. "
        "DO NOT dispatch wee-qa yourself — the dispatcher handles that.\n\n"
        "Work on this one issue only."
    )
    task_id = dispatch_agent("wee-dev", prompt)
    set_task(state, issue["number"], "wee-dev", task_id)
    set_status_label(issue["number"], L_IN_PROGRESS, issue["labels"])
    comment(
        issue["number"],
        f"🔧 wee-dev re-dispatched to fix QA failures (task `{task_id}`).",
    )
    log(f"  Re-dispatched wee-dev task_id={task_id}")
    return True


def _check_github_comments_for_verdict(issue_number: int) -> Optional[str]:
    """Scan recent comments on a GitHub issue for a VERDICT line."""
    try:
        raw = gh(
            "issue", "view", str(issue_number), "--repo", REPO,
            "--json", "comments",
        )
        data = json.loads(raw)
        # Check comments newest-first
        for c in reversed(data.get("comments", [])):
            body = c.get("body", "")
            verdict = parse_verdict(body)
            if verdict:
                return verdict
    except Exception as exc:
        log(f"  Could not fetch comments for #{issue_number}: {exc}")
    return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run(dry_run: bool = False, issue_filter: Optional[int] = None) -> int:
    global DRY_RUN
    DRY_RUN = dry_run
    if DRY_RUN:
        log("=== DRY RUN ===")

    log(f"Pipeline dispatcher starting — {now_iso()}")
    ensure_labels()

    issues = fetch_issues()
    if not issues:
        log("No open wee-dev issues found.")
        return 0

    if issue_filter:
        issues = [i for i in issues if i["number"] == issue_filter]
        if not issues:
            log(f"Issue #{issue_filter} not found in wee-dev queue.")
            return 1

    log(f"Found {len(issues)} open wee-dev issue(s):")
    for i in issues:
        log(f"  #{i['number']:4d}  [{i['status']:12s}]  {i['title'][:60]}")

    state = load_state()

    # Track concurrency slots
    active_dev = any(i["status"] == "in-progress" for i in issues)
    active_qa = any(i["status"] == "qa-running" for i in issues)

    for issue in issues:
        status = issue["status"]
        log(f"\nProcessing {issue['id']} [{status}]: {issue['title'][:60]}")

        if status == "approved":
            log(f"  {issue['id']} is approved/closed — skipping")
            continue

        elif status == "in-progress":
            result = handle_in_progress(issue, state)
            if result == "running":
                active_dev = True  # still occupying the slot
            elif result in ("moved-to-qa", "reset"):
                active_dev = False  # slot freed

        elif status == "qa-review":
            dispatched = handle_qa_review(issue, state, active_qa)
            if dispatched:
                active_qa = True

        elif status == "qa-running":
            result = handle_qa_running(issue, state)
            if result == "running":
                active_qa = True
            else:
                active_qa = False  # slot freed

        elif status == "qa-failed":
            dispatched = handle_qa_failed(issue, state, active_dev)
            if dispatched:
                active_dev = True

        elif status == "queued":
            dispatched = handle_queued(issue, state, active_dev)
            if dispatched:
                active_dev = True  # slot now occupied — stop dispatching more

    save_state(state)
    log(f"\nPipeline dispatcher done — {now_iso()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="wee-dev/wee-qa pipeline dispatcher")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    parser.add_argument("--issue", type=int, help="Process a specific issue number only")
    args = parser.parse_args()
    return run(dry_run=args.dry_run, issue_filter=args.issue)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"dispatch_pipeline failed: {exc}", file=sys.stderr)
        raise
