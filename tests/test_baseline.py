import json

import pytest
from fastapi.testclient import TestClient

from fixture_executor import FixtureExecutor, FixtureMantis
from security_auditor.api import create_app
from security_auditor.baseline import baseline_snapshot, import_baseline
from security_auditor.skills import digest
from security_auditor.store import Store
from test_api import wait_run


def test_continuation_preserves_requirements_and_does_not_regenerate(fixture_settings, monkeypatch):
    store = Store(fixture_settings.database, enforce_budgets=fixture_settings.enforce_budgets)
    executor, mantis = FixtureExecutor(store), FixtureMantis(fixture_settings, store)
    app = create_app(fixture_settings, executor=executor, mantis_executor=mantis)
    with TestClient(app) as client:
        pid = client.post('/api/v1/projects', json={'name':'baseline'}).json()['id']
        client.post(f'/api/v1/projects/{pid}/documents', files={'file':('design.md','读取和导出订单必须校验租户范围。'.encode())})
        old_id = client.post(f'/api/v1/projects/{pid}/runs', json={'mode':'requirements_only'}).json()['id']
        assert wait_run(client, pid, old_id)['status'] == 'COMPLETED'
        originals = store.records(old_id, 'requirement')
        source_evidence = [store.read_evidence(old_id, e) for e in originals[0]['evidence_ids']]
        monkeypatch.setattr('security_auditor.ingestion.extract_document', lambda *a: pytest.fail('document reparsed'))
        previous_execute = executor.execute
        async def only_checks(task, bundle, context):
            assert bundle.skill_id == 'requirement_checker'
            assert [r['id'] for r in context['upstream_records']] == [task['scope']['requirement_id']]
            return await previous_execute(task, bundle, context)
        executor.execute = only_checks
        response = client.post(f'/api/v1/projects/{pid}/runs', json={
            'mode': 'implementation_only', 'repository_id': 'fixture', 'baseline_run_id': old_id})
        assert response.status_code == 202, response.text
        rid = response.json()['id']
        completed = wait_run(client, pid, rid)
        assert completed['status'] == 'COMPLETED', completed
        assert completed['snapshot']['assets'] == [] and completed['snapshot']['standard'] is None
        imported = store.records(rid, 'requirement')
        assert imported[0]['id'] != originals[0]['id']
        for key in ('title', 'statement', 'acceptance_criteria', 'origin', 'rationale', 'clause_ids'):
            assert imported[0][key] == originals[0][key]
        new_evidence = [store.read_evidence(rid, e) for e in imported[0]['evidence_ids']]
        assert [e['content'] for e in new_evidence] == [e['content'] for e in source_evidence]
        assert new_evidence[0]['metadata']['baseline_evidence_id'] == source_evidence[0]['id']
        assert store.records(old_id, 'requirement') == originals
        stages = {t['stage'] for t in store.rows('SELECT stage FROM tasks WHERE run_id=?', (rid,))}
        assert stages == {'requirement_baseline', 'ingestion', 'threat_model', 'mantis_model', 'requirement_check'}
        checks = store.records(rid, 'assessment')
        assert {r['requirement_id'] for r in checks} == {r['id'] for r in imported}
        assert [r['acceptance_criterion'] for r in checks] == imported[0]['acceptance_criteria']
        receipt = store.rows("SELECT id,result FROM tasks WHERE run_id=? AND stage='requirement_baseline'", (rid,))[0]
        assert json.loads(receipt['result'])['record_id_map'][originals[0]['id']] == imported[0]['id']
        # Service retry is idempotent and does not duplicate baseline rows.
        import_baseline(store, rid, receipt['id'])
        assert store.records(rid, 'requirement') == imported
        assert client.get(f'/api/v1/projects/{pid}/runs/{rid}/matrix').json()['requirements'][0]['checked_criteria'] == 2
        other = client.post('/api/v1/projects', json={'name': 'other'}).json()['id']
        assert client.post(f'/api/v1/projects/{other}/runs', json={
            'mode': 'implementation_only', 'repository_id': 'fixture', 'baseline_run_id': old_id}).status_code == 400
        assert client.post(f'/api/v1/projects/{pid}/runs', json={'mode':'implementation_only','repository_id':'fixture'}).status_code == 400
        assert client.post(f'/api/v1/projects/{pid}/runs', json={'mode':'code_only','repository_id':'fixture','baseline_run_id':old_id}).status_code == 400


def test_incomplete_source_and_tampered_baseline_are_rejected(settings, store):
    project = store.create_project('empty')
    run = store.create_run(project['id'], 'requirements_only', {'language':'en'}, False, 100, 1000)
    with pytest.raises(ValueError, match='暂停'):
        baseline_snapshot(store, project['id'], run['id'])
    store.set_run_status(run['id'], 'PAUSED')
    with pytest.raises(ValueError, match='没有已提交'):
        baseline_snapshot(store, project['id'], run['id'])
    payload = {'source_run_id': run['id'], 'records': [], 'evidence': []}
    payload['sha256'] = digest(b'wrong')
    child = store.create_run(project['id'], 'implementation_only', {'baseline':payload}, False, 100, 1000)
    tid = store.add_task(child['id'], 'requirement_baseline', {}, wave='service')
    with pytest.raises(ValueError, match='摘要'):
        import_baseline(store, child['id'], tid)
    assert not store.records(child['id'])
