import pytest
from test_issue_533_agent_autonomy import client
from autonomy_coordinator import ResponsibilityStore


def test_cancelled_goal_delete_persists_and_is_scoped(tmp_path):
    api, service, store = client(tmp_path)
    row = store.create(agent="a", goal="delete test", interval_seconds=300)
    path = "/api/v1/autonomy/responsibilities/" + row["id"]
    assert api.delete(path + "?agent=a").status_code == 400
    store.control(row["id"], "cancel")
    assert api.delete(path + "?agent=b").status_code == 404
    with pytest.raises(KeyError): store.delete(row["id"], owner="other")
    assert len(store.list()) == 1
    assert api.delete(path + "?agent=a").status_code == 200
    assert api.delete(path + "?agent=a").status_code == 200
    assert api.get("/api/v1/autonomy/responsibilities?agent=a").json() == {"responsibilities": []}
    reopened = ResponsibilityStore(tmp_path)
    assert reopened.list() == []
    assert reopened.get(row["id"])["goal"] == "delete test"
    with pytest.raises(ValueError): reopened.control(row["id"], "resume")


def test_legacy_database_migration(tmp_path):
    store = ResponsibilityStore(tmp_path)
    row = store.create(agent="a", goal="legacy", interval_seconds=300)
    store.control(row["id"], "cancel")
    with store.db._transaction() as db:
        db.execute("ALTER TABLE responsibilities DROP COLUMN deleted_at")
    migrated = ResponsibilityStore(tmp_path)
    assert len(migrated.list()) == 1
    migrated.delete(row["id"])
    assert migrated.list() == []
