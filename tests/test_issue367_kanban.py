"""Regression tests for Issue #367: native Kanban board API."""

from datetime import datetime, timezone

import pytest


def test_issue367_extracts_board_metadata_from_labels():
    from kanban import extract_label_data

    metadata = extract_label_data(
        [
            "agent:research",
            "due:2026-06-15",
            "urgent",
            "priority:high",
            "status:ai-active",
        ]
    )

    assert metadata == {
        "agent": "research",
        "due": "2026-06-15",
        "priority": "high",
        "urgency": "urgent",
        "status": "ai-active",
    }


def test_issue367_defaults_repo_from_git_origin(monkeypatch):
    import subprocess

    import kanban

    class Result:
        returncode = 0
        stdout = "https://github.com/leprachuan/Wee-Orchestrator.git\n"

    monkeypatch.delenv("KANBAN_GITHUB_REPO", raising=False)
    monkeypatch.delenv("TODO_GITHUB_REPO", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())

    assert kanban._default_repo() == "leprachuan/Wee-Orchestrator"


@pytest.mark.parametrize(
    ("due", "expected"),
    [
        ("2026-06-13", "overdue"),
        ("2026-06-14T20:00:00+00:00", "today"),
        ("2026-06-16", "soon"),
        ("2026-06-25", "future"),
        (None, "none"),
    ],
)
def test_issue367_assigns_due_buckets(due, expected):
    from kanban import due_bucket

    now = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)

    assert due_bucket(due, now=now) == expected


def test_issue367_loads_folder_style_todos(tmp_path):
    from kanban import load_flatfile_cards

    todo_root = tmp_path / "TODOs"
    active = todo_root / "ACTIVE"
    completed = todo_root / "COMPLETED"
    active.mkdir(parents=True)
    completed.mkdir()

    (active / "Review quotes").write_text(
        "DUE: 2026-06-15\n"
        "LABELS: {agent:research},{urgent},{status:pending-review}\n\n"
        "Compare vendor quotes before approving."
    )
    (completed / "Archive receipts").write_text(
        "LABELS: {agent:family_knowledge}\n\nDone yesterday."
    )

    cards = load_flatfile_cards(todo_root)

    assert [card["title"] for card in cards] == [
        "Review quotes",
        "Archive receipts",
    ]
    assert cards[0]["status"] == "pending-review"
    assert cards[0]["agent"] == "research"
    assert cards[0]["urgency"] == "urgent"
    assert cards[0]["due"] == "2026-06-15"
    assert cards[1]["status"] == "done"


def test_issue367_groups_and_filters_board(monkeypatch, tmp_path):
    from kanban import load_kanban_board

    def fake_github_cards(repo, limit):
        return [
            {
                "id": "github:367",
                "title": "Build native Kanban",
                "source": "github",
                "status": "ai-active",
                "agent": "wee-dev",
                "priority": "normal",
                "urgency": "urgent",
                "due": "2026-06-14",
                "labels": ["agent:wee-dev", "urgent", "status:ai-active"],
                "details": "",
                "url": "https://github.com/leprachuan/Wee-Orchestrator/issues/367",
            }
        ]

    monkeypatch.setattr("kanban.load_github_cards", fake_github_cards)
    monkeypatch.setenv("KANBAN_GITHUB_REPO", "leprachuan/Wee-Orchestrator")

    board = load_kanban_board(tmp_path, agent="wee-dev", urgency="urgent")

    assert board["success"] is True
    assert board["total"] == 1
    assert board["agents"] == ["wee-dev"]
    assert board["sources"] == ["github"]
    assert board["repo"] == "leprachuan/Wee-Orchestrator"
    assert board["columns"]["ai-active"][0]["id"] == "github:367"


def test_issue367_load_github_cards_skips_hidden_issues(monkeypatch):
    import json
    import subprocess

    from kanban import load_github_cards

    class Result:
        returncode = 0
        stdout = json.dumps(
            [
                {
                    "number": 1,
                    "title": "Visible",
                    "state": "open",
                    "labels": [],
                },
                {
                    "number": 2,
                    "title": "Hidden",
                    "state": "open",
                    "labels": [{"name": "kanban:hidden"}],
                },
            ]
        )

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())

    cards = load_github_cards("owner/repo")

    assert [card["id"] for card in cards] == ["github:1"]


