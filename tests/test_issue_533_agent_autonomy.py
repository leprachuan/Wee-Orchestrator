import json
import threading
from dataclasses import asdict
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from autonomy_models import ModelConfig, ModelSettings, ModelPlanner, BudgetExceeded, create_model_router
from autonomy_service import ApprovalService, create_router, OWNER
from autonomy_coordinator import ResponsibilityStore, create_responsibility_router
from autonomy_policy import Action


def fixture(tmp_path):
    service = ApprovalService(tmp_path)
    store = ResponsibilityStore(tmp_path)
    settings = ModelSettings(tmp_path)
    planner = ModelPlanner(service, store, settings, completion=lambda *args: ('{"report":"done"}', {"total_tokens": 1}))
    return service, store, settings, planner


def test_legacy_settings_and_concurrent_agent_updates(tmp_path):
    cfg = ModelSettings(tmp_path)
    cfg.path.write_text(json.dumps({**asdict(ModelConfig()), 'daily_requests': 7}))
    assert cfg.load('a').daily_requests == 7
    threads = [threading.Thread(target=cfg.save, args=({**asdict(ModelConfig()), 'routine_runtime': 'codex', 'routine_model': f'model-{name}'}, name)) for name in ('a', 'b')]
    for t in threads: t.start()
    for t in threads: t.join()
    assert cfg.load('a').routine_model == 'model-a'
    assert cfg.load('b').routine_model == 'model-b'
    assert cfg.load().daily_requests == 7
    saved = json.loads(cfg.path.read_text())
    assert saved['version'] == 2 and set(saved['agents']) == {'a', 'b'}
    assert cfg.path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError): cfg.load('../b')


def test_worker_models_and_daily_budgets_are_per_agent(tmp_path):
    service, store, cfg, planner = fixture(tmp_path)
    a = store.create(agent='a', goal='a', interval_seconds=300)
    b = store.create(agent='b', goal='b', interval_seconds=300)
    for row in (a, b): store.control(row['id'], 'resume')
    calls = []
    planner.completion = lambda model, *args: (calls.append(model) or '{"report":"done"}', {'total_tokens': 1})
    for agent in ('a', 'b'):
        cfg.save({**asdict(ModelConfig()), 'routine_model': f'ollama/{agent}', 'daily_requests': 1}, agent)
    assert planner(a)['report'] == 'done'
    assert planner(b)['report'] == 'done'
    assert calls == ['ollama/a', 'ollama/b']
    assert planner.usage('a')['requests'] == 1
    assert planner.usage('b')['requests'] == 1
    assert planner.usage()['requests'] == 2
    with pytest.raises(BudgetExceeded): planner(a)


def test_legacy_usage_remains_a_conservative_budget_debit(tmp_path):
    _, store, _, planner = fixture(tmp_path)
    day = planner.usage()['day']
    with store.db._transaction() as db:
        db.execute('INSERT INTO model_usage VALUES(?,1,100,0,1)', (day,))
    assert planner.usage('a')['reserved_tokens'] == 100
    assert planner.usage('b')['requests'] == 1


def client(tmp_path):
    service, store, cfg, planner = fixture(tmp_path)
    app = FastAPI()
    auth = lambda: {'auth_type': 'shared_key'}
    app.include_router(create_router(service, auth))
    app.include_router(create_responsibility_router(store, service, auth, lambda: {'a', 'b'}))
    app.include_router(create_model_router(cfg, planner, auth, lambda: {'a', 'b'}))
    return TestClient(app), service, store


def test_agent_settings_routes_and_unknown_agent(tmp_path):
    api, _, _ = client(tmp_path)
    values = {**asdict(ModelConfig()), 'routine_runtime': 'codex', 'routine_model': 'gpt-6-luna'}
    assert api.put('/api/v1/autonomy/model-settings?agent=a', json=values).status_code == 200
    assert api.get('/api/v1/autonomy/model-settings?agent=a').json()['config']['routine_model'] == 'gpt-6-luna'
    assert api.get('/api/v1/autonomy/model-settings?agent=b').json()['config']['routine_runtime'] == 'wee'
    assert api.get('/api/v1/autonomy/model-settings?agent=missing').status_code == 404


def test_agent_scope_rejects_other_responsibility_mutations(tmp_path):
    api, _, store = client(tmp_path)
    a = store.create(agent='a', goal='a', interval_seconds=300)
    b = store.create(agent='b', goal='b', interval_seconds=300)
    assert [r['id'] for r in api.get('/api/v1/autonomy/responsibilities?agent=a').json()['responsibilities']] == [a['id']]
    assert api.post(f'/api/v1/autonomy/responsibilities/{b["id"]}/control?agent=a', json={'command':'resume'}).status_code == 404
    assert api.put(f'/api/v1/autonomy/responsibilities/{b["id"]}?agent=a', json={'goal':'wrong'}).status_code == 404
    assert store.get(b['id'])['status'] == 'paused'
    assert api.post('/api/v1/autonomy/responsibilities?agent=a', json={'agent':'b','goal':'wrong','interval_seconds':300}).status_code == 400


def test_agent_scope_decisions_and_rules(tmp_path):
    api, service, _ = client(tmp_path)
    service.policy.set_enabled(True)
    def pending(agent):
        return service.execute(Action(agent=agent, operation='model.escalate', host='codex', resource='codex:gpt-6-luna'), responsibility=agent, intent_key=agent, summary='Test', adapter=lambda: None)
    pending('a'); pending('b')
    requests = api.get('/api/v1/autonomy/approvals?agent=a').json()['requests']
    assert len(requests) == 1 and requests[0]['scope']['agent'] == 'a'
    other = api.get('/api/v1/autonomy/approvals?agent=b').json()['requests'][0]
    path = f'/api/v1/autonomy/approvals/{other["id"]}/decision'
    body = {'decision':'approve_always','fingerprint':other['fingerprint']}
    assert api.post(path+'?agent=a', json=body).status_code == 404
    assert api.post(path+'?agent=b', json=body).status_code == 200
    assert api.get('/api/v1/autonomy/rules?agent=a').json()['rules'] == []
    rule = api.get('/api/v1/autonomy/rules?agent=b').json()['rules'][0]
    assert api.delete(f'/api/v1/autonomy/rules/{rule["id"]}?agent=a').status_code == 404
    assert api.delete(f'/api/v1/autonomy/rules/{rule["id"]}?agent=b').status_code == 200
