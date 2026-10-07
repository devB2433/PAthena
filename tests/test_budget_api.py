import pytest

from security_auditor.api import create_app, BudgetRequest, RunRequest


def endpoint(app, suffix):
    return next(r.endpoint for r in app.routes if r.path.endswith(suffix) and 'POST' in r.methods)


def test_production_budget_edit_preserves_usage_and_resume(fixture_settings):
    # No lifespan/polling worker: this tests the actual production API without a
    # live model request. Only local accounting is exercised.
    app = create_app(fixture_settings)
    store = app.state.store
    project = store.create_project("budget API fixture")
    run = endpoint(app, '/{project_id}/runs')(project['id'],
        RunRequest(mode='code_only', repository_id='fixture', budget=BudgetRequest(max_requests=1, max_tokens=None)), None)
    tid = store.add_task(run['id'], 'test', {})
    rid = store.provider.begin(run['id'], tid, 'one', 1, {})
    store.provider.finish(rid, None, 200, tokens=70)
    store.set_run_status(run['id'], 'PAUSED')
    resume = endpoint(app, '/resume')
    with pytest.raises(ValueError, match='预算'):
        resume(project['id'], run['id'])
    edit = endpoint(app, '/budget')
    changed = edit(project['id'], run['id'], BudgetRequest(max_requests=500, max_tokens=None))
    assert changed['budget']['max_requests'] == 500
    assert changed['usage']['requests'] == 1 and changed['usage']['known_tokens'] == 70
    resumed = resume(project['id'], run['id'])
    assert resumed['status'] == 'PENDING' and resumed['usage'] == changed['usage']
    other = store.create_project('other')
    with pytest.raises(KeyError):
        edit(other['id'], run['id'], BudgetRequest(max_requests=500, max_tokens=None))


def test_default_budget_and_idempotent_start_have_same_public_shape(fixture_settings, monkeypatch):
    from dataclasses import replace
    from security_auditor.config import Settings
    monkeypatch.delenv('AUDITOR_MAX_REQUESTS', raising=False)
    monkeypatch.delenv('AUDITOR_MAX_TOKENS', raising=False)
    defaults = Settings()
    assert defaults.max_requests == 500 and defaults.max_tokens == 0
    app = create_app(replace(fixture_settings, max_requests=500, max_tokens=0))
    p = app.state.store.create_project('idempotence')
    start = endpoint(app, '/{project_id}/runs')
    body = RunRequest(mode='code_only', repository_id='fixture')
    first = start(p['id'], body, 'same-input')
    second = start(p['id'], body, 'same-input')
    assert first == second
    assert first['budget']['max_requests'] == 500 and first['budget']['max_tokens'] is None
    assert first['budget']['physical']
    assert first['repository_id'] == 'fixture'


@pytest.mark.parametrize('status', ['PENDING', 'WAITING', 'RUNNING'])
def test_pause_is_scoped_and_preserves_completed_results(fixture_settings, status):
    app = create_app(fixture_settings)
    store = app.state.store
    p = store.create_project('pause')
    run = endpoint(app, '/{project_id}/runs')(p['id'], RunRequest(mode='code_only', repository_id='fixture'), None)
    task = store.add_task(run['id'], 'completed-stage', {})
    store.finish_service(task, {'retained': True})
    store.set_run_status(run['id'], status)
    other = store.create_project('other')
    with pytest.raises(KeyError):
        endpoint(app, '/pause')(other['id'], run['id'])
    paused = endpoint(app, '/pause')(p['id'], run['id'])
    assert paused['pause_requested'] and paused['issue']['code'] == 'user_paused'
    assert paused['status'] == ('RUNNING' if status == 'RUNNING' else 'PAUSED')
    assert store.task(task)['result'] == {'retained': True}


def test_version_change_blocks_resume_but_preserves_budget_and_results(fixture_settings):
    app = create_app(fixture_settings)
    store = app.state.store
    p = store.create_project('version')
    run = store.create_run(p['id'], 'code_only', {'repository': {'id': 'fixture'}, 'mantis_hash': 'old'}, False, 500, 0, physical=True)
    store.set_run_status(run['id'], 'FAILED', 'old', issue_code='output')
    edit = endpoint(app, '/budget')(p['id'], run['id'], BudgetRequest(max_requests=501))
    assert edit['controls']['requires_new_run'] and not edit['controls']['can_resume']
    with pytest.raises(ValueError, match='版本'):
        endpoint(app, '/resume')(p['id'], run['id'])
    assert store.run(run['id'])['status'] == 'FAILED'


def test_running_budget_cannot_be_changed(fixture_settings):
    app = create_app(fixture_settings)
    store = app.state.store
    p = store.create_project('running')
    run = store.create_run(p['id'], 'code_only', {}, False, 500, 0, physical=True)
    store.set_run_status(run['id'], 'RUNNING')
    with pytest.raises(ValueError, match='暂停'):
        endpoint(app, '/budget')(p['id'], run['id'], BudgetRequest(max_requests=1000))
    assert store.run(run['id'])['budget']['max_requests'] == 500


def test_explicit_unlimited_run_passes_former_cap_and_keeps_pause_control(fixture_settings):
    from security_auditor.provider import BudgetExceeded
    app = create_app(fixture_settings)
    store = app.state.store
    p = store.create_project('explicit unlimited experiment')
    run = endpoint(app, '/{project_id}/runs')(p['id'], RunRequest(
        mode='code_only', repository_id='fixture', budget=BudgetRequest()), None)
    assert run['budget']['max_requests'] is None and run['budget']['max_tokens'] is None
    tid = store.add_task(run['id'], 'fixture', {})
    for call in range(501):
        request_id = store.provider.begin(run['id'], tid, str(call), 1, {})
        store.provider.finish(request_id, None, 200, tokens=7)
    assert store.run(run['id'])['usage']['requests'] == 501
    assert store.run(run['id'])['usage']['known_tokens'] == 3507
    endpoint(app, '/pause')(p['id'], run['id'])
    with pytest.raises(BudgetExceeded):
        store.provider.begin(run['id'], tid, 'after-pause', 1, {})
    assert store.run(run['id'])['usage']['requests'] == 501


def test_concurrent_resume_schedules_once_and_preserves_usage(store, sample_run):
    from concurrent.futures import ThreadPoolExecutor
    p, run, _ = sample_run
    store.provider.configure(run['id'], 500, None)
    tid = store.add_task(run['id'], 'fixture', {})
    attempt = store.provider.begin(run['id'], tid, 'original', 1, {})
    store.provider.finish(attempt, None, 200, tokens=70)
    store.set_run_status(run['id'], 'PAUSED')
    def resume(_):
        try:
            return store.resume_run(p['id'], run['id'], {})['status']
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(resume, range(4)))
    assert results.count('PENDING') == 1
    assert store.run(run['id'])['usage']['requests'] == 1
    assert len(store.rows("SELECT id FROM events WHERE event_type='run_resumed'")) == 1
