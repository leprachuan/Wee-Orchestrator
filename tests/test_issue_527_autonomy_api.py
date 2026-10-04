import json
import threading
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import agent_manager
import autonomy_models


@pytest.fixture
def client(monkeypatch, tmp_path, request):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("APP_ENV", "DEV")
    monkeypatch.setenv("API_SHARED_KEY", "autonomy-api-test")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("WEE_TASK_NOTIFICATIONS", "false")
    config = tmp_path / "agents.json"
    config.write_text(
        json.dumps({"agents": [{"name": "a", "path": str(tmp_path), "runtime": "wee"}]})
    )
    monkeypatch.setenv("AGENT_CONFIG_FILE", str(config))
    monkeypatch.setenv("SCHEDULER_JOBS_FILE", str(tmp_path / "scheduler/jobs.json"))
    monkeypatch.setenv("WEE_ALWAYS_ON_STATE_DIR", str(tmp_path / "autonomy"))

    class History(agent_manager.HistoryManager):
        def __init__(self):
            self._path = str(tmp_path / "history.json")
            self._lock = threading.Lock()
            self._save({})

    class Tasks(agent_manager.BackgroundTaskManager):
        def __init__(self):
            self._path = str(tmp_path / "tasks.json")
            self._lock = threading.Lock()
            self._tasks_cache = []
            self._bg_events = {}
            self._bg_events_lock = threading.Lock()
            self._cleanup_thread_started = False

    monkeypatch.setattr(agent_manager, "HistoryManager", History)
    monkeypatch.setattr(agent_manager, "BackgroundTaskManager", Tasks)
    original = autonomy_models.ModelPlanner.__init__
    calls = []

    def initialize(self, *args, **kwargs):
        kwargs["completion"] = lambda *args: (
            calls.append(args[0]) or '{"report":"API integration report"}',
            {"total_tokens": 50},
        )
        original(self, *args, **kwargs)

    monkeypatch.setattr(autonomy_models.ModelPlanner, "__init__", initialize)
    if getattr(request, "param", None) == "corrupt":
        state = tmp_path / "autonomy"
        state.mkdir(mode=0o700)
        db = state / "approvals.sqlite"
        db.touch(mode=0o600)
        db.write_bytes(b"not a SQLite database")
    app = agent_manager.create_api_app()
    with TestClient(app) as test:
        yield test, calls


HEADERS = {
    "Authorization": "Bearer shared_autonomy-api-test",
    "X-User-Identity": "forged-admin",
}


def wait_for(read, condition):
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        result = read()
        if condition(result):
            return result
        time.sleep(0.1)
    pytest.fail("Worker did not reach expected state")


def test_full_api_lifespan_opt_in_shared_approval_and_budget(client):
    c, calls = client
    assert c.get("/api/v1/autonomy/approvals").status_code == 401
    assert c.get("/api/v1/health").status_code == 200
    created = c.post(
        "/api/v1/autonomy/responsibilities",
        headers=HEADERS,
        json={"agent": "a", "goal": "Review observed queue", "interval_seconds": 300},
    ).json()
    assert created["status"] == "paused" and not calls
    c.post(
        "/api/v1/autonomy/responsibilities/" + created["id"] + "/control",
        headers=HEADERS,
        json={"command": "resume"},
    )
    listing = lambda: c.get("/api/v1/autonomy/approvals", headers=HEADERS).json()[
        "requests"
    ]
    r = wait_for(listing, lambda rows: len(rows) == 1)[0]
    assert r["preview"]["details"] == "API integration report"
    body = {"decision": "approve_once", "fingerprint": r["fingerprint"]}
    result = c.post(
        "/api/v1/autonomy/approvals/" + r["id"] + "/decision",
        headers=HEADERS,
        json=body,
    ).json()
    assert result["won"] and result["request"]["decided_by"] == "shared-key-client"
    again = c.post(
        "/api/v1/autonomy/approvals/" + r["id"] + "/decision",
        headers=HEADERS,
        json=body,
    ).json()
    assert not again["won"]
    wait_for(listing, lambda rows: rows[0]["status"] == "succeeded")
    work = c.get("/api/v1/autonomy/responsibilities", headers=HEADERS).json()[
        "responsibilities"
    ][0]
    assert work["report"] == "API integration report"
    assert len(calls) == 1
    settings = c.get("/api/v1/autonomy/model-settings", headers=HEADERS).json()
    assert settings["usage"]["requests"] == 1 and settings["cost_usd"] is None
    assert (
        c.post(
            "/api/v1/autonomy/responsibilities/" + created["id"] + "/control",
            headers=HEADERS,
            json={"command": "pause"},
        ).json()["status"]
        == "paused"
    )
    assert (
        c.post(
            "/api/v1/autonomy/responsibilities/" + created["id"] + "/control",
            headers=HEADERS,
            json={"command": "cancel"},
        ).json()["status"]
        == "cancelled"
    )


def test_api_rejects_unknown_agent_and_unsafe_budgets(client):
    c, _ = client
    assert (
        c.post(
            "/api/v1/autonomy/responsibilities",
            headers=HEADERS,
            json={"agent": "missing", "goal": "test", "interval_seconds": 300},
        ).status_code
        == 400
    )
    config = c.get("/api/v1/autonomy/model-settings", headers=HEADERS).json()["config"]
    assert (
        c.put(
            "/api/v1/autonomy/model-settings",
            headers=HEADERS,
            json={**config, "max_requests_per_run": 999},
        ).status_code
        == 400
    )
    assert (
        c.put(
            "/api/v1/autonomy/model-settings",
            headers=HEADERS,
            json={**config, "routine_model": "unqualified-model"},
        ).status_code
        == 400
    )


@pytest.mark.parametrize("client", ["corrupt"], indirect=True)
def test_corrupt_optional_state_disables_autonomy_without_breaking_health(client):
    c, calls = client
    assert c.get("/api/v1/health").status_code == 200
    assert c.get("/api/v1/autonomy/approvals").status_code == 401
    assert c.get("/api/v1/autonomy/approvals", headers=HEADERS).status_code == 503
    assert not calls
