"""Exercise all production budget boundaries without sending paid requests."""
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from security_auditor.api import BudgetRequest, RunRequest, create_app
from security_auditor.gateway import app as gateway
from security_auditor.provider import BudgetExceeded
from security_auditor.store import Store
from test_budget_api import endpoint


def test_disabled_defaults_ignore_explicit_run_budget(fixture_settings):
    app = create_app(replace(fixture_settings, enforce_budgets=False, max_requests=1, max_tokens=1))
    settings = next(r.endpoint for r in app.routes if r.path == '/api/v1/settings' and 'GET' in r.methods)()
    assert not settings['budget_enforced']
    assert settings['default_budget'] == {'max_requests': None, 'max_tokens': None}
    p = app.state.store.create_project('no caps')
    run = endpoint(app, '/{project_id}/runs')(p['id'], RunRequest(
        mode='code_only', repository_id='fixture', budget=BudgetRequest(max_requests=1, max_tokens=1)), None)
    assert run['budget']['max_requests'] is None and run['budget']['max_tokens'] is None
    assert not run['controls']['budget_exhausted']
    app.state.store.set_run_status(run['id'], 'PAUSED')
    with pytest.raises(ValueError, match='未启用'):
        endpoint(app, '/budget')(p['id'], run['id'], BudgetRequest(max_requests=1))


@pytest.mark.parametrize('physical', [True, False])
def test_historical_caps_bypassed_with_accounting_pause_and_restore(store, physical):
    p = store.create_project('historical capped run')
    run = store.create_run(p['id'], 'full', {}, False, 1, 1, physical=physical)
    tid = store.add_task(run['id'], 'design', {})
    unbounded = Store(store.path, enforce_budgets=False)
    for call in range(14):  # crosses both cumulative cap and old 12-call task cap
        if physical:
            rid = unbounded.provider.begin(run['id'], tid, str(call), 1, {'max_tokens': 16384})
            unbounded.provider.finish(rid, None, 200, tokens=50)
        else:
            unbounded.settle(unbounded.reserve(run['id'], tid, 10000), 50)
    assert unbounded.run(run['id'])['usage']['requests'] == 14
    assert unbounded.run(run['id'])['usage']['tokens'] == 700
    assert unbounded.run(run['id'])['max_requests'] == 1
    assert unbounded.run(run['id'])['unlimited_budget']
    unbounded.pause_run(p['id'], run['id'])
    with pytest.raises(BudgetExceeded):
        if physical:
            unbounded.provider.begin(run['id'], tid, 'paused', 1, {})
        else:
            unbounded.reserve(run['id'], tid, 10000)
    assert unbounded.resume_run(p['id'], run['id'], {})['status'] == 'PENDING'
    bounded = Store(store.path, enforce_budgets=True)
    with pytest.raises(BudgetExceeded):
        if physical:
            bounded.provider.begin(run['id'], tid, 'bounded-again', 1, {})
        else:
            bounded.reserve(run['id'], tid, 10000)
    assert unbounded.run(run['id'])['usage']['requests'] == 14


def test_gateway_disabled_caps_allow_accounted_retry_but_respect_pause(store, monkeypatch):
    p = store.create_project('gateway no caps')
    run = store.create_run(p['id'], 'code_only', {}, False, 1, 1, physical=True)
    tid = store.add_task(run['id'], 'research', {})
    monkeypatch.setenv('AUDITOR_GATEWAY_DATABASE', str(store.path))
    monkeypatch.setenv('AUDITOR_ENFORCE_BUDGETS', 'false')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fixture-key')
    monkeypatch.setenv('AUDITOR_PROVIDER_ATTEMPTS', '3')
    monkeypatch.setenv('AUDITOR_PROVIDER_RETRY_SECONDS', '0')
    monkeypatch.delenv('AUDITOR_ANALYSIS_PROXY', raising=False)
    calls = []
    def upstream(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}, 'finish_reason': 'stop'}],
                                        'usage': {'total_tokens': 70}})
    headers = {'x-auditor-run': run['id'], 'x-auditor-task': tid, 'x-auditor-request': 'no-cap'}
    body = {'model': 'deepseek-flash', 'messages': [], 'max_tokens': 16384}
    with TestClient(gateway) as client:
        gateway.state.client = httpx.AsyncClient(base_url='https://api.deepseek.com',
                                                transport=httpx.MockTransport(upstream))
        assert client.post('/v1/chat/completions', headers=headers, json=body).status_code == 200
        assert len(calls) == 2
        assert store.run(run['id'])['usage']['requests'] == 2
        assert store.run(run['id'])['usage']['known_tokens'] == 70
        store.pause_run(p['id'], run['id'])
        response = client.post('/v1/chat/completions', headers={**headers, 'x-auditor-request': 'paused'}, json=body)
        assert response.status_code == 409
        assert store.provider.failure('paused') == 'pause'
        assert len(calls) == 2
