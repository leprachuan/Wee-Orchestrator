import copy
import json
from uuid import uuid4

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from autonomy_coordinator import (
    Coordinator,
    ResponsibilityStore,
    create_responsibility_router,
)
from autonomy_github import (
    GitHub,
    GitHubUnavailable,
    RepositoryGoals,
    create_repository_router,
    repository,
)
from autonomy_service import ApprovalService, OWNER, create_router

AUTH = {"auth_type": "shared_key"}


def issue(number=1, repo="owner/work", agent="a", **changes):
    return {
        "id": number + 100,
        "number": number,
        "title": "Watch the service",
        "body": "- [ ] Check health\n- [ ] Record findings",
        "state": "open",
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "labels": [{"name": "always-on"}, {"name": "agent:" + agent}],
        **changes,
    }


class FakeGitHub:
    def __init__(self):
        self.data = {("owner/work", 1): issue()}
        self.writes = []
        self.fail = False
        self.partial = False

    def issue(self, repo, number):
        if self.fail:
            raise GitHubUnavailable("HTTP 503")
        return copy.deepcopy(self.data[repo, number])

    def issues(self, repo):
        if self.fail or self.partial:
            raise GitHubUnavailable("Partial snapshot")
        return [
            copy.deepcopy(i)
            for (r, n), i in self.data.items()
            if r == repo
            and i["state"] == "open"
            and any(l["name"] == "always-on" for l in i["labels"])
        ]

    def ensure_label(self, repo, name):
        pass

    def request(self, method, path, body):
        self.writes.append((method, path, body))
        parts = path.split("/")
        repo = "/".join(parts[2:4])
        if method == "POST" and parts[-1] == "issues":
            n = max([n for r, n in self.data if r == repo], default=0) + 1
            row = issue(
                n,
                repo=repo,
                title=body["title"],
                body=body["body"],
                labels=[{"name": l} for l in body["labels"]],
            )
            self.data[repo, n] = row
            return copy.deepcopy(row)
        n = int(parts[5])
        row = self.data[repo, n]
        if method == "POST":
            labels = {l["name"] for l in row["labels"]} | set(body["labels"])
            row["labels"] = [{"name": l} for l in sorted(labels)]
        else:
            row.update(body)
        return copy.deepcopy(row)


@pytest.fixture
def system(tmp_path):
    service = ApprovalService(tmp_path)
    store = ResponsibilityStore(tmp_path)
    remote = FakeGitHub()
    agents = {"a", "b"}
    goals = RepositoryGoals(store, service, lambda: agents, remote)
    store.repository_goals = goals
    goals.configure([{"repository": "owner/work", "enabled": True}], "owner/work", "a")
    return service, store, remote, goals, agents


def approve(service):
    pending = [
        r for r in service.approvals.list(owner=OWNER) if r["status"] == "pending"
    ]
    assert pending
    for row in pending:
        service.decide(row["id"], AUTH, "approve_once", row["fingerprint"])


def test_discovery_is_paused_unique_and_durable(system):
    s, store, remote, goals, agents = system
    goals.sync(True)
    goals.sync(True)
    assert len(store.list()) == 1
    row = store.list()[0]
    assert row["status"] == "paused"
    assert goals.source(row["id"])["body"].startswith("- [ ]")
    restarted = RepositoryGoals(store, s, lambda: agents, remote)
    assert restarted.source(row["id"])["number"] == 1
    assert restarted.settings("a")["default_repository"] == "owner/work"


