#!/usr/bin/env python3
"""Dispatch wee-qa work queue items from GitHub Issues tagged for QA review.

Picks up issues labelled ``wee-dev:qa-review`` from leprachuan/Wee-Orchestrator
and dispatches wee-qa as background tasks via the Wee Orchestrator API.

This script decouples wee-qa dispatching from wee-dev, allowing both to run
independently on a scheduled dispatcher cycle.
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
ENV_PATH = Path("/opt/n8n-copilot-shim/.env")
BACKGROUND_TASKS_URL = "https://127.0.0.1:8000/api/v1/background-tasks"
USER_IDENTITY = "8193231291"
AUTH_CHANNEL = "telegram"

AGENT_MANAGER_PATH = Path("/opt/n8n-copilot-shim/agent_manager.py")
AGENTS_CONFIG_PATH = Path("/opt/n8n-copilot-shim/agents.json")

# Labels for QA
QA_REVIEW_LABEL = "wee-dev:qa-review"
QA_APPROVED_LABEL = "wee-dev:approved"
QA_FAILED_LABEL = "wee-dev:qa-failed"

DRY_RUN = False

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(f"[{now_iso()}] {msg}")


# ---------------------------------------------------------------------------
# GitHub helpers
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


def fetch_github_issues_for_qa() -> list[dict]:
    """Fetch open issues with qa-review label from GitHub."""
    query_filter = f"repo:{REPO} label:{QA_REVIEW_LABEL} state:open"
    raw = gh(
        "issue",
        "list",
        "--repo", REPO,
        "--label", QA_REVIEW_LABEL,
        "--state", "open",
        "--limit", "100",
        "--json", "number,title,labels,body",
    )
    
    if not raw:
        return []
    
    items = json.loads(raw)
    result = []
    for item in items:
        result.append({
            "number": item["number"],
            "id": f"#{item['number']}",
            "title": item["title"],
            "labels": [label["name"] for label in item.get("labels", [])],
            "body": item.get("body", ""),
        })
    
    return result


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_agents_config() -> dict:
    """Load agents.json configuration."""
    with open(AGENTS_CONFIG_PATH) as f:
        return json.load(f)


def get_agent_dispatch_config(agent_name: str) -> dict:
    """Get dispatch config for an agent from agents.json."""
    agents_config = load_agents_config()
    agents = agents_config.get("agents", [])
    
    for agent in agents:
        if agent.get("name") == agent_name:
            return {
                "runtime": agent.get("primary_runtime", "copilot"),
                "model": agent.get("primary_model", "auto"),
                "fallback_runtime": agent.get("fallback_runtime", "copilot"),
                "fallback_model": agent.get("fallback_model", "auto"),
                "timeout": 3600,
                "permission_mode": agent.get("permissions", {}).get("mode", "restricted"),
                "yolo": agent_name in ("wee-dev", "wee-qa"),
            }
    
    raise ValueError(f"Agent {agent_name} not found in agents.json")


# ---------------------------------------------------------------------------
# API dispatch
# ---------------------------------------------------------------------------


def dispatch_via_api(
    agent_name: str,
    prompt: str,
    model: str,
    timeout: int,
    runtime: str = "copilot",
    permission_mode: str = "restricted",
    yolo: bool = False,
) -> str:
    """Dispatch a task via the background-tasks API."""
    payload = {
        "prompt": prompt,
        "agent": agent_name,
        "runtime": runtime,
        "model": model,
        "timeout": timeout,
        "yolo": yolo,
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_get_bearer_token()}",
        "X-User-Identity": USER_IDENTITY,
        "X-Auth-Channel": AUTH_CHANNEL,
    }
    
    req = request.Request(
        BACKGROUND_TASKS_URL,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    with request.urlopen(req, context=ctx) as resp:
        response_data = json.loads(resp.read())
        return response_data.get("task_id", "")


def _get_bearer_token() -> str:
    """Get bearer token from .env."""
    if ENV_PATH.exists():
        with open(ENV_PATH) as f:
            for line in f:
                if line.startswith("BEARER_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"')
    return "shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU"


# ---------------------------------------------------------------------------
# Label management
# ---------------------------------------------------------------------------


def add_label(issue_number: int, label: str) -> None:
    """Add a label to an issue."""
    if DRY_RUN:
        log(f"[dry-run] Would add label '{label}' to issue #{issue_number}")
        return
    
    gh("issue", "edit", "--repo", REPO, str(issue_number), "--add-label", label)


def remove_label(issue_number: int, label: str) -> None:
    """Remove a label from an issue."""
    if DRY_RUN:
        log(f"[dry-run] Would remove label '{label}' from issue #{issue_number}")
        return
    
    gh("issue", "edit", "--repo", REPO, str(issue_number), "--remove-label", label)


def comment_issue(issue_number: int, comment: str) -> None:
    """Add a comment to an issue."""
    if DRY_RUN:
        log(f"[dry-run] Would comment on issue #{issue_number}")
        return
    
    gh("issue", "comment", "--repo", REPO, str(issue_number), "-b", comment)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dispatch wee-qa work from GitHub Issues"
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
    
    # Fetch issues ready for QA review
    log(f"Fetching open issues labelled '{QA_REVIEW_LABEL}' from {REPO}...")
    items = fetch_github_issues_for_qa()
    
    if not items:
        log("No issues awaiting QA review. Nothing to dispatch.")
        return 0
    
    log(f"Found {len(items)} issue(s) ready for QA review")
    
    # Dispatch only the first item (one at a time, wee-qa runs serially)
    next_item = items[0]
    log(f"Dispatching wee-qa for: {next_item['id']} — {next_item['title']}")
    
    qa_cfg = get_agent_dispatch_config("wee-qa")
    
    prompt = (
        f"QA review for GitHub issue #{next_item['number']} in {REPO}: "
        f"{next_item['title']}. "
        "Changes are on dev host 192.168.1.100 in /opt/n8n-copilot-shim-dev/. "
        "Run tests, check code quality, verify the implementation matches the "
        "issue requirements. If QA passes, add label wee-dev:approved and "
        "close the issue with the commit SHA. If QA fails, add label "
        "wee-dev:qa-failed and comment with the failures."
    )
    
    if DRY_RUN:
        log(f"[dry-run] Would dispatch wee-qa (runtime={qa_cfg['runtime']}, model={qa_cfg['model']})")
        return 0
    
    try:
        task_id = dispatch_via_api(
            "wee-qa",
            prompt,
            qa_cfg["model"],
            qa_cfg["timeout"],
            runtime=qa_cfg["runtime"],
            permission_mode=qa_cfg.get("permission_mode"),
            yolo=qa_cfg.get("yolo"),
        )
        log(f"Dispatched wee-qa task_id={task_id} for issue #{next_item['number']}")
        
        # Remove the qa-review label (it's being actively worked on now)
        remove_label(next_item["number"], QA_REVIEW_LABEL)
        
        return 0
    except Exception as e:
        log(f"ERROR: Failed to dispatch wee-qa: {e}")
        return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"dispatch_wee_qa_work_queue failed: {exc}")
        raise
