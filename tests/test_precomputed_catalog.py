import json
from pathlib import Path

import pytest

from security_auditor.control_catalog import bind_controls, catalog, read_control, search
from security_auditor.domain import Applicability, Requirement, StageOutput
from security_auditor.ingestion import standard_manifest
from security_auditor.runtime import Scheduler, result_matrix
from security_auditor.skills import digest
from security_auditor.standard_controls import preparation
from security_auditor.standard_library import import_library


class FixtureEncoder:
    """Known semantic fixture; never used by the application."""
    def __init__(self):
        self.profile = {'model': 'test-semantic', 'dimensions': 3, 'revision': 'test'}
        self.profile['id'] = digest(json.dumps(self.profile, sort_keys=True).encode())
        self.calls = []

    def encode(self, texts, *, purpose):
        self.calls.append((purpose, list(texts)))
        return [[1., 0., 0.] if 'identity' in t.lower() or '身份' in t else [0., 1., 0.] for t in texts]


@pytest.fixture
def prepared_pack(tmp_path):
    root = tmp_path / 'standard'
    root.mkdir()
    clauses, controls = [], []
    for cid, words, ctype, method in [('8.3.1', 'Verify identity before access.', 'TECHNICAL', 'CODE'),
                                     ('12.6.1', 'Train personnel.', 'ORGANIZATIONAL', 'NON_CODE')]:
        source = {'file': 'standard.pdf', 'pdf_pages': [1], 'context_ids': []}
        clauses.append({'id': cid, 'text': words, 'defined_approach': words, 'applicability_notes': '', 'source': source})
        text = {'title': words, 'statement': words, 'acceptance_criteria': [words],
                'applicability_conditions': ['For in-scope systems'], 'rationale': 'Normative control'}
        controls.append({'id': 'control_' + cid.replace('.', '_'), 'clause_id': cid, 'control_type': ctype,
                         'verification_method': method, 'source_excerpt': words, 'source_pages': [1],
                         'localizations': {'en': text, 'zh-CN': {**text, 'title': '身份验证' if cid == '8.3.1' else '人员培训'}}})
    raw = ''.join(json.dumps(c) + '\n' for c in clauses).encode()
    (root / 'clauses.jsonl').write_bytes(raw)
    (root / 'controls.jsonl').write_text(''.join(json.dumps(c) + '\n' for c in controls))
    (root / 'manifest.json').write_text(json.dumps({'id': 'PCI_DSS', 'version': '4.0.1', 'fixture': False,
        'requirement_ids': [c['id'] for c in clauses], 'clauses_sha256': digest(raw)}))
    return root, controls


def prepared_run(store, root):
    manifest = standard_manifest(root)
    manifest['library_id'] = import_library(store, manifest)
    project = store.create_project('semantic matching')
    return store.create_run(project['id'], 'full', {'standard': manifest}, False, 100, 200000)


def test_import_and_cross_project_search_never_reembed_standard_or_regenerate_controls(store, prepared_pack):
    root, _ = prepared_pack
    embedder = FixtureEncoder()
    first = preparation(root, embedder)
    passage_calls = len(embedder.calls)
    assert preparation(root, embedder) == first and len(embedder.calls) == passage_calls
    run = prepared_run(store, root)
    found = search(store, run['id'], '身份认证', embedder=embedder)
    assert found['items'][0]['control_id'] == 'control_8_3_1'
    assert embedder.calls[-1] == ('query', ['身份认证'])
    before = len(embedder.calls)
    second = prepared_run(store, root)
    assert search(store, second['id'], '身份认证', embedder=embedder)['items'] == found['items']
    assert len(embedder.calls) == before
    assert len(store.rows('SELECT * FROM standard_catalogs')) == 1
    assert len(store.rows('SELECT * FROM standard_controls')) == 2
    assert len(store.rows('SELECT * FROM standard_vectors')) == 6


