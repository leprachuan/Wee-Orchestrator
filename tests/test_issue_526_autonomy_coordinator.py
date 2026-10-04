import json
import pytest
from autonomy_service import ApprovalService, OWNER
from autonomy_coordinator import Coordinator, ResponsibilityStore

AUTH = {"auth_type": "shared_key"}


def setup(tmp_path):
    service = ApprovalService(tmp_path)
    store = ResponsibilityStore(tmp_path)
    row = store.create(agent="a", goal="Watch and draft a report", interval_seconds=300)
    calls = []

    def plan(row):
        calls.append(row["id"])
        return {"report": "A bounded report"}

    worker = Coordinator(service, store, lambda: {"a"}, plan)
    assert worker.acquire()
    return service, store, row, worker, calls


def test_opt_in_shared_approval_restart_no_duplicate_model_or_effect(tmp_path):
    s, store, row, w, calls = setup(tmp_path)
    w.step()
    assert not calls  # paused by default
    store.control(row["id"], "resume")
    s.policy.set_enabled(True)
    w.step()
    assert len(calls) == 1 and store.get(row["id"])["phase"] == "waiting"
    approval = s.approvals.list(owner=OWNER)[0]
    assert approval["status"] == "pending"
    w.close()
    other = Coordinator(
        s,
        store,
        lambda: {"a"},
        lambda row: pytest.fail("Must reuse pending checkpoint"),
    )
    assert other.acquire()
    other.step()
    s.decide(approval["id"], AUTH, "approve_once", approval["fingerprint"])
    other.step()
    other.step()
    assert store.get(row["id"])["phase"] == "idle"
    assert (other.workspace(row["id"]) / "report.md").read_text() == "A bounded report"
    assert len(calls) == 1
    other.close()


def test_pause_and_cancel_prevent_pending_execution(tmp_path):
    s, store, row, w, calls = setup(tmp_path)
    store.control(row["id"], "resume")
    s.policy.set_enabled(True)
    w.step()
    r = s.approvals.list(owner=OWNER)[0]
    s.decide(r["id"], AUTH, "approve_once", r["fingerprint"])
    store.control(row["id"], "pause")
    w.step()
    assert not (w.workspace(row["id"]) / "report.md").exists()
    store.control(row["id"], "cancel")
    with pytest.raises(ValueError):
        store.control(row["id"], "resume")
    w.close()


def test_exclusive_worker_and_interrupted_runs_need_reconciliation(tmp_path):
    s, store, row, w, calls = setup(tmp_path)
    other = Coordinator(s, store, lambda: {"a"}, lambda r: None)
    assert not other.acquire()
    store.update(row["id"], phase="running")
    w.close()
    assert other.acquire()
    assert store.get(row["id"])["phase"] == "attention"
    store.control(row["id"], "resume")
    other.step()
    assert not calls
    store.control(row["id"], "reconcile")
    assert (
        store.get(row["id"])["status"] == "paused"
        and store.get(row["id"])["phase"] == "idle"
    )
    other.close()


def test_symlink_destination_is_replaced_not_followed(tmp_path):
    s, store, row, w, calls = setup(tmp_path)
    store.control(row["id"], "resume")
    s.policy.set_enabled(True)
    outside = tmp_path / "outside"
    outside.write_text("must remain")
    (w.workspace(row["id"]) / "report.md").symlink_to(outside)
    w.step()
    r = s.approvals.list(owner=OWNER)[0]
    s.decide(r["id"], AUTH, "approve_once", r["fingerprint"])
    w.step()
    assert outside.read_text() == "must remain"
    assert not (w.workspace(row["id"]) / "report.md").is_symlink()
    w.close()


def test_missing_agent_and_bad_plan_fail_closed(tmp_path):
    s, store, row, w, calls = setup(tmp_path)
    store.control(row["id"], "resume")
    s.policy.set_enabled(True)
    w.planner = lambda row: {"command": "rm something"}
    w.step()
    assert store.get(row["id"])["phase"] == "attention" and not s.approvals.list(
        owner=OWNER
    )
    w.close()


def test_symlink_workspace_parent_fails_closed(tmp_path):
    s, store, row, w, calls = setup(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "workspaces").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(OSError):
        w.workspace(row["id"])
    assert not list(elsewhere.iterdir())
    w.close()


def test_revision_during_planning_cannot_save_stale_plan(tmp_path):
    s, store, row, w, calls = setup(tmp_path)
    store.control(row["id"], "resume")
    s.policy.set_enabled(True)

    def revise(current):
        store.revise(current["id"], "Revised responsibility")
        store.control(current["id"], "resume")
        return {"report": "Stale result"}

    w.planner = revise
    w.step()
    current = store.get(row["id"])
    assert current["goal"] == "Revised responsibility" and current["phase"] == "idle"
    assert not s.approvals.list(owner=OWNER)
    w.close()
