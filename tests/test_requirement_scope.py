"""Scope selection and workflow contracts. Scripted models do not measure accuracy."""
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from security_auditor.api import create_app
from security_auditor.control_catalog import read_control, search
from security_auditor.domain import Applicability, StageOutput
from security_auditor.ingestion import standard_manifest
from security_auditor.runtime import Scheduler, report, result_matrix
from security_auditor.scope_policy import preview, scope_options
from security_auditor.scope_resolution import resolve, check_target
from security_auditor.standard_controls import preparation
from security_auditor.standard_library import import_library
from test_precomputed_catalog import FixtureEncoder, FixtureRanker, prepared_pack as pack_fixture, prepared_run
from fixture_executor import FixtureExecutor, FixtureMantis


@pytest.fixture
def prepared_pack(tmp_path):
    return pack_fixture.__wrapped__(tmp_path)


def frozen_scope(store, root, nodes=None, ids=None):
    preparation(root, FixtureEncoder())
    run = prepared_run(store, root)
    cid = run['snapshot']['standard']['prepared']['catalog_id']
    request = {'catalog_id': cid, 'selector': {'kind': 'controls', 'control_ids': ids} if ids else
               {'kind': 'facets', 'standard_node_ids': nodes or ['8']}}
    return run, preview(store, request)


def test_chapter_scope_persists_metadata_and_reuses_vectors(store, prepared_pack):
    root, _ = prepared_pack
    run, frozen = frozen_scope(store, root, ['8', '8.3'])
    assert frozen['control_ids'] == ['control_8_3_1']
    assert frozen['counts']['controls'] == 1
    assert scope_options(store, frozen['catalog_id'])['supported_dimensions'] == ['standard_node_ids', 'control_ids']
    assert len(store.rows('SELECT * FROM standard_scope_metadata')) == 1
    original = store.rows('SELECT * FROM standard_vectors')
    assert preview(store, {k: frozen[k] for k in ('catalog_id', 'selector', 'preview_hash')}) == frozen
    assert store.rows('SELECT * FROM standard_vectors') == original
    assert not store.run(run['id'])['usage']['requests']


@pytest.mark.parametrize('selector', [
    {'kind': 'facets', 'standard_node_ids': []},
    {'kind': 'facets', 'standard_node_ids': ['80']},
    {'kind': 'controls', 'control_ids': ['missing']},
    {'kind': 'controls', 'control_ids': ['control_8_3_1'], 'standard_node_ids': ['8']},
    {'kind': 'facets', 'standard_node_ids': ['8', '8']},
    {'kind': 'facets', 'standard_node_ids': ['8'], 'topic_ids': ['auth']},
])
def test_invalid_or_unsupported_selection_never_falls_back(store, prepared_pack, selector):
    root, _ = prepared_pack
    _, frozen = frozen_scope(store, root)
    with pytest.raises(ValueError):
        preview(store, {'catalog_id': frozen['catalog_id'], 'selector': selector})


def test_preview_is_pinned_and_metadata_tampering_is_rejected(store, prepared_pack):
    root, _ = prepared_pack
    _, frozen = frozen_scope(store, root)
    with pytest.raises(ValueError, match='预览已变化'):
        preview(store, {'catalog_id': frozen['catalog_id'], 'selector': {'kind': 'facets', 'standard_node_ids': ['12']},
                        'preview_hash': frozen['preview_hash']})
    with store.connect() as db:
        db.execute("UPDATE standard_scope_metadata SET payload='{}'")
    with pytest.raises(ValueError, match='元数据'):
        scope_options(store, frozen['catalog_id'])


def test_metadata_only_revision_reuses_passage_vectors(prepared_pack):
    root, _ = prepared_pack
    model = FixtureEncoder()
    first = preparation(root, model)
    calls = list(model.calls)
    path = root / 'manifest.json'
    path.write_text(json.dumps({**json.loads(path.read_text()), 'revision_note': 'metadata only'}))
    second = preparation(root, model)
    assert first['catalog_id'] != second['catalog_id']
    assert first['vectors_hash'] == second['vectors_hash']
    assert model.calls == calls


def test_dense_and_lexical_candidates_are_filtered_before_reranking(store, prepared_pack):
    root, _ = prepared_pack
    model, ranker = FixtureEncoder(), FixtureRanker()
    preparation(root, model, ranker)
    run = prepared_run(store, root)
    hits = search(store, run['id'], 'identity personnel', section='12', embedder=model, reranker_model=ranker)
    assert [r['control_id'] for r in hits['items']] == ['control_12_6_1']
    assert ranker.calls and all('Verify identity' not in doc for doc in ranker.calls[0][1])
    assert not search(store, run['id'], 'identity', section='80', embedder=model, reranker_model=ranker)['items']