def test_version_mismatch_and_tampered_vectors_are_not_repaired_during_project_search(store, prepared_pack):
    root, _ = prepared_pack
    embedder = FixtureEncoder()
    preparation(root, embedder)
    run = prepared_run(store, root)
    different = FixtureEncoder()
    different.profile = {**different.profile, 'revision': 'changed'}
    with pytest.raises(ValueError, match='查询向量模型'):
        search(store, run['id'], 'identity', embedder=different)
    assert not different.calls
    with store.connect() as db:
        db.execute("UPDATE standard_vectors SET vector='[0,1,0]' WHERE item_id='control_8_3_1'")
    with pytest.raises(ValueError, match='向量摘要'):
        search(store, run['id'], 'identity', embedder=embedder)
    assert sum(c[0] == 'passage' for c in embedder.calls) == 1


def test_changed_standard_requires_explicit_preparation_and_preserves_old_vectors(prepared_pack):
    root, _ = prepared_pack
    embedder = FixtureEncoder()
    old = preparation(root, embedder)
    manifest = json.loads((root / 'manifest.json').read_text())
    manifest['revision_note'] = 'Updated standard preparation input'
    (root / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='标准版本不一致'):
        standard_manifest(root)
    updated = preparation(root, embedder)
    assert updated['catalog_id'] != old['catalog_id']
    assert (root / old['vectors_file']).is_file()
    assert standard_manifest(root)['prepared'] == updated


def test_binding_copies_frozen_control_without_project_text_generation(settings, store, prepared_pack):
    root, templates = prepared_pack
    preparation(root, FixtureEncoder())
    run = prepared_run(store, root)
    doc = store.add_evidence(run['id'], 'document', 'design.md:1', 'Verify identity.', {})
    tid = store.add_task(run['id'], 'requirements', {})
    store.note_read(tid, doc)
    req = Requirement(title='API identity', module='api', statement='Verify identities',
        acceptance_criteria=['Invalid identity denied'], origin='EXPLICIT_DESIGN', evidence_ids=[doc], rationale='Design')
    store.commit_output(tid, 'requirement_generator', StageOutput(records=[req], summary='Design requirements'))
    scope = Scheduler(settings, store).scopes(run['id'], 'pci_mapper')[0]
    assert scope['compliance_scope_policy'] == 'precomputed_controls_v3'
    match_task = store.add_task(run['id'], 'pci_mapping', scope)
    c, eid = read_control(store, run['id'], match_task, 'control_8_3_1')
    for source in [doc, eid]:
        store.note_read(match_task, source)
    match = Applicability(title='Matched identity control', clause_id='8.3.1', status='UNDETERMINED',
        relevance='RELEVANT', control_scope='CODE_RELATED', control_ids=[c['id']], requirement_ids=[req.id],
        missing_facts=['CDE scope'], evidence_ids=[doc, eid], rationale='Identity control relates to API')
    store.commit_output(match_task, 'pci_mapper', StageOutput(records=[match], summary='Matched'))
    binding = store.add_task(run['id'], 'pci_requirements', {}, wave='service')
    usage = store.run(run['id'])['usage']
    receipt = bind_controls(store, run['id'], binding)
    bind_controls(store, run['id'], binding)
    assert receipt['model_calls'] == receipt['standard_embedding_calls'] == 0
    assert store.run(run['id'])['usage'] == usage
    generated = next(r for r in store.records(run['id'], 'requirement') if r['origin'] == 'PCI_DSS')
    text = templates[0]['localizations']['en']
    for key in ['title', 'statement', 'acceptance_criteria', 'rationale', 'applicability_conditions']:
        assert generated[key] == text[key]
    assert generated['matched_requirement_ids'] == [req.id]
    assert generated['standard_control_id'] == 'control_8_3_1'
    assert len(result_matrix(store, run['id'])['requirements']) == 2
    assert len(store.rows('SELECT * FROM project_control_bindings')) == 1
    from security_auditor.baseline import baseline_snapshot, import_baseline
    store.set_run_status(run['id'], 'COMPLETED')
    baseline = baseline_snapshot(store, run['project_id'], run['id'])
    child = store.create_run(run['project_id'], 'implementation_only', {'baseline': baseline}, False, 100, 200000)
    child_task = store.add_task(child['id'], 'requirement_baseline', {}, wave='service')
    imported = import_baseline(store, child['id'], child_task)
    bound = next(r for r in store.records(child['id'], 'requirement') if r['origin'] == 'PCI_DSS')
    assert bound['matched_requirement_ids'] == [imported['record_id_map'][req.id]]
    assert bound['standard_control_id'] == generated['standard_control_id']


