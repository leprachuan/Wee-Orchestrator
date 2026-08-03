"""
Regression test for issue #456: `GET /api/v1/kanban/board` returned a bare
500 "Internal server error" whenever the `gh` CLI was missing from the API
process's PATH, or slow enough to hit its 20s timeout.

This was found from a real failure. A GUI-launched macOS client starts its
Local API subprocess with launchd's minimal PATH
(`/usr/bin:/bin:/usr/sbin:/sbin`), which omits Homebrew, so
`subprocess.run(["gh", ...])` raised `FileNotFoundError`. Two gaps combined:

1. `load_github_cards` caught a non-zero return code and a JSON decode error,
   but neither `FileNotFoundError` nor `subprocess.TimeoutExpired`.
2. The board route called `load_kanban_board` with no try/except, unlike every
   sibling kanban route, which funnel through `_kanban_http_error`.

The client had no way to distinguish "your tooling is misconfigured" from "the
server is broken". These tests pin both halves: the loader raises a typed
KanbanError with an actionable status code, and the route reports it rather
than collapsing it into a 500.
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_456")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9456")

import kanban  # noqa: E402
from kanban import KanbanError, load_github_cards  # noqa: E402


def test_issue_456_missing_gh_binary_raises_actionable_error(monkeypatch):
    def _raise_missing(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory: 'gh'")

    monkeypatch.setattr(kanban.subprocess, "run", _raise_missing)

    with pytest.raises(KanbanError) as excinfo:
        load_github_cards("leprachuan/fosterbot-home", 200)

    assert excinfo.value.status_code == 503
    message = str(excinfo.value)
    assert "gh" in message
    assert "PATH" in message, "the message must point at the actual cause"


def test_issue_456_gh_timeout_raises_gateway_timeout(monkeypatch):
    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=20)

    monkeypatch.setattr(kanban.subprocess, "run", _raise_timeout)

    with pytest.raises(KanbanError) as excinfo:
        load_github_cards("leprachuan/fosterbot-home", 200)

    assert excinfo.value.status_code == 504


def test_issue_456_no_repo_still_short_circuits(monkeypatch):
    """The guard clause must run before any subprocess attempt."""

    def _explode(*args, **kwargs):
        raise AssertionError("gh must not be invoked when no repo is configured")

    monkeypatch.setattr(kanban.subprocess, "run", _explode)
    assert load_github_cards(None, 200) == []


def test_issue_456_non_zero_exit_still_degrades_quietly(monkeypatch):
    """A gh that runs but fails (e.g. auth) keeps the previous behaviour:
    GitHub cards are skipped rather than failing the whole board."""

    class _Result:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(kanban.subprocess, "run", lambda *a, **k: _Result())
    assert load_github_cards("leprachuan/fosterbot-home", 200) == []


def test_issue_456_board_route_reports_error_instead_of_bare_500(monkeypatch):
    """The route must surface the loader's status code, not collapse to 500."""
    try:
        from starlette.testclient import TestClient
    except ImportError:  # pragma: no cover - depends on installed extras
        from fastapi.testclient import TestClient

    from agent_manager import create_api_app

    def _raise_missing(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory: 'gh'")

    monkeypatch.setattr(kanban.subprocess, "run", _raise_missing)
    monkeypatch.setattr(kanban, "_default_repo", lambda: "leprachuan/fosterbot-home")

    client = TestClient(create_api_app(), raise_server_exceptions=False)
    response = client.get(
        "/api/v1/kanban/board",
        headers={"Authorization": f"Bearer shared_{os.environ['API_SHARED_KEY']}"},
    )

    assert response.status_code == 503, response.text
    assert "gh" in response.json()["detail"]