def test_issue367_update_github_item_rewrites_metadata_labels(monkeypatch):
    import kanban

    issue = {
        "number": 3,
        "title": "Test TODO",
        "body": "Details",
        "state": "open",
        "url": "https://github.com/owner/repo/issues/3",
        "labels": [
            {"name": "status:todo"},
            {"name": "agent:old"},
            {"name": "urgent"},
            {"name": "keep"},
        ],
    }
    calls = {}

    monkeypatch.setattr(kanban, "_load_github_issue", lambda repo, number: issue)
    monkeypatch.setattr(kanban, "_ensure_label", lambda repo, label: None)

    def fake_edit(repo, number, **kwargs):
        calls.update(kwargs)

    monkeypatch.setattr(kanban, "_edit_issue", fake_edit)

    kanban.update_github_item(
        "owner/repo",
        "github:3",
        status="ai-active",
        agent="wee-dev",
        urgency="normal",
    )

    assert calls["add_labels"] == ["agent:wee-dev", "status:ai-active"]
    assert calls["remove_labels"] == ["agent:old", "status:todo", "urgent"]


def test_issue381_update_github_item_replaces_only_user_labels(monkeypatch):
    import kanban

    issue = {
        "number": 381,
        "title": "Manage labels",
        "body": "Details",
        "state": "open",
        "url": "https://github.com/owner/repo/issues/381",
        "labels": [
            {"name": "status:todo"},
            {"name": "agent:wee-dev"},
            {"name": "keep"},
            {"name": "remove-me"},
        ],
    }
    calls = {}

    monkeypatch.setattr(kanban, "_load_github_issue", lambda repo, number: issue)
    monkeypatch.setattr(kanban, "_ensure_label", lambda repo, label: None)
    monkeypatch.setattr(kanban, "_edit_issue", lambda repo, number, **kwargs: calls.update(kwargs))
    monkeypatch.setattr(kanban, "github_item", lambda repo, item_id: {})

    kanban.update_github_item(
        "owner/repo",
        "github:381",
        labels=["keep", "New Label", "new label", "  "],
    )

    assert calls["add_labels"] == ["New Label"]
    assert calls["remove_labels"] == ["remove-me"]


def test_issue381_empty_label_list_removes_all_user_labels(monkeypatch):
    import kanban

    issue = {
        "number": 381,
        "title": "Manage labels",
        "body": "Details",
        "state": "open",
        "url": "https://github.com/owner/repo/issues/381",
        "labels": [{"name": "status:todo"}, {"name": "one"}, {"name": "two"}],
    }
    calls = {}

    monkeypatch.setattr(kanban, "_load_github_issue", lambda repo, number: issue)
    monkeypatch.setattr(kanban, "_edit_issue", lambda repo, number, **kwargs: calls.update(kwargs))
    monkeypatch.setattr(kanban, "github_item", lambda repo, item_id: {})

    kanban.update_github_item("owner/repo", "github:381", labels=[])

    assert calls["add_labels"] == []
    assert calls["remove_labels"] == ["one", "two"]


def test_issue381_rejects_managed_labels():
    import kanban

    with pytest.raises(kanban.KanbanError, match="managed Kanban label"):
        kanban._user_label_updates(["status:todo"], ["status:done"])


def test_issue367_dispatch_marks_github_item_ai_active(monkeypatch):
    import kanban

    issue = {
        "number": 8,
        "title": "Dispatch TODO",
        "body": "Details",
        "state": "open",
        "url": "https://github.com/owner/repo/issues/8",
        "labels": [{"name": "status:todo"}],
    }
    calls = {"comments": []}

    monkeypatch.setattr(kanban, "_load_github_issue", lambda repo, number: issue)
    monkeypatch.setattr(kanban, "_ensure_label", lambda repo, label: None)

    def fake_edit(repo, number, **kwargs):
        calls.update(kwargs)

    def fake_run_gh(args, timeout=20):
        calls["comments"].append(args)
        return "{}"

    monkeypatch.setattr(kanban, "_edit_issue", fake_edit)
    monkeypatch.setattr(kanban, "_run_gh", fake_run_gh)

    kanban.mark_github_item_ai_active(
        "owner/repo",
        "github:8",
        agent="wee-dev",
        comment="Dispatched",
    )

    assert calls["add_labels"] == ["agent:wee-dev", "status:ai-active"]
    assert calls["remove_labels"] == ["status:todo"]
    assert calls["comments"] == [
        ["issue", "comment", "8", "--repo", "owner/repo", "--body", "Dispatched"]
    ]