def test_mapper_cannot_select_missing_control(store, prepared_pack):
    root, _ = prepared_pack
    preparation(root, FixtureEncoder())
    run = prepared_run(store, root)
    assert catalog(store, run['id'])['control_count'] == 2
    # Missing, arbitrary, or another-version control IDs are rejected even if named by a model.
    from security_auditor.control_catalog import control
    with pytest.raises(ValueError, match='不属于当前目录'):
        control(store, run['id'], 'invented-control')


@pytest.mark.parametrize('selected,read_it,error', [
    ('control_8_3_1', False, '已实际读取'),
    ('control_12_6_1', True, '纯组织控制'),
])
def test_mapping_submission_enforces_read_and_code_scope(store, prepared_pack, selected, read_it, error):
    root, _ = prepared_pack
    preparation(root, FixtureEncoder())
    run = prepared_run(store, root)
    doc = store.add_evidence(run['id'], 'document', 'design.md:1', 'Verify identity.', {})
    tid = store.add_task(run['id'], 'requirements', {})
    store.note_read(tid, doc)
    req = Requirement(title='Design', module='api', statement='Verify identity', acceptance_criteria=['Deny invalid'],
        origin='EXPLICIT_DESIGN', evidence_ids=[doc], rationale='Design requirement')
    store.commit_output(tid, 'requirement_generator', StageOutput(records=[req], summary='Design'))
    task = store.add_task(run['id'], 'pci_mapping', {'requirement_id': req.id, 'compliance_scope_policy': 'precomputed_controls_v3'})
    clause_id = '8.3.1' if selected == 'control_8_3_1' else '12.6.1'
    if read_it:
        _, eid = read_control(store, run['id'], task, selected)
    else:
        from security_auditor.standard_library import read
        _, eid = read(store, run['id'], clause_id)
    for source in [doc, eid]:
        store.note_read(task, source)
    match = Applicability(title='Wrong match', clause_id=clause_id, status='APPLICABLE', relevance='RELEVANT',
        control_scope='CODE_RELATED', control_ids=[selected], requirement_ids=[req.id], evidence_ids=[doc, eid], rationale='Fixture')
    with pytest.raises(ValueError, match=error):
        store.commit_output(task, 'pci_mapper', StageOutput(records=[match], summary='Rejected'))
    assert not store.records(run['id'], 'applicability')


def test_catalog_preparation_rejects_unsupported_normative_citation(prepared_pack):
    root, _ = prepared_pack
    raw = (root / 'controls.jsonl').read_text().replace('Verify identity before access.', 'Invented requirement.')
    (root / 'controls.jsonl').write_text(raw)
    embedder = FixtureEncoder()
    with pytest.raises(ValueError, match='确切原文'):
        preparation(root, embedder)
    assert not embedder.calls


class FixtureRanker:
    def __init__(self):
        self.profile = {'model': 'fixture-reranker', 'revision': 'test'}
        self.profile['id'] = digest(json.dumps(self.profile, sort_keys=True).encode())
        self.calls = []

    def rerank(self, query, documents):
        self.calls.append((query, documents))
        return [1. if 'identity' in doc else 0. for doc in documents]


@pytest.mark.parametrize('field,value', [('algorithm', 'unsupported'), ('rrf_k', 0), ('rerank_limit', 290)])
def test_unsupported_retrieval_recipe_is_rejected_before_import(store, prepared_pack, field, value):
    root, _ = prepared_pack
    prepared = preparation(root, FixtureEncoder(), FixtureRanker())
    prepared['retrieval'][field] = value
    prepared['catalog_id'] = digest(json.dumps({k: v for k, v in prepared.items() if k != 'catalog_id'}, sort_keys=True).encode())
    (root / 'prepared.json').write_text(json.dumps(prepared))
    with pytest.raises(ValueError, match='检索策略不支持'):
        prepared_run(store, root)
    assert not store.rows('SELECT * FROM standard_catalogs')