class ScopeExecutor(FixtureExecutor):
    def __init__(self, store):
        super().__init__(store)
        self.checks = []

    async def execute(self, task, bundle, context):
        if bundle.skill_id == 'pci_mapper':
            if not task['scope'].get('control_id'):
                return StageOutput(records=[], gaps=['Scripted no-match for the original design task'], summary='No match')
            cid = task['scope']['control_id']
            control, eid = read_control(self.store, task['run_id'], task['id'], cid)
            doc = self.store.evidence(task['run_id'], 'document')[0]['id']
            for source in [doc, eid]:
                self.store.note_read(task['id'], source)
            unrelated = control['control_type'] == 'ORGANIZATIONAL'
            return StageOutput(records=[Applicability(title='Fixed control relevance', clause_id=control['clause_id'],
                control_ids=[cid], requirement_ids=[], status='NOT_APPLICABLE' if unrelated else 'UNDETERMINED',
                relevance='UNRELATED' if unrelated else 'RELEVANT', control_scope='NON_CODE' if unrelated else 'CODE_RELATED',
                missing_facts=[] if unrelated else ['Formal PCI scope unknown'], evidence_ids=[doc, eid],
                rationale='Scripted scope fixture: organization duty excluded' if unrelated else 'Scripted relevant code control')],
                summary='Control inventory decision')
        if bundle.skill_id == 'requirement_checker':
            self.checks.append(task['scope']['requirement_id'])
        result = await super().execute(task, bundle, context)
        if bundle.skill_id == 'design_analyst':
            for fact in result.records:
                fact.evidence_ids = [eid for eid in fact.evidence_ids if self.store.read_evidence(task['run_id'], eid)['source_type'] == 'document']
        return result


class ObservedNative(FixtureMantis):
    def __init__(self, settings, store):
        super().__init__(settings, store)
        self.phases = []
        self.requirement_context = []

    async def execute(self, run_id, phase, parent):
        self.phases.append(phase)
        self.requirement_context.append([r['id'] for r in self.store.records(run_id, 'requirement')])
        await super().execute(run_id, phase, parent)


async def execute_scope_app(fixture_settings, store, root, nodes):
    from security_auditor.ingestion import repository_inventory
    preparation(root, FixtureEncoder())
    manifest = standard_manifest(root)
    manifest['library_id'] = import_library(store, manifest)
    frozen = preview(store, {'catalog_id': manifest['prepared']['catalog_id'],
                            'selector': {'kind': 'facets', 'standard_node_ids': nodes}})
    agent, native = ScopeExecutor(store), ObservedNative(fixture_settings, store)
    scheduler = Scheduler(fixture_settings, store, agent, native)
    project = store.create_project('Scope test, isolated')
    path = fixture_settings.state_dir.parent / 'scope-design.md'
    path.write_text('API access uses identity; personnel training is outside this code delivery.')
    asset = store.add_asset(project['id'], path.name, path, 'text/markdown')
    repo = Path(fixture_settings.repositories['fixture'])
    snapshot = {'assets': [asset], 'repository': {'root': str(repo), 'files': repository_inventory(repo, 5000)},
                'standard': manifest, 'requirement_check_scope': frozen,
                'skill_hashes': {s['id']: s['sha256'] for s in scheduler.loader.catalog()},
                'workflow_hash': __import__('hashlib').sha256(fixture_settings.workflow.read_bytes()).hexdigest()}
    run = store.create_run(project['id'], 'full', snapshot, False, 100, 200000)
    await scheduler.execute_run(run['id'])
    assert store.run(run['id'])['status'] == 'COMPLETED', store.run(run['id'])
    return run, agent, native


