import pytest
from test_issue_542_adaptive_heartbeat import adaptive, active_goal
from autonomy_heartbeat import Heartbeats


def step(quote='Write notes to workspace.'):
    return {'kind': 'save_note', 'permission': 'autonomous', 'quote': quote}


def test_agent_instructions_persist_pause_and_invalidate_runs(adaptive):
    service, store, hb, now = adaptive
    first = active_goal(adaptive)
    other = active_goal(adaptive, agent='b')
    saved = hb.save_agent_instructions('a', 'Write notes to workspace.', 'Ask before deployment.')
    current = store.get(first['id'])
    assert current['status'] == 'paused'
    assert current['run_number'] > first['run_number']
    assert current['instruction_version'] > first['instruction_version']
    assert store.get(other['id'])['status'] == 'active'
    assert Heartbeats(store, service, lambda: {'a', 'b'}).agent_instructions('a') == saved
    store.control(first['id'], 'resume')
    assert hb.context('a')['agent_instructions'] == saved
    assert hb.permission(store.get(first['id']), step()) == 'allow'


def test_agent_and_goal_approval_requirements_win(adaptive):
    service, store, hb, now = adaptive
    row = active_goal(adaptive, allowed='Write notes to workspace.', ask='Ask before notes.')
    hb.save_agent_instructions('a', 'Write notes to workspace.', '')
    assert hb.permission(store.get(row['id']), step()) == 'ask'
    hb.instructions(row['id'], 'Write notes to workspace.', '')
    hb.save_agent_instructions('a', 'Write notes to workspace.', 'Ask before notes.')
    assert hb.permission(store.get(row['id']), step()) == 'ask'
    assert hb.agent_instructions('b')['autonomous_instructions'] == ''


def test_unknown_agent_and_invalid_text_rejected(adaptive):
    _, _, hb, _ = adaptive
    with pytest.raises(KeyError):
        hb.save_agent_instructions('unknown', '', '')
    with pytest.raises(ValueError):
        hb.save_agent_instructions('a', 'x' * 8001, '')
    with pytest.raises(ValueError):
        hb.save_agent_instructions('a', '', '\0')


def test_saving_unchanged_instructions_preserves_active_goal(adaptive):
    _, store, hb, _ = adaptive
    hb.save_agent_instructions('a', 'Write notes.', '')
    row = active_goal(adaptive)
    hb.save_agent_instructions('a', 'Write notes.', '')
    assert store.get(row['id'])['status'] == 'active'
    assert store.get(row['id'])['run_number'] == row['run_number']