def test_local_reranking_is_cached_across_projects_but_keeps_languages_and_versions_separate(store, prepared_pack):
    root, _ = prepared_pack
    embedder, ranker = FixtureEncoder(), FixtureRanker()
    preparation(root, embedder, ranker)
    one = prepared_run(store, root)
    first = search(store, one['id'], 'identity', embedder=embedder, reranker_model=ranker)
    two = prepared_run(store, root)
    assert search(store, two['id'], 'identity', embedder=embedder, reranker_model=ranker)['items'] == first['items']
    assert len(ranker.calls) == 1
    with store.connect() as db:
        snapshot = {**store.run(two['id'])['snapshot'], 'language': 'zh-CN'}
        db.execute('UPDATE runs SET snapshot=? WHERE id=?', (json.dumps(snapshot), two['id']))
    localized = search(store, two['id'], 'identity', embedder=embedder, reranker_model=ranker)
    assert localized['items'][0]['title'] == '身份验证'
    assert len(ranker.calls) == 2
    assert len(store.rows('SELECT * FROM standard_search_cache')) == 2
    changed = FixtureRanker()
    changed.profile = {**changed.profile, 'revision': 'changed'}
    with pytest.raises(ValueError, match='重排模型'):
        search(store, one['id'], 'identity', embedder=embedder, reranker_model=changed)
    assert not changed.calls
    from security_auditor.retrieval import lexical_table
    table = lexical_table(one['snapshot']['standard']['prepared']['catalog_id'])
    with store.connect() as db:
        db.execute(f"UPDATE {table} SET content='tampered' WHERE rowid=1")
    with pytest.raises(ValueError, match='关键词索引摘要'):
        search(store, one['id'], 'identity', embedder=embedder, reranker_model=ranker)


def test_invalid_rerank_inventory_does_not_commit_search_cache(store, prepared_pack):
    root, _ = prepared_pack
    embedder, ranker = FixtureEncoder(), FixtureRanker()
    preparation(root, embedder, ranker)
    run = prepared_run(store, root)
    ranker.rerank = lambda *a: [float('nan')]
    with pytest.raises(ValueError, match='重排结果库存'):
        search(store, run['id'], 'identity', embedder=embedder, reranker_model=ranker)
    assert not store.rows('SELECT * FROM standard_search_cache')


def test_legacy_english_catalog_stays_queryable_without_lexical_or_rerank_rebuild(store, prepared_pack):
    from security_auditor.standard_controls import catalog_items
    root, controls = prepared_pack
    embedder = FixtureEncoder()
    prepared = preparation(root, embedder)
    clauses = [json.loads(line) for line in (root / 'clauses.jsonl').read_text().splitlines()]
    items = catalog_items(clauses, controls, 1)
    raw = ''.join(json.dumps({'item_type': item['item_type'], 'item_id': item['item_id'],
        'text_hash': digest(item['text'].encode()), 'vector': [1., 0., 0.]}) + '\n' for item in items)
    (root / 'legacy-vectors.jsonl').write_text(raw)
    old = {k: v for k, v in prepared.items() if k not in {'catalog_id', 'retrieval'}}
    old.update(schema_version=1, vectors_file='legacy-vectors.jsonl', vectors_hash=digest(raw.encode()), vector_count=4)
    old['catalog_id'] = digest(json.dumps(old, sort_keys=True).encode())
    (root / 'prepared.json').write_text(json.dumps(old))
    run = prepared_run(store, root)
    result = search(store, run['id'], 'identity', embedder=embedder)
    assert result['retrieval'] == 'PERSISTED_STANDARD_AND_CONTROL_VECTORS'
    assert len(result['items']) == 2
    assert not store.rows('SELECT * FROM standard_search_cache')


