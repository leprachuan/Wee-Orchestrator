import json
from pathlib import Path

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from autonomy_coordinator import (
    Coordinator,
    ResponsibilityStore,
    create_responsibility_router,
)
from autonomy_heartbeat import Heartbeats, delay, revision, create_heartbeat_router
from autonomy_models import ModelPlanner
from autonomy_policy import Action
from autonomy_relay import RequestRelay, create_relay_router
from autonomy_service import ApprovalService, OWNER
from test_issue_538_repository_goals import system, approve

AUTH = {"auth_type": "shared_key"}


@pytest.fixture
def adaptive(tmp_path):
    now = [1000.0]
    service = ApprovalService(tmp_path)
    store = ResponsibilityStore(tmp_path, clock=lambda: now[0])
    hb = Heartbeats(store, service, lambda: {"a", "b"})
    store.heartbeats = hb
    store.request_relay = RequestRelay(hb)
    return service, store, hb, now


@pytest.mark.parametrize(
    "value,expected",
    [
        (1, 300),
        (300, 300),
        (14400, 14400),
        (999999, 14400),
        (-1, 300),
        ("five", 3600),
        (None, 3600),
        (True, 3600),
    ],
)
def test_delay_bounds_and_invalid_fallback(value, expected):
    assert delay(value) == expected


def active_goal(
    adaptive,
    agent="a",
    allowed="Write reports and notes to workspace.",
    ask="Ask before deployment.",
):
    service, store, hb, now = adaptive
    row = store.create(agent=agent, goal="Track useful work", interval_seconds=300)
    hb.instructions(row["id"], allowed, ask)
    store.control(row["id"], "resume")
    service.policy.set_enabled(True)
    return store.get(row["id"])


def test_one_schedule_all_goals_completion_clock_and_restart(adaptive):
    service, store, hb, now = adaptive
    first = active_goal(adaptive)
    second = active_goal(adaptive)
    calls = []

    def planner(row):
        calls.append(row)
        now[0] += 12
        return {
            "report": "Reviewed both goals",
            "next_delay_seconds": 600,
            "scheduling_reason": "Two actionable tasks",
        }

    worker = Coordinator(service, store, lambda: {"a", "b"}, planner)
    assert worker.acquire()
    try:
        worker.step()
        worker.step()
        assert len(calls) == 1
        assert {g["id"] for g in calls[0]["agent_context"]["active_goals"]} == {
            first["id"],
            second["id"],
        }
        assert hb.get("a")["next_at"] == 1612
        assert hb.get("b")["delay_seconds"] == 14400
        restarted = Heartbeats(store, service, lambda: {"a", "b"})
        assert restarted.get("a")["next_at"] == 1612
        now[0] = 1612
        worker.step()
        assert len(calls) == 2
    finally:
        worker.close()


def test_full_permission_text_is_model_context(adaptive):
    row = active_goal(
        adaptive, allowed="notes " + ("x" * 4000), ask="x" * 4000 + " deployment"
    )
    context = adaptive[2].context("a")["active_goals"][0]
    assert context["allowed_autonomously"] == row["autonomous_instructions"]
    assert context["ask_permission_first"] == row["permission_required_instructions"]


def test_permission_citation_ask_first_and_negation(adaptive):
    row = active_goal(adaptive)
    hb = adaptive[2]
    step = {
        "kind": "save_note",
        "permission": "autonomous",
        "quote": row["autonomous_instructions"],
    }
    assert hb.permission(row, step) == "allow"
    assert hb.permission(row, {**step, "quote": "invented permission"}) == "ask"
    row["permission_required_instructions"] = "Ask before writing notes"
    assert hb.permission(row, step) == "ask"
    row["permission_required_instructions"] = ""
    row["autonomous_instructions"] = "Never write notes"
    assert hb.permission(row, {**step, "quote": "Never write notes"}) == "ask"