@pytest.mark.parametrize('nodes,selected_count', [(['8'], 1), (['12'], 0)])
async def test_full_workflow_keeps_native_phases_and_unrelated_findings(
        fixture_settings, store, prepared_pack, nodes, selected_count):
    root, _ = prepared_pack
    run, agent, native = await execute_scope_app(fixture_settings, store, root, nodes)
    assert native.phases == ['model', 'audit']
    matrix = result_matrix(store, run['id'])
    assert len(agent.checks) == selected_count
    assert matrix['requirement_check_scope']['resolution']['counts']['selected_requirements'] == selected_count
    assert len(matrix['requirements']) >= 1
    assert any(r['implementation_status'] == 'NOT_SELECTED' for r in matrix['requirements'])
    assert matrix['findings'] and matrix['findings'][0]['requirement_ids'] == []
    assert matrix['findings'][0]['engine'] == 'mantis'
    assert all(set(ids) == {r['id'] for r in matrix['requirements']} for ids in native.requirement_context)
    assert report(store, run['id'])['requirement_check_scope'] == matrix['requirement_check_scope']
    if selected_count:
        bound = next(r for r in matrix['requirements'] if r.get('standard_control_id'))
        assert not bound['matched_requirement_ids']  # Design omission does not erase a selected control.
        assert bound['checked_criteria'] == bound['total_criteria']
    # Invalid independent check tasks cannot bypass the selection or commit verdicts.
    unselected = next(r for r in matrix['requirements'] if r['check_selection'] == 'NOT_SELECTED')
    with pytest.raises(ValueError, match='冻结需求检查范围'):
        check_target(store, run['id'], {'requirement_id': unselected['id'], 'acceptance_criteria': unselected['acceptance_criteria']})
    before = resolve(store, run['id'])
    assert resolve(store, run['id']) == before
    with store.connect() as db:
        db.execute("UPDATE scope_resolutions SET payload='{}' WHERE run_id=?", (run['id'],))
    with pytest.raises(ValueError, match='解析摘要'):
        result_matrix(store, run['id'])


def test_api_preview_modes_versions_and_all_exports(fixture_settings, store, prepared_pack, monkeypatch):
    from test_api import wait_run
    root, _ = prepared_pack
    model = FixtureEncoder()
    preparation(root, model)
    monkeypatch.setattr('security_auditor.embeddings.encoder', lambda *args: model)
    configured = replace(fixture_settings, standard_pack=str(root))
    app = create_app(configured, ScopeExecutor(store), ObservedNative(configured, store))
    with TestClient(app) as client:
        pid = client.post('/api/v1/projects', json={'name': 'Scoped full workflow'}).json()['id']
        catalogs = client.get('/api/v1/standards').json()
        cid = catalogs['configured_catalog_id']
        assert cid and client.get(f'/api/v1/standards/{cid}/scope-options').status_code == 200
        request = {'catalog_id': cid, 'selector': {'kind': 'facets', 'standard_node_ids': ['8']}}
        value = client.post(f'/api/v1/projects/{pid}/requirement-check-scope-preview', json=request)
        assert value.status_code == 200, value.text
        request['preview_hash'] = value.json()['preview_hash']
        assert not store.rows('SELECT * FROM usage')
        for mode in ['requirements_only', 'code_only']:
            response = client.post(f'/api/v1/projects/{pid}/runs', json={'mode': mode, 'repository_id': 'fixture', 'requirement_check_scope': request})
            assert response.status_code == 400
        client.post(f'/api/v1/projects/{pid}/documents', files={'file': ('design.md', b'API identity control')})
        response = client.post(f'/api/v1/projects/{pid}/runs', json={'mode': 'full', 'repository_id': 'fixture', 'requirement_check_scope': request})
        assert response.status_code == 202, response.text
        rid = response.json()['id']
        assert wait_run(client, pid, rid)['status'] == 'COMPLETED'
        base = f'/api/v1/projects/{pid}/runs/{rid}'
        scope = client.get(base + '/requirement-check-scope').json()
        assert scope['resolution']['counts']['selected_requirements'] == 1
        for suffix in ['json', 'csv', 'html']:
            result = client.get(base + '/reports/' + suffix)
            assert result.status_code == 200, result.text
            assert 'Cross-tenant export' in result.text  # Outside selected requirement, still exported.
        html = client.get(base + '/reports/html').text
        assert 'Not selected for this implementation check' in html
        assert 'Threat modeling and independent audit retain their original code scope' in html
        other = client.post('/api/v1/projects', json={'name': 'Other'}).json()['id']
        assert client.get(f'/api/v1/projects/{other}/runs/{rid}/requirement-check-scope').status_code == 404
        # Recheck from a complete baseline, while preserving every baseline requirement.
        rerun = client.post(f'/api/v1/projects/{pid}/runs', json={'mode': 'implementation_only', 'repository_id': 'fixture',
                            'baseline_run_id': rid, 'requirement_check_scope': request})
        assert rerun.status_code == 202, rerun.text
        repeat = wait_run(client, pid, rerun.json()['id'])
        assert repeat['status'] == 'COMPLETED', repeat
        repeat_matrix = client.get(f'/api/v1/projects/{pid}/runs/{repeat["id"]}/matrix').json()
        assert len(repeat_matrix['requirements']) == 2
        assert sum(r['check_selection'] == 'SELECTED' for r in repeat_matrix['requirements']) == 1


