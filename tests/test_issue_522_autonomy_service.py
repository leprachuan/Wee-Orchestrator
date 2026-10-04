from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from autonomy_policy import Action, PolicyStore
from autonomy_approvals import ApprovalConflict
from autonomy_service import ApprovalService, OWNER, principal, create_router

AUTH = {'auth_type': 'shared_key', 'identity': 'forged-header'}

def request(service, key='intent'):
    return service.approvals.create(Action('wee-dev', 'service.restart', 'dev', 'wee'), owner=OWNER, responsibility='health', intent_key=key, preview={'summary':'Restart Wee on dev'})


def test_identity_is_server_verified():
    assert principal(AUTH) == (OWNER, 'shared-key-client')
    assert principal({'auth_type':'session_token', 'identity':'ios'}) == (OWNER,'paired:ios')
    with pytest.raises(PermissionError): principal({'identity':'admin'})


def test_rule_publication_recovers_without_duplicate_or_reactivation(tmp_path):
    s=ApprovalService(tmp_path); s.policy.set_enabled(True); r=request(s)
    row,won=s.approvals.resolve_always(r['id'],owner=OWNER,actor='web',fingerprint=r['fingerprint'])
    assert won and row['status']=='rule_pending'
    assert not s.approvals.claim(r['id'],Action('wee-dev','service.restart','dev','wee'),owner=OWNER,responsibility='health')
    # Simulate JSON committed, SQLite publication uncommitted after a crash.
    with s.approvals._transaction() as db:
        out=dict(db.execute('SELECT * FROM approval_rule_outbox').fetchone())
    rule=s.policy.add(actor='web',approval_id=r['id'],decision='allow',rule_id=out['rule_id'],**json.loads(out['scope_json']))
    s.policy.revoke(rule.id,actor='mac')
    ApprovalService(tmp_path).recover_rules()
    assert len(s.policy.load()['rules'])==1
    assert not s.policy.load()['rules'][0].enabled
    assert s.approvals.get(r['id'],owner=OWNER)['status']=='expired'


def test_three_clients_always_allow_first_wins(tmp_path):
    s=ApprovalService(tmp_path);s.policy.set_enabled(True);r=request(s)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda actor: ApprovalService(tmp_path).decide(r['id'],{'auth_type':'session_token','identity':actor},'approve_always',r['fingerprint']),['mac','ios','web']))
    assert sum(result['won'] for result in results)==1
    assert len(s.policy.load()['rules'])==1
    assert all(result['request']['status']=='approved' for result in results)


def test_gate_revalidates_deny_and_reserves_every_execution(tmp_path):
    s=ApprovalService(tmp_path);s.policy.set_enabled(True);r=request(s)
    s.decide(r['id'],AUTH,'approve_once',r['fingerprint'])
    s.policy.add(actor='web',approval_id='manual',agent='wee-dev',operation='service.restart',host='dev',resource='wee',decision='deny')
    calls=[];a=Action('wee-dev','service.restart','dev','wee')
    kwargs=dict(responsibility='health',intent_key='intent',summary='Restart',adapter=lambda a:calls.append(a))
    assert s.execute(a,**kwargs)['status']=='denied' and not calls
    rule=s.policy.load()['rules'][0];s.policy.revoke(rule.id,actor='mac')
    assert s.execute(a,**kwargs)['status']=='succeeded'
    assert s.execute(a,**kwargs)['status']=='succeeded' and len(calls)==1
    with pytest.raises(PermissionError):s.execute(Action('wee-dev','shell.execute','dev','shell'),**kwargs)


def test_external_failure_is_uncertain_and_not_replayed(tmp_path):
    s=ApprovalService(tmp_path);s.policy.set_enabled(True);r=request(s);s.decide(r['id'],AUTH,'approve_once',r['fingerprint'])
    calls=[]
    def fail(a): calls.append(1); raise RuntimeError('after external effect')
    kwargs=dict(responsibility='health',intent_key='intent',summary='Restart',adapter=fail)
    with pytest.raises(RuntimeError):s.execute(Action('wee-dev','service.restart','dev','wee'),**kwargs)
    assert s.execute(Action('wee-dev','service.restart','dev','wee'),**kwargs)['status']=='uncertain'
    assert len(calls)==1


def test_api_auth_all_shared_clients_and_no_arguments_in_preview(tmp_path):
    from fastapi import FastAPI, HTTPException, Header
    from fastapi.testclient import TestClient
    async def auth(authorization: str=Header('')):
        if authorization!='Bearer test':raise HTTPException(401)
        return AUTH
    s=ApprovalService(tmp_path);s.policy.set_enabled(True);r=request(s)
    app=FastAPI();app.include_router(create_router(s,auth));client=TestClient(app)
    assert client.get('/api/v1/autonomy/approvals').status_code==401
    h={'Authorization':'Bearer test'}
    response=client.get('/api/v1/autonomy/approvals',headers=h)
    assert response.status_code==200 and response.json()['requests'][0]['preview']['summary']=='Restart Wee on dev'
    for forbidden in ('arguments_json','intent_key','owner'):
        assert forbidden not in response.text
    assert client.post('/api/v1/autonomy/approvals/'+r['id']+'/decision',headers=h,json={'decision':'approve_always','fingerprint':r['fingerprint']}).status_code==200
    assert len(client.get('/api/v1/autonomy/rules',headers=h).json()['rules'])==1
    cursor=client.get('/api/v1/autonomy/events',headers=h).json()['cursor']
    assert client.get('/api/v1/autonomy/events',params={'after':cursor},headers=h).json()['events']==[]


def test_process_safe_writers_do_not_lose_rules(tmp_path):
    from multiprocessing import get_context
    path=tmp_path/'rules.json'
    # Separate store instances exercise independent advisory locks.
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda i: PolicyStore(path).add(actor='user',approval_id=str(i),agent='a',operation='file.read',host='dev',resource='/tmp/'+str(i),decision='allow'),range(40)))
    assert len(PolicyStore(path).load()['rules'])==40


def test_rule_edit_is_atomic_audited_and_does_not_reactivate(tmp_path):
    s=ApprovalService(tmp_path)
    old=s.policy.add(actor='web',approval_id='manual',agent='a',operation='file.read',host='dev',resource='/tmp/a',decision='allow')
    new=s.policy.replace(old.id,actor='mac',agent='a',operation='file.read',host='dev',resource='/tmp/b',decision='ask')
    rules=s.policy.load()['rules']
    assert len(rules)==2 and not rules[0].enabled and rules[0].revoked_by=='mac'
    assert new.source_approval_id=='edit:'+old.id and new.resource=='/tmp/b'
    with pytest.raises(ValueError):s.policy.replace(old.id,actor='mac',agent='a',operation='file.read',host='dev',resource='/tmp/c',decision='allow')
    assert len(s.policy.load()['rules'])==2
