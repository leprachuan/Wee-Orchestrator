"""Kanban board normalization for Wee Orchestrator.

The board intentionally lives under a Kanban-specific API surface instead of
reviving the removed WebUI TODO panel. It can read flat-file TODOs and,
optionally, GitHub issues via the `gh` CLI when a repository is configured.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


KANBAN_COLUMNS = ("todo", "in-progress", "ai-active", "pending-review", "done")
LABEL_PREFIXES = ("status:", "agent:", "due:", "priority:", "urgency:")
HIDDEN_LABEL = "kanban:hidden"


class KanbanError(RuntimeError):
    """Raised when a Kanban item operation cannot be completed."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _repo_from_git_origin() -> str | None:
    """Infer owner/repo from the current checkout's GitHub origin."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(Path(__file__).resolve().parent),
                "config",
                "--get",
                "remote.origin.url",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    origin = result.stdout.strip()
    if not origin:
        return None

    if origin.startswith("git@github.com:"):
        origin = origin.split(":", 1)[1]
    elif "github.com/" in origin:
        origin = origin.split("github.com/", 1)[1]
    else:
        return None

    origin = origin.removesuffix(".git").strip("/")
    parts = origin.split("/")
    if len(parts) < 2:
        return None
    return "/".join(parts[:2])


def _default_repo() -> str | None:
    return (
        os.environ.get("KANBAN_GITHUB_REPO")
        or os.environ.get("TODO_GITHUB_REPO")
        or _repo_from_git_origin()
    )


def _run_gh(args: list[str], timeout: int = 20) -> str:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "gh command failed").strip()
        raise KanbanError(detail, status_code=502)
    return result.stdout


def _github_issue_number(item_id: str) -> int:
    if not item_id.startswith("github:"):
        raise KanbanError(
            "Only GitHub-backed Kanban items can be edited right now.",
            status_code=400,
        )
    raw = item_id.split(":", 1)[1]
    if not raw.isdigit():
        raise KanbanError("Invalid GitHub Kanban item id.", status_code=400)
    return int(raw)


def _ensure_repo(repo: str | None = None) -> str:
    resolved = repo or _default_repo()
    if not resolved:
        raise KanbanError("No Kanban GitHub repository is configured.", status_code=400)
    return resolved


def _load_github_issue(repo: str, number: int) -> dict[str, Any]:
    stdout = _run_gh(
        [
            "issue",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "number,title,url,body,state,labels,comments,createdAt,updatedAt",
        ]
    )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise KanbanError(f"Invalid gh issue response: {exc}", status_code=502)


def _label_name(label: Any) -> str:
    if isinstance(label, dict):
        return str(label.get("name") or "")
    return str(label or "")


def _current_label_names(issue: dict[str, Any]) -> list[str]:
    return [name for label in issue.get("labels") or [] if (name := _label_name(label))]


def _ensure_label(repo: str, label: str) -> None:
    if not label:
        return
    color = "57606a"
    if label.startswith("status:"):
        color = "0e8a16"
    elif label.startswith("agent:"):
        color = "1d76db"
    elif label.startswith("due:"):
        color = "fbca04"
    elif label.startswith("priority:") or label.startswith("urgency:"):
        color = "d93f0b"

    subprocess.run(
        [
            "gh",
            "label",
            "create",
            label,
            "--repo",
            repo,
            "--color",
            color,
            "--description",
            "Managed by Wee Kanban",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _metadata_label_updates(
    current_labels: list[str],
    *,
    status: str | None = None,
    agent: str | None = None,
    due: str | None = None,
    priority: str | None = None,
    urgency: str | None = None,
) -> tuple[list[str], list[str]]:
    remove: list[str] = []
    add: list[str] = []
    fields = {
        "status:": status,
        "agent:": agent,
        "due:": due,
        "priority:": priority,
        "urgency:": urgency,
    }

    for prefix, value in fields.items():
        if value is None:
            continue
        remove.extend(label for label in current_labels if label.lower().startswith(prefix))
        normalized = value.strip()
        if normalized:
            if prefix == "status:" and normalized not in KANBAN_COLUMNS:
                raise KanbanError(f"Invalid status '{normalized}'.", status_code=400)
            if prefix == "urgency:" and normalized == "normal":
                continue
            add.append(f"{prefix}{normalized}")

    if urgency is not None:
        remove.extend(
            label
            for label in current_labels
            if label.lower() in {"urgent", "urgency:high"}
        )

    add_keys = {label.lower() for label in add}
    remove = [label for label in remove if label.lower() not in add_keys]

    return sorted(set(remove)), sorted(set(add))


def _user_label_updates(
    current_labels: list[str], labels: list[str] | None
) -> tuple[list[str], list[str]]:
    """Return label changes without allowing clients to replace Kanban metadata."""
    if labels is None:
        return [], []

    managed_prefixes = ("status:", "agent:", "due:", "priority:", "urgency:")

    def is_managed(label: str) -> bool:
        lower = label.lower()
        return lower == "urgent" or lower.startswith(managed_prefixes)

    desired: dict[str, str] = {}
    for raw_label in labels:
        label = raw_label.strip()
        if not label:
            continue
        if len(label) > 50:
            raise KanbanError("Labels must be 50 characters or fewer.", status_code=400)
        if is_managed(label):
            raise KanbanError(
                f"'{label}' is a managed Kanban label and cannot be edited directly.",
                status_code=400,
            )
        desired.setdefault(label.lower(), label)

    current_user = {label.lower(): label for label in current_labels if not is_managed(label)}
    remove = [label for key, label in current_user.items() if key not in desired]
    add = [label for key, label in desired.items() if key not in current_user]
    return sorted(remove), sorted(add)


def _edit_issue(
    repo: str,
    number: int,
    *,
    title: str | None = None,
    body: str | None = None,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
) -> None:
    args = ["issue", "edit", str(number), "--repo", repo]
    if title is not None:
        args.extend(["--title", title])
    if body is not None:
        args.extend(["--body", body])
    for label in remove_labels or []:
        args.extend(["--remove-label", label])
    for label in add_labels or []:
        _ensure_label(repo, label)
        args.extend(["--add-label", label])
    if len(args) == 5:
        return
    _run_gh(args)


def github_item(repo: str | None, item_id: str) -> dict[str, Any]:
    resolved = _ensure_repo(repo)
    number = _github_issue_number(item_id)
    issue = _load_github_issue(resolved, number)
    card = issue_to_card(issue)
    card["comments"] = issue.get("comments") or []
    card["repo"] = resolved
    return card


def update_github_item(
    repo: str | None,
    item_id: str,
    *,
    title: str | None = None,
    details: str | None = None,
    status: str | None = None,
    agent: str | None = None,
    due: str | None = None,
    priority: str | None = None,
    urgency: str | None = None,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    resolved = _ensure_repo(repo)
    number = _github_issue_number(item_id)
    issue = _load_github_issue(resolved, number)
    current_labels = _current_label_names(issue)
    remove, add = _metadata_label_updates(
        current_labels,
        status=status,
        agent=agent,
        due=due,
        priority=priority,
        urgency=urgency,
    )
    user_remove, user_add = _user_label_updates(current_labels, labels)
    remove = sorted(set(remove + user_remove))
    add = sorted(set(add + user_add))
    _edit_issue(
        resolved,
        number,
        title=title,
        body=details,
        add_labels=add,
        remove_labels=remove,
    )
    return github_item(resolved, item_id)


def comment_github_item(repo: str | None, item_id: str, body: str) -> dict[str, Any]:
    resolved = _ensure_repo(repo)
    number = _github_issue_number(item_id)
    if not body.strip():
        raise KanbanError("comment body is required.", status_code=400)
    _run_gh(["issue", "comment", str(number), "--repo", resolved, "--body", body])
    return github_item(resolved, item_id)


def complete_github_item(repo: str | None, item_id: str) -> dict[str, Any]:
    resolved = _ensure_repo(repo)
    number = _github_issue_number(item_id)
    issue = _load_github_issue(resolved, number)
    remove, add = _metadata_label_updates(_current_label_names(issue), status="done")
    _edit_issue(resolved, number, add_labels=add, remove_labels=remove)
    _run_gh(["issue", "close", str(number), "--repo", resolved, "--comment", "Marked complete from Wee Kanban."])
    return github_item(resolved, item_id)


def close_github_item(repo: str | None, item_id: str) -> dict[str, Any]:
    resolved = _ensure_repo(repo)
    number = _github_issue_number(item_id)
    _run_gh(["issue", "close", str(number), "--repo", resolved])
    return github_item(resolved, item_id)


def mark_github_item_ai_active(
    repo: str | None,
    item_id: str,
    *,
    agent: str,
    comment: str | None = None,
) -> dict[str, Any]:
    resolved = _ensure_repo(repo)
    number = _github_issue_number(item_id)
    if not agent.strip():
        raise KanbanError("agent is required.", status_code=400)
    issue = _load_github_issue(resolved, number)
    remove, add = _metadata_label_updates(
        _current_label_names(issue),
        status="ai-active",
        agent=agent.strip(),
    )
    _edit_issue(resolved, number, add_labels=add, remove_labels=remove)
    if comment:
        _run_gh(["issue", "comment", str(number), "--repo", resolved, "--body", comment])
    return github_item(resolved, item_id)


def parse_due(value: str | None) -> datetime | None:
    """Parse a date/datetime string into an aware datetime when possible."""
    if not value:
        return None

    raw = value.strip()
    if not raw:
        return None

    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed_date = date.fromisoformat(raw[:10])
            return datetime.combine(parsed_date, datetime.min.time()).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def due_bucket(due: str | None, now: datetime | None = None) -> str:
    """Return a compact due status used by WebUI and iOS badges."""
    due_dt = parse_due(due)
    if due_dt is None:
        return "none"

    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    if due_dt < now_dt:
        return "overdue"
    if due_dt.date() == now_dt.date():
        return "today"
    if due_dt <= now_dt + timedelta(days=3):
        return "soon"
    return "future"


def extract_label_data(labels: list[str]) -> dict[str, Any]:
    """Extract board metadata from label names."""
    data: dict[str, Any] = {
        "agent": None,
        "due": None,
        "priority": "normal",
        "urgency": "normal",
        "status": "todo",
    }

    for label in labels:
        name = label.strip()
        lower = name.lower()

        if lower.startswith("agent:"):
            data["agent"] = name.split(":", 1)[1].strip()
        elif lower.startswith("due:"):
            data["due"] = name.split(":", 1)[1].strip()
        elif lower in {"urgent", "urgency:urgent", "urgency:high"}:
            data["urgency"] = "urgent"
        elif lower.startswith("priority:"):
            data["priority"] = lower.split(":", 1)[1].strip() or "normal"
        elif lower.startswith("status:"):
            status = lower.split(":", 1)[1].strip()
            if status in KANBAN_COLUMNS:
                data["status"] = status

    return data


def _stable_id(source: str, key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"{source}:{digest}"


def _parse_header_todo(path: Path, status: str) -> dict[str, Any] | None:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None

    due = None
    labels: list[str] = []
    body_start = 0

    for idx, line in enumerate(lines):
        if line.startswith("DUE:"):
            due = line[4:].strip()
            body_start = idx + 1
        elif line.startswith("LABELS:"):
            raw = line[7:].strip()
            labels = [
                label.strip().strip("{}")
                for label in re.split(r"[,\s]+", raw)
                if label.strip().strip("{}")
            ]
            body_start = idx + 1
        else:
            break

    while body_start < len(lines) and not lines[body_start].strip():
        body_start += 1

    metadata = extract_label_data(labels)
    metadata["status"] = "done" if status == "done" else metadata["status"]
    if due and not metadata.get("due"):
        metadata["due"] = due

    return normalize_card(
        {
            "id": _stable_id("flat", str(path)),
            "title": path.name,
            "source": "flatfile",
            "status": metadata["status"],
            "agent": metadata["agent"],
            "priority": metadata["priority"],
            "urgency": metadata["urgency"],
            "due": metadata["due"],
            "labels": labels,
            "details": "\n".join(lines[body_start:]).strip(),
            "url": None,
            "created_at": None,
            "updated_at": None,
        }
    )


def load_flatfile_cards(todo_path: Path | None, limit: int = 200) -> list[dict[str, Any]]:
    """Load cards from folder-style TODOs or a legacy TODO markdown file."""
    if todo_path is None:
        return []

    cards: list[dict[str, Any]] = []

    if todo_path.is_dir():
        for subdir, status in (("ACTIVE", "todo"), ("COMPLETED", "done")):
            current_dir = todo_path / subdir
            if not current_dir.is_dir():
                continue
            for entry in sorted(current_dir.iterdir()):
                if len(cards) >= limit:
                    return cards
                if entry.is_file():
                    card = _parse_header_todo(entry, status)
                    if card:
                        cards.append(card)
        return cards

    if not todo_path.exists():
        return []

    for line in todo_path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:]
        if not stripped.startswith("[ ]"):
            continue

        content = re.sub(r"^\[.\]\s+", "", stripped)
        due = None
        due_match = re.search(r"\(due ([^)]+)\)", content)
        if due_match:
            due = due_match.group(1).strip()
        labels: list[str] = []
        label_match = re.search(r"\{([^}]+)\}", content)
        if label_match:
            labels = [label.strip() for label in label_match.group(1).split(",")]
        title = re.sub(r"\s*\(due [^)]+\)", "", content)
        title = re.sub(r"\s*\{[^}]+\}", "", title).strip()
        metadata = extract_label_data(labels)
        if due and not metadata.get("due"):
            metadata["due"] = due
        cards.append(
            normalize_card(
                {
                    "id": _stable_id("flat", f"{todo_path}:{title}"),
                    "title": title,
                    "source": "flatfile",
                    "status": metadata["status"],
                    "agent": metadata["agent"],
                    "priority": metadata["priority"],
                    "urgency": metadata["urgency"],
                    "due": metadata["due"],
                    "labels": labels,
                    "details": "",
                    "url": None,
                    "created_at": None,
                    "updated_at": None,
                }
            )
        )
        if len(cards) >= limit:
            break

    return cards


def _issue_labels(issue: dict[str, Any]) -> list[str]:
    labels = issue.get("labels") or []
    names: list[str] = []
    for label in labels:
        if isinstance(label, dict):
            name = label.get("name")
        else:
            name = str(label)
        if name:
            names.append(str(name))
    return names


def _is_hidden_issue(issue: dict[str, Any]) -> bool:
    return any(label.lower() == HIDDEN_LABEL for label in _issue_labels(issue))


def issue_to_card(issue: dict[str, Any]) -> dict[str, Any]:
    labels = _issue_labels(issue)
    metadata = extract_label_data(labels)
    state = issue.get("state") or "open"
    status = "done" if state == "closed" else metadata["status"]

    return normalize_card(
        {
            "id": f"github:{issue.get('number')}",
            "title": issue.get("title") or f"Issue #{issue.get('number')}",
            "source": "github",
            "status": status,
            "agent": metadata["agent"],
            "priority": metadata["priority"],
            "urgency": metadata["urgency"],
            "due": metadata["due"],
            "labels": labels,
            "details": issue.get("body") or "",
            "url": issue.get("url"),
            "created_at": issue.get("createdAt"),
            "updated_at": issue.get("updatedAt"),
            "github_issue_number": issue.get("number"),
        }
    )


def load_github_cards(repo: str | None, limit: int = 100) -> list[dict[str, Any]]:
    """Load GitHub issues with gh CLI when a repo is configured."""
    if not repo:
        return []

    result = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--limit",
            str(limit),
            "--json",
            "number,title,url,body,state,labels,createdAt,updatedAt",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        return []

    try:
        issues = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    return [issue_to_card(issue) for issue in issues if not _is_hidden_issue(issue)]


def normalize_card(card: dict[str, Any]) -> dict[str, Any]:
    status = card.get("status") or "todo"
    if status not in KANBAN_COLUMNS:
        status = "todo"

    due = card.get("due")
    bucket = due_bucket(due)
    card["status"] = status
    card["due_bucket"] = bucket
    card["is_overdue"] = bucket == "overdue"
    card.setdefault("labels", [])
    card.setdefault("details", "")
    return card


def group_cards(cards: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    columns = {column: [] for column in KANBAN_COLUMNS}
    for card in cards:
        columns.setdefault(card["status"], []).append(card)
    return columns


def filter_cards(
    cards: list[dict[str, Any]],
    agent: str | None = None,
    urgency: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    source: str | None = None,
) -> list[dict[str, Any]]:
    filtered = cards
    if agent:
        filtered = [card for card in filtered if card.get("agent") == agent]
    if urgency:
        filtered = [card for card in filtered if card.get("urgency") == urgency]
    if source:
        filtered = [card for card in filtered if card.get("source") == source]
    if date_from:
        filtered = [
            card
            for card in filtered
            if card.get("due") and card["due"][:10] >= date_from
        ]
    if date_to:
        filtered = [
            card for card in filtered if card.get("due") and card["due"][:10] <= date_to
        ]
    return filtered


def load_kanban_board(
    todo_path: Path | None = None,
    repo: str | None = None,
    limit: int = 200,
    agent: str | None = None,
    urgency: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Load and group Kanban cards from all enabled sources."""
    bounded_limit = min(max(1, limit), 500)
    repo = repo or _default_repo()
    github_cards = load_github_cards(repo, bounded_limit)
    flat_cards = load_flatfile_cards(todo_path, bounded_limit)
    cards = filter_cards(
        [*github_cards, *flat_cards],
        agent=agent,
        urgency=urgency,
        date_from=date_from,
        date_to=date_to,
        source=source,
    )

    agents = sorted({card["agent"] for card in cards if card.get("agent")})
    sources = sorted({card["source"] for card in cards if card.get("source")})

    return {
        "success": True,
        "columns": group_cards(cards),
        "agents": agents,
        "sources": sources,
        "total": len(cards),
        "repo": repo,
    }