def test_issue367_dispatch_preserves_existing_agent_label(monkeypatch):
    import kanban

    issue = {
        "number": 9,
        "title": "Dispatch TODO",
        "body": "Details",
        "state": "open",
        "url": "https://github.com/owner/repo/issues/9",
        "labels": [{"name": "status:in-progress"}, {"name": "agent:wee-dev"}],
    }
    calls = {}

    monkeypatch.setattr(kanban, "_load_github_issue", lambda repo, number: issue)
    monkeypatch.setattr(kanban, "_ensure_label", lambda repo, label: None)
    monkeypatch.setattr(kanban, "_edit_issue", lambda repo, number, **kwargs: calls.update(kwargs))

    kanban.mark_github_item_ai_active("owner/repo", "github:9", agent="wee-dev")

    assert calls["add_labels"] == ["agent:wee-dev", "status:ai-active"]
    assert calls["remove_labels"] == ["status:in-progress"]


def test_issue367_kanban_api_requires_auth():
    from fastapi.testclient import TestClient

    from agent_manager import create_api_app

    client = TestClient(create_api_app(), raise_server_exceptions=False)

    response = client.get("/api/v1/kanban/board")

    assert response.status_code == 401


def test_issue367_kanban_api_returns_board(monkeypatch):
    from fastapi.testclient import TestClient

    import kanban
    from agent_manager import create_api_app

    monkeypatch.setenv("API_SHARED_KEY", "test_key_367")

    def fake_board(**kwargs):
        return {
            "success": True,
            "columns": {
                "todo": [],
                "in-progress": [],
                "ai-active": [],
                "pending-review": [],
                "done": [],
            },
            "agents": [],
            "sources": [],
            "total": 0,
            "repo": None,
            "kwargs": {key: str(value) for key, value in kwargs.items()},
        }

    monkeypatch.setattr(kanban, "load_kanban_board", fake_board)

    client = TestClient(create_api_app(), raise_server_exceptions=False)
    response = client.get(
        "/api/v1/kanban/board?agent=research&urgency=urgent&source=github",
        headers={"Authorization": "Bearer shared_test_key_367"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["kwargs"]["agent"] == "research"
    assert payload["kwargs"]["urgency"] == "urgent"
    assert payload["kwargs"]["source"] == "github"


def test_issue367_kanban_api_updates_item(monkeypatch):
    from fastapi.testclient import TestClient

    import kanban
    from agent_manager import create_api_app

    monkeypatch.setenv("API_SHARED_KEY", "test_key_367")

    def fake_update(repo, item_id, **kwargs):
        return {
            "id": item_id,
            "repo": repo,
            "title": kwargs["title"],
            "status": kwargs["status"],
            "agent": kwargs["agent"],
        }

    monkeypatch.setattr(kanban, "update_github_item", fake_update)

    client = TestClient(create_api_app(), raise_server_exceptions=False)
    response = client.patch(
        "/api/v1/kanban/items/github:3?repo=owner/repo",
        headers={"Authorization": "Bearer shared_test_key_367"},
        json={"title": "Updated", "status": "ai-active", "agent": "wee-dev"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": "github:3",
        "repo": "owner/repo",
        "title": "Updated",
        "status": "ai-active",
        "agent": "wee-dev",
    }


def test_issue367_kanban_settings_persist_repo(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import agent_manager

    monkeypatch.setenv("API_SHARED_KEY", "test_key_367")
    monkeypatch.delenv("KANBAN_GITHUB_REPO", raising=False)
    monkeypatch.setattr(agent_manager, "SCRIPT_BASE_DIR", str(tmp_path))

    client = TestClient(agent_manager.create_api_app(), raise_server_exceptions=False)
    headers = {"Authorization": "Bearer shared_test_key_367"}

    response = client.put(
        "/api/v1/settings/kanban",
        headers=headers,
        json={"github_repo": "leprachuan/fosterbot-home"},
    )

    assert response.status_code == 200
    assert response.json()["effective_repo"] == "leprachuan/fosterbot-home"
    assert (tmp_path / ".env").read_text() == (
        "KANBAN_GITHUB_REPO=leprachuan/fosterbot-home\n"
    )

    response = client.get("/api/v1/settings/kanban", headers=headers)

    assert response.status_code == 200
    assert response.json()["github_repo"] == "leprachuan/fosterbot-home"