def test_save_note_progress_executes_once_without_extra_prompt(adaptive):
    service, store, hb, now = adaptive
    row = active_goal(adaptive)
    hb.propose(
        row,
        {
            "actions": [
                {
                    "kind": "save_note",
                    "payload": {"content": "Verified result"},
                    "permission": "autonomous",
                    "instruction_quote": row["autonomous_instructions"],
                }
            ]
        },
    )
    worker = Coordinator(service, store, lambda: {"a"}, lambda r: {"report": "ok"})
    hb.process(worker)
    hb.process(worker)
    assert (
        worker.workspace(row["id"]) / "progress.md"
    ).read_text() == "Verified result"
    rows = service.approvals.list(owner=OWNER)
    assert len(rows) == 1 and rows[0]["status"] == "succeeded"


def test_instruction_edit_invalidates_work_and_pauses(adaptive):
    service, store, hb, now = adaptive
    row = active_goal(adaptive)
    hb.propose(
        row,
        {
            "actions": [
                {
                    "kind": "save_note",
                    "payload": {"content": "Old intent"},
                    "permission": "autonomous",
                    "instruction_quote": row["autonomous_instructions"],
                }
            ],
            "steering_questions": ["Which deadline?"],
        },
    )
    old = hb.steering()[0]
    hb.instructions(row["id"], "", "Ask for every action")
    assert store.get(row["id"])["status"] == "paused"
    assert not hb.steering()
    with pytest.raises(ValueError):
        hb.answer(old["id"], "Tomorrow", old["revision"])
    worker = Coordinator(service, store, lambda: {"a"}, lambda r: {"report": "ok"})
    hb.process(worker)
    assert not (worker.workspace(row["id"]) / "progress.md").exists()


def test_steering_first_response_wins_and_wakes_with_floor(adaptive):
    service, store, hb, now = adaptive
    row = active_goal(adaptive)
    hb.schedule("a", 14400, "Waiting")
    hb.ask(row["id"], "Which deadline?", "question")
    request = hb.steering()[0]
    now[0] = 1010
    assert hb.answer(request["id"], "Friday", request["revision"])["won"]
    assert not hb.answer(request["id"], "Monday", request["revision"])["won"]
    assert hb.get("a")["next_at"] == 1300
    assert hb.context("a")["steering_answers"][0]["answer"] == "Friday"


def test_goal_allow_cannot_override_global_ask_or_deny(adaptive):
    service, store, hb, now = adaptive
    row = active_goal(adaptive)
    action = Action("a", "file.write", "api-host", "/safe/report.md")
    service.policy.add(
        actor="test",
        approval_id="test",
        agent="a",
        operation="file.write",
        host="api-host",
        resource="/safe/report.md",
        decision="ask",
    )
    result = service.execute(
        action,
        summary="Test request",
        responsibility=row["id"],
        intent_key="ask",
        adapter=lambda a: pytest.fail("must ask"),
        goal_decision="allow",
    )
    assert result["status"] == "pending"
    service.policy.add(
        actor="test",
        approval_id="test",
        agent="a",
        operation="file.write",
        host="api-host",
        resource="/safe/report.md",
        decision="deny",
    )
    assert (
        service.execute(
            action,
            summary="Test request",
            responsibility=row["id"],
            intent_key="deny",
            adapter=lambda a: pytest.fail("must deny"),
            goal_decision="allow",
        )["status"]
        == "denied"
    )


def test_repository_instruction_mirror_is_approved_and_revision_bound(system):
    service, store, remote, goals, agents = system
    hb = Heartbeats(store, service, lambda: agents)
    store.heartbeats = hb
    goals.sync(True)
    row = store.list()[0]
    hb.instructions(row["id"], "Write notes", "Ask before issue comments")
    goals.process_operations()
    assert not remote.writes
    approve(service)
    goals.process_operations()
    body = remote.data["owner/work", 1]["body"]
    assert "<!-- wee-autonomy-remit -->" in body and "Ask before issue comments" in body
    assert "- [ ] Check health" in body
    assert store.get(row["id"])["status"] == "paused"


