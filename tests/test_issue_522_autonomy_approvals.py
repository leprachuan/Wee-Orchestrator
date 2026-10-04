from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading
import pytest
from autonomy_policy import Action
from autonomy_approvals import ApprovalStore, ApprovalConflict


def action(**fields):
    return Action(
        **(
            dict(
                agent="wee-dev", operation="service.restart", host="dev", resource="wee"
            )
            | fields
        )
    )


def create(store, **fields):
    return store.create(
        action(),
        **(
            dict(owner="owner", responsibility="keep-healthy", intent_key="restart-1")
            | fields
        ),
    )


def resolve(store, request, **fields):
    return store.resolve(
        request["id"],
        **(
            dict(
                owner="owner",
                actor="mac-user",
                decision="approve_once",
                fingerprint=request["fingerprint"],
            )
            | fields
        ),
    )


def test_request_and_decision_survive_restart(tmp_path):
    path = tmp_path / "approvals.sqlite"
    store = ApprovalStore(path)
    request = create(store)
    assert ApprovalStore(path).get(request["id"], owner="owner") == request
    record, won = resolve(store, request)
    assert won and record["status"] == "approved"
    assert (
        ApprovalStore(path).get(request["id"], owner="owner")["decided_by"]
        == "mac-user"
    )
    assert [e["kind"] for e in store.events(owner="owner")] == ["created", "approved"]


def test_first_decision_wins_across_store_connections(tmp_path):
    path = tmp_path / "approvals.sqlite"
    request = create(ApprovalStore(path))
    stores = [ApprovalStore(path) for _ in range(3)]
    barrier = threading.Barrier(3)

    def decide(index):
        barrier.wait()
        return resolve(
            stores[index],
            request,
            actor=["mac", "ios", "web"][index],
            decision=["approve_once", "reject", "revise"][index],
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(decide, range(3)))
    assert sum(won for _, won in results) == 1
    assert len({record["status"] for record, _ in results}) == 1
    assert len(stores[0].events(owner="owner")) == 2


def test_intent_retries_do_not_create_new_requests(tmp_path):
    store = ApprovalStore(tmp_path / "db")
    request = create(store)
    assert create(store) == request
    assert len(store.events(owner="owner")) == 1
    with pytest.raises(ApprovalConflict):
        store.create(
            action(host="prod"),
            owner="owner",
            responsibility="keep-healthy",
            intent_key="restart-1",
        )
    with pytest.raises(ApprovalConflict):
        create(store, responsibility="other")
    assert create(store, owner="other")["id"] != request["id"]


@pytest.mark.parametrize("method", ["get", "resolve", "cancel", "claim"])
def test_wrong_owner_cannot_read_or_change_request(tmp_path, method):
    store = ApprovalStore(tmp_path / "db")
    request = create(store)
    with pytest.raises(KeyError):
        if method == "get":
            store.get(request["id"], owner="other")
        elif method == "resolve":
            resolve(store, request, owner="other")
        elif method == "cancel":
            store.cancel(request["id"], owner="other", actor="web")
        else:
            store.claim(
                request["id"], action(), owner="other", responsibility="keep-healthy"
            )
    assert store.events(owner="other") == []
    assert store.get(request["id"], owner="owner")["status"] == "pending"


def test_fingerprint_mismatch_does_not_approve(tmp_path):
    store = ApprovalStore(tmp_path / "db")
    request = create(store)
    with pytest.raises(ApprovalConflict):
        resolve(store, request, fingerprint=action(host="prod").fingerprint)
    assert store.get(request["id"], owner="owner")["status"] == "pending"


@pytest.mark.parametrize("approved", [False, True])
def test_expiry_prevents_decision_and_execution(tmp_path, approved):
    now = [100]
    store = ApprovalStore(tmp_path / "db", clock=lambda: now[0])
    request = create(store, ttl_seconds=10)
    if approved:
        resolve(store, request)
    now[0] = 110
    record, won = resolve(store, request)
    assert not won and record["status"] == "expired"
    assert not store.claim(
        request["id"], action(), owner="owner", responsibility="keep-healthy"
    )
    assert [e["kind"] for e in store.events(owner="owner")].count("expired") == 1


