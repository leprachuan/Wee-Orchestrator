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
