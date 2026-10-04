import json
import pytest
from autonomy_models import (
    ModelPlanner,
    ModelSettings,
    ModelConfig,
    ModelWaiting,
    BudgetExceeded,
)
from autonomy_service import ApprovalService, OWNER
from autonomy_coordinator import ResponsibilityStore, Coordinator


def setup(tmp_path, completion):
    s = ApprovalService(tmp_path)
    store = ResponsibilityStore(tmp_path)
    settings = ModelSettings(tmp_path)
    row = store.create(
        agent="a", goal="Review current task queue", interval_seconds=300
    )
    store.control(row["id"], "resume")
    s.policy.set_enabled(True)
    planner = ModelPlanner(
        s,
        store,
        settings,
        completion=completion,
        observations=lambda: {"queued_tasks": 2},
    )
    w = Coordinator(s, store, lambda: {"a"}, planner)
    assert w.acquire()
    return s, store, settings, planner, w, row


def test_routine_only_and_zero_extra_calls_waiting_approval(tmp_path):
    calls = []

    def complete(model, messages, tokens):
        calls.append(model)
        return '{"report":"Queue reviewed"}', {"total_tokens": 100}

    s, store, cfg, p, w, row = setup(tmp_path, complete)
    w.step()
    w.step()
    w.step()
    assert calls == [ModelConfig().routine_model]
    assert p.usage()["requests"] == 1 and p.usage()["actual_tokens"] == 100
    approval = s.approvals.list(owner=OWNER)[0]
    assert json.loads(approval["preview_json"])["details"] == "Queue reviewed"
    s.decide(
        approval["id"],
        {"auth_type": "shared_key"},
        "approve_once",
        approval["fingerprint"],
    )
    w.step()
    assert store.get(row["id"])["report"] == "Queue reviewed" and len(calls) == 1
    w.close()


def test_only_evidenced_escalation_waits_shared_approval_and_returns_to_routine(
    tmp_path,
):
    calls = []

    def complete(model, messages, tokens):
        calls.append(model)
        return (
            "not json" if len(calls) <= 2 else '{"report":"Verified stronger report"}'
        ), {"total_tokens": 100}

    s, store, cfg, p, w, row = setup(tmp_path, complete)
    cfg.save(
        {**vars(ModelConfig()), "escalation_models": ["openrouter/openai/gpt-4.1"]}
    )
    w.step()
    assert store.get(row["id"])["phase"] == "model_waiting" and len(calls) == 2
    w.step()
    assert len(calls) == 2
    request = s.approvals.list(owner=OWNER)[0]
    assert json.loads(request["scope_json"])["operation"] == "model.escalate"
    s.decide(
        request["id"],
        {"auth_type": "shared_key"},
        "approve_once",
        request["fingerprint"],
    )
    w.step()
    assert (
        calls[-1] == "openrouter/openai/gpt-4.1"
        and store.get(row["id"])["phase"] == "waiting"
    )
    report = [
        r
        for r in s.approvals.list(owner=OWNER)
        if json.loads(r["scope_json"])["operation"] == "file.write"
    ][0]
    s.decide(
        report["id"], {"auth_type": "shared_key"}, "approve_once", report["fingerprint"]
    )
    w.step()
    store.update(row["id"], next_at=0)
    w.step()
    assert calls[-1] == ModelConfig().routine_model
    w.close()


def test_budget_reserved_before_network_and_unknown_usage_kept(tmp_path):
    calls = []

    def complete(*args):
        calls.append(1)
        return '{"report":"ok"}', {}

    s, store, cfg, p, w, row = setup(tmp_path, complete)
    cfg.save({**vars(ModelConfig()), "daily_requests": 1})
    w.step()
    assert p.usage()["unknown_usage"] == 1
    second = store.create(agent="a", goal="Another run", interval_seconds=300)
    store.control(second["id"], "resume")
    w.step()
    assert len(calls) == 1 and store.get(second["id"])["phase"] == "attention"
    assert "budget" in store.get(second["id"])["error"]
    w.close()


def test_no_escalation_for_valid_routine_or_without_allowlist(tmp_path):
    calls = []

    def invalid(*args):
        calls.append(1)
        return "broken", {}

    s, store, cfg, p, w, row = setup(tmp_path, invalid)
    w.step()
    assert len(calls) == 2 and store.get(row["id"])["phase"] == "attention"
    assert not s.approvals.list(owner=OWNER)
    w.close()


def test_removed_escalation_allowlist_blocks_approved_request(tmp_path):
    calls = []

    def complete(*args):
        calls.append(1)
        return "invalid", {}

    s, store, cfg, p, w, row = setup(tmp_path, complete)
    cfg.save(
        {**vars(ModelConfig()), "escalation_models": ["openrouter/openai/gpt-4.1"]}
    )
    w.step()
    r = s.approvals.list(owner=OWNER)[0]
    s.decide(r["id"], {"auth_type": "shared_key"}, "approve_once", r["fingerprint"])
    cfg.save(vars(ModelConfig()))
    w.step()
    assert len(calls) == 2 and store.get(row["id"])["phase"] == "attention"
    w.close()


@pytest.mark.parametrize(
    "fields",
    [
        {"routine_model": "unqualified-luna"},
        {"daily_requests": True},
        {"max_requests_per_run": 4},
        {"escalation_models": ["openrouter/openai/gpt-4.1-mini"]},
        {"max_output_tokens": 100000},
    ],
)
def test_invalid_model_budget_configuration_rejected(fields):
    with pytest.raises(ValueError):
        ModelConfig(**{**vars(ModelConfig()), **fields})