async def test_real_adk_control_driven_protocol_loads_checked_in_skill_and_submits_once(settings, store, prepared_pack):
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from pydantic import PrivateAttr
    from security_auditor.agents import AdkExecutor
    from security_auditor.skills import SkillLoader
    root, _ = prepared_pack
    run, frozen = frozen_scope(store, root)
    doc = store.add_evidence(run['id'], 'document', 'design.md:1', 'API authenticates callers.', {})
    scope = {'control_id': 'control_8_3_1', 'clause_ids': ['8.3.1'], 'compliance_scope_policy': 'selected_controls_v1'}
    tid = store.add_task(run['id'], 'pci_mapping', scope)
    bundle = SkillLoader(settings.skills_dir).resolve('pci_mapper', 'pci_mapper', '0.6.1')

    class OneSubmission(BaseLlm):
        model: str = 'scope-contract-fixture'
        _calls: int = PrivateAttr(default=0)

        async def generate_content_async(self, llm_request, stream=False):
            self._calls += 1
            context = json.loads(llm_request.contents[0].parts[0].text)
            assert '[PCI_MAPPER-14]' in str(llm_request.config.system_instruction)
            assert context['selected_control']['standard_control']['id'] == scope['control_id']
            source = context['selected_control']['source']
            assert 'Verify identity before access.' in source
            eid = store.evidence(run['id'], 'standard')[0]['id']
            # IDs from this invocation, not approximate names invented by a model.
            from security_auditor.source_references import SourceReferences
            refs = SourceReferences()
            refs.register([r['id'] for r in store.evidence(run['id']) if r['source_type'] in {'document', 'standard', 'code'}])
            decision = Applicability(title='Prepared identity control', clause_id='8.3.1', status='UNDETERMINED',
                relevance='RELEVANT', control_scope='CODE_RELATED', control_ids=[scope['control_id']], requirement_ids=[],
                evidence_ids=[doc, eid], missing_facts=['Formal scope unknown'], rationale='Identity behavior is relevant')
            value = refs.encode(StageOutput(records=[decision], gaps=[], summary='One control decision').model_dump())
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(
                name='set_model_response', args=value))]))

    model = OneSubmission()
    output = await AdkExecutor(settings, store, model_factory=lambda: model).execute(store.task(tid), bundle,
        {'scope': scope, 'upstream_records': [{'id': 'context', 'evidence_ids': [doc]}]})
    store.commit_output(tid, 'pci_mapper', output)
    assert model._calls == 1
    assert output.records[0].requirement_ids == []
    assert store.records(run['id'], 'applicability')[0]['control_ids'] == frozen['control_ids']


async def test_unselected_task_cannot_commit_or_consume_a_model_call(fixture_settings, store, prepared_pack):
    root, _ = prepared_pack
    run, agent, _ = await execute_scope_app(fixture_settings, store, root, ['8'])
    matrix = result_matrix(store, run['id'])
    unselected = next(r for r in matrix['requirements'] if r['check_selection'] == 'NOT_SELECTED')
    parent = store.add_task(run['id'], 'requirement_check', {'injected': True}, wave='stage')
    scope = {'requirement_id': unselected['id'], 'module': unselected['module'],
             'acceptance_criteria': unselected['acceptance_criteria'],
             'scope_resolution_hash': matrix['requirement_check_scope']['resolution']['resolution_hash']}
    task = store.add_task(run['id'], 'requirement_check', scope, parent)
    original_calls = len(agent.checks)
    scheduler = Scheduler(fixture_settings, store, agent, ObservedNative(fixture_settings, store))
    node = next(n for n in scheduler.spec['nodes'] if n['id'] == 'requirement_check')
    with pytest.raises(ValueError, match='冻结需求检查范围'):
        await scheduler.child(run['id'], node, parent, scope)
    with pytest.raises(ValueError, match='冻结需求检查范围'):
        store.commit_output(task, 'requirement_checker', StageOutput(records=[], summary='Invalid bypass'))
    assert len(agent.checks) == original_calls