@pytest.mark.parametrize(
    "change",
    [
        {"state": "closed"},
        {"labels": [{"name": "agent:a"}]},
        {"labels": [{"name": "always-on"}, {"name": "agent:a"}, {"name": "agent:b"}]},
        {"labels": [{"name": "always-on"}, {"name": "agent:missing"}]},
    ],
)
def test_closure_label_and_assignment_changes_invalidate_runs(system, change):
    s, store, remote, goals, _ = system
    row = goals.ingest("owner/work", remote.issue("owner/work", 1))
    store.control(row["id"], "resume")
    store.update(row["id"], phase="waiting", checkpoint='{"report":"old"}')
    remote.data["owner/work", 1].update(change)
    assert not goals.validate(row["id"])
    current = store.get(row["id"])
    assert current["status"] == "paused" and current["checkpoint"] == "{}"
    assert current["run_number"] > row["run_number"]


def test_edit_and_reassignment_pause_and_reopen_never_auto_resumes(system):
    s, store, remote, goals, _ = system
    row = goals.ingest("owner/work", remote.issue("owner/work", 1))
    store.control(row["id"], "resume")
    remote.data["owner/work", 1]["body"] = "New checklist"
    assert goals.validate(row["id"])
    assert store.get(row["id"])["status"] == "paused"
    remote.data["owner/work", 1]["labels"] = [
        {"name": "always-on"},
        {"name": "agent:b"},
    ]
    goals.sync(True)
    assert store.get(row["id"])["agent"] == "b"
    remote.data["owner/work", 1]["state"] = "closed"
    goals.sync(True)
    remote.data["owner/work", 1]["state"] = "open"
    goals.sync(True)
    assert store.get(row["id"])["status"] == "paused"


def test_network_and_partial_snapshot_preserve_goals_but_preflight_stops_run(system):
    s, store, remote, goals, _ = system
    row = goals.ingest("owner/work", remote.issue("owner/work", 1))
    store.control(row["id"], "resume")
    remote.partial = True
    goals.sync(True)
    assert len(store.list()) == 1 and store.get(row["id"])["status"] == "active"
    remote.fail = True
    assert not goals.validate(row["id"])
    assert store.get(row["id"])["status"] == "paused"
    remote.fail = False
    remote.partial = False
    goals.sync(True)
    assert (
        goals.source(row["id"])["eligible"]
        and store.get(row["id"])["status"] == "paused"
    )


def test_disabled_repo_invalidates_pending_plan(system):
    s, store, remote, goals, _ = system
    row = goals.ingest("owner/work", remote.issue("owner/work", 1))
    store.control(row["id"], "resume")
    goals.configure([], "", "a")
    assert store.get(row["id"])["status"] == "paused"
    assert not goals.source(row["id"])["eligible"]
    with pytest.raises(ValueError):
        goals.ingest("owner/work", remote.issue("owner/work", 1))


def test_create_requires_shared_approval_and_has_idempotent_intent(system):
    s, store, remote, goals, _ = system
    key = str(uuid4())
    args = dict(
        agent="a",
        repo="owner/work",
        kind="create",
        payload={"title": "Goal", "body": "- [ ] Check"},
        request_id=key,
    )
    op = goals.submit(**args)
    assert op["status"] == "pending" and not remote.writes
    approve(s)
    goals.process_operations()
    op = goals.submit(**args)
    assert op["status"] == "succeeded"
    assert len([w for w in remote.writes if w[1].endswith("/issues")]) == 1
    assert store.get(op["result"]["responsibility"])["status"] == "paused"
    args["payload"]["title"] = "Different"
    with pytest.raises(ValueError, match="Request ID"):
        goals.submit(**args)


def test_link_migrates_legacy_preserves_id_and_report(system):
    s, store, remote, goals, _ = system
    row = store.create(agent="a", goal="Old goal", interval_seconds=300)
    store.update(row["id"], report="Historical report")
    op = goals.submit(
        agent="a",
        repo="owner/work",
        kind="link",
        payload={"number": 1, "responsibility": row["id"], "mode": "finite"},
        request_id=str(uuid4()),
    )
    assert not remote.writes
    approve(s)
    goals.process_operations()
    assert len(store.list()) == 1
    assert goals.source(row["id"])["mode"] == "finite"
    assert store.get(row["id"])["report"] == "Historical report"
    assert store.get(row["id"])["status"] == "paused"