def test_relay_durable_first_winner_origin_validates(adaptive, tmp_path):
    service, store, hb, now = adaptive
    row = active_goal(adaptive)
    hb.ask(row["id"], "Which goal first?", "question")
    source = store.request_relay
    hubstore = ResponsibilityStore(tmp_path / "hub")
    hubhb = Heartbeats(hubstore, ApprovalService(tmp_path / "hub"), lambda: {"a"})
    hub = RequestRelay(hubhb)
    hub.publish(source.identity, source.local())
    question = hub.inbox()["peers"][0]["steering"][0]
    body = {
        "kind": "steering",
        "answer": "Service reliability",
        "revision": question["revision"],
    }
    assert hub.decide(source.identity, question["id"], body, AUTH)["won"]
    assert not hub.decide(
        source.identity, question["id"], {**body, "answer": "Other"}, AUTH
    )["won"]
    assert hub.inbox()["peers"][0]["steering"][0]["status"] == "awaiting_origin"
    restarted = RequestRelay(hubhb)
    queued = restarted.pending(source.identity)[0]
    result = source.decide(
        source.identity, queued["request_id"], json.loads(queued["payload"]), AUTH
    )
    assert result["won"]
    restarted.ack(source.identity, queued["id"], result)
    hub.publish(source.identity, source.local())
    assert not hub.inbox()["peers"][0]["steering"]
    assert not restarted.pending(source.identity)


def test_inbox_is_authenticated_unscoped_and_origin_checked(adaptive):
    service, store, hb, now = adaptive
    row = active_goal(adaptive, agent="b")
    hb.ask(row["id"], "Question for b", "q")

    def auth(authorization: str = Header(default="")):
        if authorization != "Bearer test":
            raise HTTPException(401)
        return AUTH

    app = FastAPI()
    app.include_router(create_heartbeat_router(hb, auth))
    app.include_router(create_relay_router(store.request_relay, auth))
    client = TestClient(app)
    assert client.get("/api/v1/autonomy/inbox").status_code == 401
    inbox = client.get(
        "/api/v1/autonomy/inbox?agent=a", headers={"Authorization": "Bearer test"}
    ).json()
    assert inbox["steering"][0]["agent"] == "b"
    response = client.put(
        "/api/v1/autonomy/relay/" + inbox["instance_id"],
        json={k: inbox[k] for k in ["instance_id", "approvals", "steering"]},
        headers={"Authorization": "Bearer test"},
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "actions",
    [
        [{"kind": "shell", "payload": {}, "permission": "autonomous"}],
        [{"kind": "save_note", "goal_id": {}, "payload": {"content": "hi"}}],
        [
            {
                "kind": "issue_checklist",
                "payload": {"items": [{"text": "health", "completed": "yes"}]},
            }
        ],
    ],
)
def test_model_rejects_unsupported_or_malformed_actions(actions):
    with pytest.raises((ValueError, TypeError)):
        ModelPlanner._verify(
            json.dumps({"report": "ok", "actions": actions}), adaptive=True
        )


def test_global_approval_reject_and_stale_fingerprint(adaptive):
    service, store, hb, now = adaptive
    row = active_goal(adaptive)
    action = Action("a", "file.write", "api-host", "/safe/file.md")
    outcome = service.execute(
        action,
        summary="Test request",
        responsibility=row["id"],
        intent_key="request",
        adapter=lambda a: None,
    )
    approval = store.request_relay.local()["approvals"][0]
    with pytest.raises(ValueError):
        store.request_relay.decide(
            store.request_relay.identity,
            approval["id"],
            {"kind": "approval", "decision": "approve_once", "fingerprint": "stale"},
            AUTH,
        )
    result = store.request_relay.decide(
        store.request_relay.identity,
        approval["id"],
        {
            "kind": "approval",
            "decision": "reject",
            "fingerprint": approval["fingerprint"],
        },
        AUTH,
    )
    assert result["request"]["status"] == "rejected"
    assert not store.request_relay.local()["approvals"]


def test_instruction_change_removes_old_approval(adaptive):
    service, store, hb, now = adaptive
    row = active_goal(adaptive)
    action = Action("a", "file.write", "api-host", "/safe/file.md")
    service.execute(
        action,
        summary="Test request",
        responsibility=row["id"],
        intent_key="old",
        adapter=lambda a: None,
    )
    approval = store.request_relay.local()["approvals"][0]
    hb.instructions(row["id"], "", "Ask for all actions")
    assert not store.request_relay.local()["approvals"]
    assert service.approvals.get(approval["id"], owner=OWNER)["status"] == "cancelled"