async def test_real_adk_selects_read_precomputed_controls_without_generating_requirements(settings, store, prepared_pack, monkeypatch):
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from pydantic import PrivateAttr
    from security_auditor.agents import AdkExecutor
    from security_auditor.skills import SkillLoader
    root, _ = prepared_pack
    embedder = FixtureEncoder()
    preparation(root, embedder)
    monkeypatch.setattr('security_auditor.embeddings.encoder', lambda *a: embedder)
    run = prepared_run(store, root)
    doc = store.add_evidence(run['id'], 'document', 'design.md:1', 'Verify identity.', {})
    tid = store.add_task(run['id'], 'requirements', {})
    store.note_read(tid, doc)
    req = Requirement(title='API identity', module='api', statement='Verify identity',
        acceptance_criteria=['Deny invalid identity'], origin='EXPLICIT_DESIGN', evidence_ids=[doc], rationale='Design')
    store.commit_output(tid, 'requirement_generator', StageOutput(records=[req], summary='Design'))
    scope = Scheduler(settings, store).scopes(run['id'], 'pci_mapper')[0]
    task = store.add_task(run['id'], 'pci_mapping', scope)
    bundle = SkillLoader(settings.skills_dir).resolve('pci_mapper', 'pci_mapper', '0.1.0')

    class CatalogModel(BaseLlm):
        model: str = 'prepared-catalog-fixture'
        _calls: int = PrivateAttr(default=0)

        async def generate_content_async(self, llm_request, stream=False):
            self._calls += 1
            if self._calls == 1:
                calls = [('read_evidence', {'evidence_id': doc}),
                         ('read_upstream_records', {'record_ids': [req.id]}),
                         ('search_standard_library', {'query': 'identity', 'section': '8', 'limit': 8, 'offset': 0})]
            elif self._calls == 2:
                calls = [('read_standard_control', {'control_id': 'control_8_3_1'})]
            else:
                declaration = next(d for tool in llm_request.config.tools for d in tool.function_declarations
                                   if d.name == 'set_model_response')
                props = declaration.parameters_json_schema['$defs']['Applicability']['properties']
                assert props['control_ids']['items']['enum'] == ['control_8_3_1']
                eid = store.evidence(run['id'], 'standard')[0]['id']
                result = Applicability(title='Identity control', clause_id='8.3.1', status='APPLICABLE',
                    relevance='RELEVANT', control_scope='CODE_RELATED', control_ids=['control_8_3_1'],
                    requirement_ids=[req.id], evidence_ids=[doc, eid], rationale='Identity matches the design')
                calls = [('set_model_response', {'records': [result.model_dump()], 'gaps': [], 'summary': 'Matched'})]
            yield LlmResponse(content=types.Content(role='model', parts=[
                types.Part(function_call=types.FunctionCall(name=name, args=args)) for name, args in calls]))

    model = CatalogModel()
    output = await AdkExecutor(settings, store, model_factory=lambda: model).execute(
        store.task(task), bundle, {'scope': scope, 'upstream_records': [req.model_dump()]})
    store.commit_output(task, 'pci_mapper', output)
    assert model._calls == 3
    assert store.records(run['id'], 'applicability')[0]['control_ids'] == ['control_8_3_1']
    assert not [r for r in store.records(run['id'], 'requirement') if r['origin'] == 'PCI_DSS']
    assert [purpose for purpose, _ in embedder.calls] == ['passage', 'query']