def test_finite_completion_is_gated_and_recurring_cannot_close(system):
    s, store, remote, goals, _ = system
    row = goals.ingest("owner/work", remote.issue("owner/work", 1), mode="finite")
    op = goals.submit(
        agent="a",
        repo="owner/work",
        kind="complete",
        payload={"responsibility": row["id"]},
        request_id=str(uuid4()),
    )
    assert remote.data["owner/work", 1]["state"] == "open"
    approve(s)
    goals.process_operations()
    assert remote.data["owner/work", 1]["state"] == "closed"
    assert store.get(row["id"])["status"] == "paused"
    remote.data["owner/work", 2] = issue(2)
    recurring = goals.ingest("owner/work", remote.issue("owner/work", 2))
    with pytest.raises(ValueError, match="finite"):
        goals.submit(
            agent="a",
            repo="owner/work",
            kind="complete",
            payload={"responsibility": recurring["id"]},
            request_id=str(uuid4()),
        )


def test_coordinator_includes_issue_context_and_recurring_report_keeps_issue_open(
    system,
):
    s, store, remote, goals, _ = system
    row = goals.ingest("owner/work", remote.issue("owner/work", 1))
    store.control(row["id"], "resume")
    s.policy.set_enabled(True)
    calls = []
    worker = Coordinator(
        s, store, lambda: {"a"}, lambda r: calls.append(r) or {"report": "Checked"}
    )
    assert worker.acquire()
    try:
        worker.step()
        assert calls[0]["source"]["body"].startswith("- [ ]")
        approve(s)
        worker.step()
        assert store.get(row["id"])["report"] == "Checked"
        assert remote.data["owner/work", 1]["state"] == "open"
        assert not remote.writes
        worker.step()
        assert len(calls) == 1
    finally:
        worker.close()


def test_issue_edit_prevents_previously_approved_report_execution(system):
    s, store, remote, goals, _ = system
    row = goals.ingest("owner/work", remote.issue("owner/work", 1))
    store.control(row["id"], "resume")
    s.policy.set_enabled(True)
    worker = Coordinator(s, store, lambda: {"a"}, lambda r: {"report": "Old plan"})
    assert worker.acquire()
    try:
        worker.step()
        approve(s)
        remote.data["owner/work", 1]["body"] = "Changed"
        worker.step()
        assert store.get(row["id"])["status"] == "paused"
        assert not (worker.workspace(row["id"]) / "report.md").exists()
    finally:
        worker.close()


def test_pull_requests_invalid_agents_and_transfers_do_not_import(system):
    s, store, remote, goals, _ = system
    assert goals.ingest("owner/work", issue(labels=[{"name": "always-on"}])) is None
    with pytest.raises(ValueError):
        goals.ingest("owner/work", issue(pull_request={}))
    with pytest.raises(ValueError):
        goals.ingest(
            "owner/work", issue(html_url="https://github.com/other/work/issues/1")
        )
    assert not store.list()


@pytest.mark.parametrize(
    "value",
    [
        "../repo",
        "owner/repo/extra",
        "https://github.com/owner/repo",
        "owner/repo?x=y",
        "owner/..",
    ],
)
def test_repository_validation(value):
    with pytest.raises(ValueError):
        repository(value)