def test_push_all_registered_devices_quiet_dedup_and_retry(adaptive):
    from autonomy_push import InboxPush

    service, store, hb, now = adaptive
    row = active_goal(adaptive)
    hb.ask(row["id"], "Choose priority", "question")
    push = InboxPush(store.request_relay)
    push.register("ios-one", "ab" * 32, "sandbox")
    push.register("ios-two", "cd" * 32, "production")
    sent = []
    push.tick(lambda device, request, key: sent.append((device["id"], request)))
    push.tick(lambda *args: pytest.fail("unchanged request must stay quiet"))
    assert {r[0] for r in sent} == {"ios-one", "ios-two"}
    assert all(r[1]["kind"] == "steering" for r in sent)
    question = hb.steering()[0]
    hb.answer(question["id"], "Today", question["revision"])
    push.tick(lambda *args: pytest.fail("resolved request must stay quiet"))
    with pytest.raises(ValueError):
        push.register("invalid", "not-a-token", "sandbox")


def test_issue_remit_edit_is_imported_paused_before_any_grant(system):
    service, store, remote, goals, agents = system
    hb = Heartbeats(store, service, lambda: agents)
    store.heartbeats = hb
    goals.sync(True)
    row = store.list()[0]
    store.control(row["id"], "resume")
    remote.data["owner/work", 1][
        "body"
    ] += "\n\n<!-- wee-autonomy-remit -->\n## Allowed autonomously\nWrite notes\n## Ask permission first\nAsk before comments\n<!-- /wee-autonomy-remit -->"
    goals.sync(True)
    current = store.get(row["id"])
    assert current["status"] == "paused"
    assert current["autonomous_instructions"] == "Write notes"
    assert current["permission_required_instructions"] == "Ask before comments"


def test_old_pending_run_cannot_replace_newer_agent_cadence(adaptive):
    service, store, hb, now = adaptive
    hb.schedule("a", 300, "First run", generation="first")
    now[0] = 1300
    hb.schedule("a", 14400, "Newer run", generation="second")
    now[0] = 1310
    hb.schedule("a", 300, "Late approval from first", generation="first", finish=True)
    assert hb.get("a")["next_at"] == 15700
    assert hb.get("a")["reason"] == "Newer run"


def test_apns_jwt_uses_ephemeral_key_and_retries_failed_delivery(
    adaptive, tmp_path, monkeypatch
):
    import base64
    import autonomy_push
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    from autonomy_push import InboxPush

    key = ec.generate_private_key(ec.SECP256R1())
    keyfile = tmp_path / "ephemeral-apns-key.pem"
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv("WEE_APNS_KEY_FILE", str(keyfile))
    monkeypatch.setenv("WEE_APNS_KEY_ID", "test-key")
    monkeypatch.setenv("WEE_APNS_TEAM_ID", "test-team")
    token = InboxPush.jwt()
    header, claims, signature = token.split(".")

    def decode(value):
        return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))

    assert json.loads(decode(header))["alg"] == "ES256"
    assert json.loads(decode(claims))["iss"] == "test-team"
    sig = decode(signature)
    der = utils.encode_dss_signature(
        int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
    )
    key.public_key().verify(
        der, (header + "." + claims).encode(), ec.ECDSA(hashes.SHA256())
    )
    service, store, hb, now = adaptive
    row = active_goal(adaptive)
    hb.ask(row["id"], "Deadline?", "retry")
    push = InboxPush(store.request_relay)
    push.register("device", "ab" * 32, "sandbox")
    push.tick(lambda *args: (_ for _ in ()).throw(OSError("Transport unavailable")))
    sent = []
    push.tick(lambda *args: pytest.fail("Backoff must prevent immediate retry"))
    with push.db._transaction() as db:
        db.execute("UPDATE inbox_push_delivery SET next_at=0")
    push.tick(lambda *args: sent.append(True))
    assert sent == [True]
