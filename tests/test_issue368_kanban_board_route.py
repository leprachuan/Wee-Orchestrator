"""
Regression test for issue #368: the iOS Kanban screen showed
`HTTP 404: {"detail":"Not Found"}` because the backend did not serve
`/api/v1/kanban/board`.

The route exists now, so this guards the server side of that failure: if it is
ever removed or renamed again, the iOS and macOS Kanban screens fall back to
`/api/v1/todos` at best, and show a raw 404 at worst.

Scope note. The client-side half of #368 — falling back to `/api/v1/todos` on a
404 — lives in the iOS client repo (`WeeAPIClient.kanbanBoard()`) and is present
on its `main`. It has no test of its own because that project has a single app
target with no test target or shared scheme, so adding one is separate work.
This file covers only what belongs to this repo.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_368")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9368")

from agent_manager import create_api_app  # noqa: E402

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover - depends on installed extras
    from fastapi.testclient import TestClient

BOARD_PATH = "/api/v1/kanban/board"


@pytest.fixture(scope="module")
def app():
    return create_api_app()


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app, raise_server_exceptions=False)


def _registered_paths(app):
    return {getattr(route, "path", None) for route in app.routes}


def test_kanban_board_route_is_registered(app):
    """The exact path the clients call must exist."""
    assert BOARD_PATH in _registered_paths(app), (
        f"{BOARD_PATH} is not registered — the iOS/macOS Kanban screens will 404 "
        "(issue #368)"
    )


def test_kanban_board_is_a_get_route(app):
    """A wrong method would also surface to the client as a failure."""
    methods = set()
    for route in app.routes:
        if getattr(route, "path", None) == BOARD_PATH:
            methods |= set(getattr(route, "methods", set()) or set())
    assert "GET" in methods, f"{BOARD_PATH} must accept GET, got {methods or 'none'}"


def test_kanban_board_does_not_return_404(client):
    """The reported symptom was literally a 404 from this path.

    Any other status is acceptable here — unauthenticated requests may be
    rejected, and the board's contents depend on live data. 404 specifically
    means the route is missing, which is the bug.
    """
    response = client.get(BOARD_PATH)

    assert response.status_code != 404, (
        "issue #368 regression: the Kanban board route is missing, so clients "
        f"receive {response.text[:120]!r}"
    )


def test_legacy_todos_fallback_target_still_exists(app):
    """The iOS client falls back to /api/v1/todos on a 404.

    That fallback is only useful while this path is served, so removing it would
    silently break the mitigation #368 asked for.
    """
    paths = _registered_paths(app)
    assert "/api/v1/todos" in paths, (
        "/api/v1/todos is the iOS fallback for a missing Kanban board (#368); "
        "removing it leaves the client with nothing to fall back to"
    )