def test_repository_api_auth_agent_scope_and_linked_revision(system):
    s, store, remote, goals, agents = system

    def auth(authorization: str = Header(default="")):
        if authorization != "Bearer test":
            raise HTTPException(401)
        return AUTH

    app = FastAPI()
    app.include_router(create_repository_router(goals, auth))
    app.include_router(create_responsibility_router(store, s, auth, lambda: agents))
    app.include_router(create_router(s, auth))
    c = TestClient(app)
    headers = {"Authorization": "Bearer test"}
    assert c.get("/api/v1/autonomy/repositories").status_code == 401
    assert (
        c.get(
            "/api/v1/autonomy/repositories?agent=missing", headers=headers
        ).status_code
        == 400
    )
    row = goals.ingest("owner/work", remote.issue("owner/work", 1))
    assert (
        c.get("/api/v1/autonomy/responsibilities?agent=b", headers=headers).json()[
            "responsibilities"
        ]
        == []
    )
    rows = c.get("/api/v1/autonomy/responsibilities", headers=headers).json()[
        "responsibilities"
    ]
    assert rows[0]["source"]["url"].endswith("/issues/1")
    assert (
        c.put(
            "/api/v1/autonomy/responsibilities/" + row["id"] + "?agent=a",
            headers=headers,
            json={"goal": "Local override"},
        ).status_code
        == 400
    )
    assert (
        c.post(
            "/api/v1/autonomy/responsibilities/" + row["id"] + "/control?agent=b",
            headers=headers,
            json={"command": "resume"},
        ).status_code
        == 404
    )
    assert (
        c.post(
            "/api/v1/autonomy/repository-operations?agent=b",
            headers=headers,
            json={
                "agent": "a",
                "kind": "link",
                "number": 1,
                "request_id": str(uuid4()),
            },
        ).status_code
        == 400
    )


def test_normal_issue_queue_labels_are_not_dispatched_twice(system):
    s, store, remote, goals, _ = system
    remote.data["owner/work", 1]["labels"].append({"name": "a"})
    assert goals.ingest("owner/work", remote.issue("owner/work", 1)) is None
    assert not store.list()
    remote.data["owner/work", 1]["labels"].pop()
    row = goals.ingest("owner/work", remote.issue("owner/work", 1))
    store.control(row["id"], "resume")
    remote.data["owner/work", 1]["labels"].append({"name": "in-progress"})
    assert not goals.validate(row["id"])
    assert store.get(row["id"])["status"] == "paused"


def test_pending_operations_are_fair_and_rejection_makes_no_external_write(system):
    s, store, remote, goals, _ = system
    first = goals.submit(
        agent="a",
        repo="owner/work",
        kind="create",
        payload={"title": "First"},
        request_id=str(uuid4()),
    )
    second = goals.submit(
        agent="a",
        repo="owner/work",
        kind="create",
        payload={"title": "Second"},
        request_id=str(uuid4()),
    )
    approval = s.approvals.get(second["approval_id"], owner=OWNER)
    s.decide(approval["id"], AUTH, "approve_once", approval["fingerprint"])
    goals.process_operations()
    goals.process_operations()
    assert [w[2]["title"] for w in remote.writes if w[1].endswith("/issues")] == [
        "Second"
    ]
    approval = s.approvals.get(first["approval_id"], owner=OWNER)
    s.decide(approval["id"], AUTH, "reject", approval["fingerprint"])
    goals.process_operations()
    assert len(remote.writes) == 1


def test_repository_failure_status_visible_before_any_import(system):
    s, store, remote, goals, _ = system
    remote.fail = True
    goals.sync(True)
    assert goals.settings("a")["repositories"][0]["sync"]["error"]
    remote.fail = False
    goals.sync(True)
    assert not goals.settings("a")["repositories"][0]["sync"]["error"]


def test_issue_changed_during_planning_does_not_submit_old_plan(system):
    s, store, remote, goals, _ = system
    row = goals.ingest("owner/work", remote.issue("owner/work", 1))
    store.control(row["id"], "resume")
    s.policy.set_enabled(True)

    def planner(context):
        remote.data["owner/work", 1]["body"] = "Changed while planning"
        return {"report": "Stale response"}

    worker = Coordinator(s, store, lambda: {"a"}, planner)
    assert worker.acquire()
    try:
        worker.step()
        assert store.get(row["id"])["status"] == "paused"
        assert not s.approvals.list(owner=OWNER)
    finally:
        worker.close()