async def test_project_workflow_binds_prepared_controls_without_compliance_generation_calls(fixture_settings, store, prepared_pack):
    from fixture_executor import FixtureExecutor, FixtureMantis
    from security_auditor.ingestion import repository_inventory
    root, _ = prepared_pack
    embedder = FixtureEncoder()
    preparation(root, embedder)
    manifest = standard_manifest(root)
    manifest['library_id'] = import_library(store, manifest)
    roles = []

    class MatchingExecutor(FixtureExecutor):
        async def execute(self, task, bundle, context):
            roles.append(bundle.skill_id)
            if bundle.skill_id == 'pci_mapper':
                assert context['scope']['compliance_scope_policy'] == 'precomputed_controls_v3'
                found = search(store, task['run_id'], '身份验证', embedder=embedder)
                c, eid = read_control(store, task['run_id'], task['id'], found['items'][0]['control_id'])
                req = next(r for r in context['upstream_records'] if r['kind'] == 'requirement')
                doc = store.evidence(task['run_id'], 'document')[0]['id']
                for source in [doc, eid]:
                    store.note_read(task['id'], source)
                return StageOutput(records=[Applicability(title='Matched prepared control', clause_id=c['clause_id'],
                    status='UNDETERMINED', relevance='RELEVANT', control_scope='CODE_RELATED', control_ids=[c['id']],
                    requirement_ids=[req['id']], missing_facts=['CDE scope'], evidence_ids=[doc, eid], rationale='Fixture')], summary='Matched')
            return await super().execute(task, bundle, context)

    scheduler = Scheduler(fixture_settings, store, MatchingExecutor(store), FixtureMantis(fixture_settings, store))
    project = store.create_project('Workflow contract')
    source = fixture_settings.state_dir.parent / 'design.md'
    source.write_text('API validates identities.')
    asset = store.add_asset(project['id'], source.name, source, 'text/markdown')
    repo = next(iter(fixture_settings.repositories.values()))
    snapshot = {'assets': [asset], 'repository': {'root': repo, 'files': repository_inventory(Path(repo), 5000)},
        'standard': manifest, 'skill_hashes': {s['id']: s['sha256'] for s in scheduler.loader.catalog()},
        'workflow_hash': digest(fixture_settings.workflow.read_bytes())}
    run = store.create_run(project['id'], 'full', snapshot, False, 100, 200000)
    await scheduler.execute_run(run['id'])
    assert store.run(run['id'])['status'] == 'COMPLETED', store.run(run['id'])['error']
    assert 'pci_mapper' in roles and 'pci_requirement_generator' not in roles
    assert sum(purpose == 'passage' for purpose, _ in embedder.calls) == 1
    requirements = result_matrix(store, run['id'])['requirements']
    assert len(requirements) == 2 and all(r['checked_criteria'] == r['total_criteria'] for r in requirements)
    task = store.rows("SELECT * FROM tasks WHERE run_id=? AND stage='pci_requirements'", (run['id'],))[0]
    assert task['wave'] == 'service' and json.loads(task['result'])['model_calls'] == 0


async def test_prepared_deployment_control_retains_all_criteria_without_model_invocation(settings, store, sample_run):
    from security_auditor.domain import Assessment
    _, run, eid = sample_run
    task = store.add_task(run['id'], 'bound_control', {}, wave='service')
    req = Requirement(title='Enable deployed audit logs', module='api', statement='Enable deployed audit logs.',
        acceptance_criteria=['Audit logs enabled', 'Retention configured'], origin='PCI_DSS', clause_ids=['10.2.1'],
        standard_control_id='prepared-audit', verification_method='DEPLOYMENT', evidence_ids=[eid], rationale='Prepared control')
    with store.connect() as db:
        db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)', (req.id, run['id'], task, 'requirement', req.model_dump_json(), 'test'))

    class NoModel:
        async def execute(self, *args):
            raise AssertionError('A prepared deployment-only classification must not call a model')

    scheduler = Scheduler(settings, store, NoModel())
    parent = store.add_task(run['id'], 'requirement_check', {}, wave='stage')
    node = next(n for n in scheduler.spec['nodes'] if n['id'] == 'requirement_check')
    await scheduler.child(run['id'], node, parent, scheduler.scopes(run['id'], 'requirement_checker')[0])
    checks = store.records(run['id'], 'assessment')
    assert len(checks) == 2 and all(Assessment.model_validate(c).implementation_status == 'NOT_CODE_VERIFIABLE' for c in checks)
    assert store.run(run['id'])['usage']['requests'] == 0
    assert not store.records(run['id'], 'finding')
