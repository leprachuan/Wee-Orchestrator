import json
import pytest
from autonomy_policy import Action, Rule, PolicyStore, evaluate


def rule(**changes):
    fields = dict(
        id="r1",
        agent="wee-dev",
        operation="service.restart",
        host="dev",
        resource="wee",
        decision="allow",
        created_by="owner",
        created_at="2026-10-04T00:00:00Z",
        source_approval_id="a1",
    )
    return Rule(**(fields | changes))


def action(**changes):
    return Action(
        **(
            dict(
                agent="wee-dev", operation="service.restart", host="dev", resource="wee"
            )
            | changes
        )
    )


def test_default_disabled_and_unmatched_ask():
    assert evaluate(action(), [rule()])[0] == "deny"
    assert evaluate(action(), [], enabled=True)[0] == "ask"
    assert evaluate(action(), [rule()], enabled=True)[0] == "allow"


@pytest.mark.parametrize(
    "changes",
    [
        dict(agent="other"),
        dict(host="production"),
        dict(resource="other"),
        dict(operation="release.deploy"),
    ],
)
def test_scope_mismatches_require_approval(changes):
    assert evaluate(action(**changes), [rule()], enabled=True)[0] == "ask"


def test_deny_and_ask_precedence():
    assert (
        evaluate(action(), [rule(), rule(id="r2", decision="deny")], enabled=True)[0]
        == "deny"
    )
    assert (
        evaluate(action(), [rule(), rule(id="r2", decision="ask")], enabled=True)[0]
        == "ask"
    )


@pytest.mark.parametrize(
    "operation",
    [
        "shell.execute",
        "python.execute",
        "browser.execute",
        "delegate.execute",
        "unknown.execute",
    ],
)
def test_opaque_commands_cannot_inherit_grants(operation):
    assert (
        evaluate(
            action(operation=operation), [rule(operation=operation)], enabled=True
        )[0]
        == "ask"
    )


def test_path_scope_respects_boundaries():
    scope = rule(operation="file.write", resource="/workspace/wee", path_prefix=True)
    assert (
        evaluate(
            action(operation="file.write", resource="/workspace/wee/file"),
            [scope],
            enabled=True,
        )[0]
        == "allow"
    )
    assert (
        evaluate(
            action(operation="file.write", resource="/workspace/wee-other/file"),
            [scope],
            enabled=True,
        )[0]
        == "ask"
    )
    with pytest.raises(ValueError):
        action(operation="file.write", resource="/workspace/wee/../secret")


def test_fingerprint_binds_arguments_and_canonical_order():
    assert (
        action(arguments_json='{"b":2,"a":1}').fingerprint
        == action(arguments_json='{"a":1,"b":2}').fingerprint
    )
    assert (
        action(arguments_json='{"version":1}').fingerprint
        != action(arguments_json='{"version":2}').fingerprint
    )
    assert action(host="prod").fingerprint != action().fingerprint


def test_atomic_rule_persistence_and_revocation(tmp_path):
    store = PolicyStore(tmp_path / "policy.json")
    assert store.load()["enabled"] is False
    saved = store.add(
        actor="owner",
        approval_id="a1",
        agent="wee-dev",
        operation="service.restart",
        host="dev",
        resource="wee",
        decision="allow",
    )
    assert PolicyStore(store.path).load()["rules"] == [saved]
    revoked = store.revoke(saved.id, actor="owner")
    assert revoked.revoked_by == "owner"
    assert evaluate(action(), store.load()["rules"], enabled=True)[0] == "ask"
    assert store.load()["revision"] == 2
    assert store.revoke(saved.id, actor="owner") == revoked


def test_failed_atomic_write_preserves_rules(tmp_path, monkeypatch):
    store = PolicyStore(tmp_path / "policy.json")
    options = dict(
        actor="owner",
        approval_id="a1",
        agent="wee-dev",
        operation="service.restart",
        host="dev",
        resource="wee",
        decision="allow",
    )
    store.add(**options)
    previous = store.path.read_bytes()

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr("autonomy_policy.os.replace", fail)
    with pytest.raises(OSError):
        store.add(**options)
    assert store.path.read_bytes() == previous
    assert {item.name for item in tmp_path.iterdir()} == {
        "policy.json",
        "policy.json.lock",
    }


@pytest.mark.parametrize(
    "changes",
    [
        dict(host="*"),
        dict(agent="*"),
        dict(resource="*"),
        dict(path_prefix=True),
        dict(enabled="true"),
    ],
)
def test_invalid_rules_rejected(changes):
    with pytest.raises(ValueError):
        rule(**changes)


def test_corrupt_policies_fail_closed(tmp_path):
    store = PolicyStore(tmp_path / "policy.json")
    store.path.write_text("{bad")
    with pytest.raises(ValueError):
        store.load()


def test_invalid_feature_flag_never_enables_autonomy():
    assert evaluate(action(), [rule()], enabled="false")[0] == "deny"


def test_boolean_schema_version_is_invalid(tmp_path):
    store = PolicyStore(tmp_path / "policy.json")
    store.path.write_text(
        json.dumps(dict(version=True, revision=0, enabled=False, rules=[]))
    )
    with pytest.raises(ValueError):
        store.load()