def test_claim_is_single_use_across_restart(tmp_path):
    path = tmp_path / "db"
    store = ApprovalStore(path)
    request = create(store)
    resolve(store, request)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(
            pool.map(
                lambda _: ApprovalStore(path).claim(
                    request["id"],
                    action(),
                    owner="owner",
                    responsibility="keep-healthy",
                ),
                range(4),
            )
        )
    assert sum(claimed) == 1
    assert not ApprovalStore(path).claim(
        request["id"], action(), owner="owner", responsibility="keep-healthy"
    )


def test_changed_arguments_invalidate_approved_request(tmp_path):
    store = ApprovalStore(tmp_path / "db")
    request = create(store)
    resolve(store, request)
    assert not store.claim(
        request["id"],
        action(arguments_json='{"version":"new"}'),
        owner="owner",
        responsibility="keep-healthy",
    )
    assert store.get(request["id"], owner="owner")["status"] == "invalidated"
    assert store.events(owner="owner")[-1]["kind"] == "invalidated"


def test_claim_cannot_switch_responsibility(tmp_path):
    store = ApprovalStore(tmp_path / "db")
    request = create(store)
    resolve(store, request)
    with pytest.raises(ApprovalConflict):
        store.claim(request["id"], action(), owner="owner", responsibility="other")
    assert store.get(request["id"], owner="owner")["status"] == "approved"


@pytest.mark.parametrize(
    "decision,status", [("reject", "rejected"), ("revise", "revision_requested")]
)
def test_rejection_and_revision_do_not_execute(tmp_path, decision, status):
    store = ApprovalStore(tmp_path / "db")
    request = create(store)
    record, won = resolve(store, request, decision=decision)
    assert won and record["status"] == status
    assert not store.claim(
        request["id"], action(), owner="owner", responsibility="keep-healthy"
    )


def test_cancel_prevents_late_decision_and_claim(tmp_path):
    store = ApprovalStore(tmp_path / "db")
    request = create(store)
    resolve(store, request)
    record, won = store.cancel(request["id"], owner="owner", actor="web")
    assert won and record["status"] == "cancelled"
    assert not resolve(store, request)[1]
    assert not store.claim(
        request["id"], action(), owner="owner", responsibility="keep-healthy"
    )


def test_event_cursor_and_payload_do_not_include_arguments(tmp_path):
    store = ApprovalStore(tmp_path / "db")
    request = store.create(
        action(arguments_json='{"private":"synthetic-test-marker"}'),
        owner="owner",
        responsibility="r",
        intent_key="k",
    )
    resolve(store, request)
    first = store.events(owner="owner", limit=1)[0]
    remaining = store.events(owner="owner", after=first["sequence"])
    assert [event["kind"] for event in remaining] == ["approved"]
    assert "synthetic-test-marker" not in json.dumps(request) + json.dumps(remaining)
    assert b"synthetic-test-marker" not in store.path.read_bytes()
    assert store.path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("ttl", [0, -1, True, 86401, 1.5])
def test_invalid_expiry_is_rejected(tmp_path, ttl):
    with pytest.raises(ValueError):
        create(ApprovalStore(tmp_path / "db"), ttl_seconds=ttl)


def test_always_allow_not_exposed_before_rule_transaction(tmp_path):
    store = ApprovalStore(tmp_path / "db")
    request = create(store)
    with pytest.raises(ValueError):
        resolve(store, request, decision="approve_always")
    assert store.get(request["id"], owner="owner")["status"] == "pending"


def test_existing_public_database_rejected(tmp_path):
    path = tmp_path / "db"
    path.touch(mode=0o644)
    with pytest.raises(ValueError):
        ApprovalStore(path)
