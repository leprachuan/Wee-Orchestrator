"""QA Gate for wee-dev work queue dispatch (Issue #148).

Provides a pre-dispatch gate that blocks wee-dev from picking up new issues
when an existing issue is in a QA-blocking state (qa-review or in-progress).

The gate is checked:
1. By the scheduler executor before dispatching jobs with ``gate_check`` config
2. By the dispatch_wee_dev_work_queue.py script before item selection

Blocking states:
- ``wee-dev:qa-review`` — PR submitted, waiting for wee-qa verdict
- ``wee-dev:in-progress`` — wee-dev is still working on an issue
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

REPO = os.getenv("WEE_DEV_REPO", "leprachuan/Wee-Orchestrator")
LOCK_PATH = Path(os.getenv("WEE_DEV_LOCK_PATH", "/opt/wee-dev/WORK_QUEUE.lock.json"))

# Labels that indicate wee-dev should NOT pick up a new issue
QA_BLOCKING_LABELS = frozenset({"wee-dev:qa-review", "wee-dev:in-progress"})


def read_lock(lock_path: Optional[Path] = None) -> Optional[Dict]:
    """Read the work queue lock file."""
    path = lock_path or LOCK_PATH
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _gh_issue_list(repo: str, label: str, timeout: int = 30) -> List[Dict]:
    """List open issues with a specific label via ``gh`` CLI."""
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                repo,
                "--label",
                label,
                "--state",
                "open",
                "--limit",
                "10",
                "--json",
                "number,title,labels",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Failed to query GitHub issues for label %r: %s", label, exc)
    return []


def _gh_pr_list(repo: str, state: str = "open", timeout: int = 30) -> List[Dict]:
    """List PRs with head branches matching ``issue/*`` via ``gh`` CLI."""
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                state,
                "--limit",
                "20",
                "--json",
                "number,title,headRefName,state,labels,mergeable",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Failed to query GitHub PRs: %s", exc)
    return []


def check_blocking_issues(
    repo: Optional[str] = None,
) -> List[Dict]:
    """Return open issues that have QA-blocking labels.

    An issue with ``wee-dev:qa-review`` or ``wee-dev:in-progress`` means
    wee-dev must not dequeue a new work item.
    """
    repo = repo or REPO
    blocking: List[Dict] = []
    for label in sorted(QA_BLOCKING_LABELS):
        issues = _gh_issue_list(repo, label)
        for issue in issues:
            issue_labels = {
                lbl["name"] if isinstance(lbl, dict) else lbl
                for lbl in issue.get("labels", [])
            }
            if issue_labels & QA_BLOCKING_LABELS:
                blocking.append(
                    {
                        "number": issue["number"],
                        "title": issue.get("title", ""),
                        "blocking_labels": sorted(issue_labels & QA_BLOCKING_LABELS),
                    }
                )
    # Deduplicate by issue number
    seen = set()
    unique: List[Dict] = []
    for item in blocking:
        if item["number"] not in seen:
            seen.add(item["number"])
            unique.append(item)
    return unique


def check_open_prs(repo: Optional[str] = None) -> List[Dict]:
    """Return open PRs from ``issue/*`` branches that are not yet merged.

    An open PR from a wee-dev branch means work is still in flight.
    """
    repo = repo or REPO
    prs = _gh_pr_list(repo, state="open")
    wee_dev_prs: List[Dict] = []
    for pr in prs:
        head = pr.get("headRefName", "")
        if head.startswith("issue/"):
            wee_dev_prs.append(
                {
                    "number": pr["number"],
                    "title": pr.get("title", ""),
                    "branch": head,
                }
            )
    return wee_dev_prs


def is_wee_dev_gated(
    repo: Optional[str] = None,
    lock_path: Optional[Path] = None,
    check_github: bool = True,
    check_prs: bool = True,
) -> Tuple[bool, str, Dict]:
    """Check if wee-dev is gated from picking up new issues.

    This is the primary entry point. Call this before dispatching wee-dev
    for a new issue.

    Returns:
        (gated, reason, details) where:
        - gated: True if wee-dev should NOT pick up a new issue
        - reason: Human-readable explanation
        - details: Dict with blocking issues/PRs for logging
    """
    repo = repo or REPO
    lpath = lock_path or LOCK_PATH
    details: Dict = {}

    # Check 1: Lock file state
    lock = read_lock(lpath)
    if lock:
        state = lock.get("state", "")
        if state in ("qa-review", "qa-gate-blocked", "wee-dev-running"):
            details["lock"] = lock
            work_id = lock.get("work_item_id", "unknown")
            reason = (
                f"Lock file: state={state!r}, "
                f"item={work_id} -- {lock.get('reason', 'no reason')}"
            )
            logger.info("QA Gate BLOCKED (lock): %s", reason)
            return True, reason, details

    # Check 2: GitHub issues with blocking labels
    if check_github:
        blocking = check_blocking_issues(repo)
        if blocking:
            details["blocking_issues"] = blocking
            nums = ", ".join(f"#{b['number']}" for b in blocking)
            labels = ", ".join(lbl for b in blocking for lbl in b["blocking_labels"])
            reason = (
                f"GitHub issues {nums} have blocking labels ({labels}) "
                f"-- wee-dev must wait for QA verdict"
            )
            logger.info("QA Gate BLOCKED (labels): %s", reason)
            return True, reason, details

    # Check 3: Open PRs from wee-dev branches
    if check_prs:
        prs = check_open_prs(repo)
        if prs:
            details["open_prs"] = prs
            pr_nums = ", ".join(f"PR #{p['number']}" for p in prs)
            reason = (
                f"Open PRs from wee-dev branches ({pr_nums}) "
                f"-- wee-dev must wait until merged/closed"
            )
            logger.info("QA Gate BLOCKED (PRs): %s", reason)
            return True, reason, details

    logger.debug("QA Gate: wee-dev is clear to pick up a new issue")
    return False, "", details
