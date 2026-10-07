from dataclasses import replace

import pytest

from security_auditor.domain import Applicability, Requirement, StageOutput
from security_auditor.provider import OutputContractError
from security_auditor.runtime import Scheduler
from security_auditor.skills import digest


def test_pci_batches_preserve_every_clause_and_parent_context(settings, store, sample_run):
    _, run, _ = sample_run
    context = store.add_evidence(run['id'], 'standard', 'scope', 'scope definitions', {'context_id': 'scope'})
    for i in range(19):
        store.add_evidence(run['id'], 'standard', str(i), 'formal requirement',
                           {'clause_id': f'PCI-{i}', 'context_ids': ['scope']})
    scopes = Scheduler(settings, store).scopes(run['id'], 'pci_mapper')
    clauses = [cid for scope in scopes for cid in scope['clause_ids']]
    assert len(scopes) == 3
    assert len(clauses) == len(set(clauses)) == 19
    assert set(clauses) == {f'PCI-{i}' for i in range(19)}
    assert all(scope['context_evidence_ids'] == [context] for scope in scopes)


@pytest.mark.parametrize('status,missing,generate,accepted', [
    ('UNDETERMINED', [], False, False),
    ('UNDETERMINED', ['CDE deployment scope'], False, True),
    ('UNDETERMINED', ['CDE deployment scope'], True, False),
    ('NOT_APPLICABLE', [], True, False),
    ('APPLICABLE', [], False, True),
    ('APPLICABLE', [], True, False),
])
def test_pci_requirements_need_actual_applicability_and_missing_facts(
        store, sample_run, status, missing, generate, accepted):
    _, run, _ = sample_run
    eid = store.add_evidence(run['id'], 'standard', '8.1', 'Formal clause', {'clause_id': '8.1'})
    tid = store.add_task(run['id'], 'pci_mapping', {'clause_ids': ['8.1']})
    store.start_task(tid)
    store.note_read(tid, eid)
    records = [Applicability(title='Scope decision', clause_id='8.1', status=status,
                             missing_facts=missing, rationale='Source-based decision', evidence_ids=[eid])]
    if generate:
        records.append(Requirement(title='Authentication', module='api', statement='Require authentication',
                                   acceptance_criteria=['Unauthenticated requests denied'], origin='PCI_DSS',
                                   clause_ids=['8.1'], rationale='Applicable clause', evidence_ids=[eid]))
    output = StageOutput(records=records, summary='Clause analysis')
    if accepted:
        store.commit_output(tid, 'pci_mapper', output)
        assert store.task(tid)['status'] == 'SUCCEEDED'
    else:
        with pytest.raises(ValueError):
            store.commit_output(tid, 'pci_mapper', output)
        assert not store.records(run['id'])


def test_pci_context_does_not_grow_with_previous_decisions(settings, store, sample_run):
    _, run, eid = sample_run
    scheduler = Scheduler(settings, store)
    before = scheduler.upstream_context(run['id'], 'pci_mapper', {})
    with store.connect() as db:
        for i in range(300):
            record = Applicability(id=f'prior-{i}', title='Previous unrelated clause', clause_id=str(i),
                                   status='UNDETERMINED', missing_facts=['scope'],
                                   evidence_ids=[eid], rationale='x' * 1000)
            db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',
                       (record.id, run['id'], 'previous', record.kind, record.model_dump_json(), 'test'))
    assert scheduler.upstream_context(run['id'], 'pci_mapper', {}) == before


async def test_invalid_first_batch_stops_queued_model_calls_but_keeps_inventory(settings, store):
    project = store.create_project('Document experiment')
    source = settings.state_dir / 'design.md'
    source.write_text('Authenticate requests.')
    asset = store.add_asset(project['id'], source.name, source, 'text/markdown')
    class FailingExecutor:
        calls = 0
        async def execute(self, *args):
            self.calls += 1
            raise OutputContractError('Truncated result')
    executor = FailingExecutor()
    scheduler = Scheduler(settings, store, executor)
    snapshot = {'assets': [asset], 'repository': None, 'standard': None,
                'skill_hashes': {s['id']: s['sha256'] for s in scheduler.loader.catalog()},
                'workflow_hash': digest(settings.workflow.read_bytes())}
    run = store.create_run(project['id'], 'requirements_only', snapshot, False, 500, 0)
    scheduler.scopes = lambda *args: [{'part': i} for i in range(7)]
    await scheduler.execute_run(run['id'])
    assert executor.calls == settings.concurrency == 2
    assert store.run(run['id'])['status'] == 'FAILED'
    children = store.rows("SELECT status FROM tasks WHERE run_id=? AND wave='analysis'", (run['id'],))
    assert len(children) == 7
    assert sum(t['status'] == 'PENDING' for t in children) == 5
    assert store.rows("SELECT status FROM tasks WHERE run_id=? AND wave='stage'", (run['id'],)) == [{'status': 'FAILED'}]


def test_output_allowance_is_separate_from_total_token_budget(settings):
    assert settings.agent_output_tokens == 16384
    assert replace(settings, max_tokens=0).agent_output_tokens == 16384
    for invalid in (4095, 32769):
        with pytest.raises(ValueError):
            replace(settings, agent_output_tokens=invalid)


def test_small_design_document_is_one_analysis_scope(settings, store, sample_run):
    _, run, _ = sample_run
    for i in range(10):
        store.add_evidence(run['id'], 'document', f'KEP:{i}', 'a' * 4800, {})
    scopes = Scheduler(settings, store).scopes(run['id'], 'design_analyst')
    assert len(scopes) == 1
    assert len(scopes[0]['evidence_ids']) == 10
    assert 'split_reason' not in scopes[0]


@pytest.mark.parametrize('status,generate,accepted', [
    ('UNDETERMINED', True, False), ('NOT_APPLICABLE', True, False),
    ('APPLICABLE', False, False), ('APPLICABLE', True, True)])
def test_compliance_requirements_have_a_separate_explicit_applicable_inventory(settings, store, sample_run, status, generate, accepted):
    _, run, _ = sample_run
    eid = store.add_evidence(run['id'], 'standard', '8.1', 'Formal clause', {'clause_id': '8.1'})
    decision = Applicability(title='Scope', clause_id='8.1', status=status, missing_facts=['deployment'], evidence_ids=[eid], rationale='source')
    first = store.add_task(run['id'], 'pci_mapping', {'clause_ids': ['8.1']})
    store.start_task(first)
    store.note_read(first, eid)
    store.commit_output(first, 'pci_mapper', StageOutput(records=[decision], summary='scope'))
    scopes = Scheduler(settings, store).scopes(run['id'], 'pci_requirement_generator')
    assert [cid for scope in scopes for cid in scope['clause_ids']] == (['8.1'] if status == 'APPLICABLE' else [])
    second = store.add_task(run['id'], 'pci_requirements', {'clause_ids': ['8.1']})
    store.start_task(second)
    store.note_read(second, eid)
    records = [Requirement(title='Authentication', module='api', statement='Require authentication',
                           acceptance_criteria=['Unauthenticated requests denied'], origin='PCI_DSS',
                           clause_ids=['8.1'], rationale='Applicable clause', evidence_ids=[eid])] if generate else []
    if accepted:
        store.commit_output(second, 'pci_requirement_generator', StageOutput(records=records, summary='requirements'))
        assert store.task(second)['status'] == 'SUCCEEDED'
    else:
        with pytest.raises(ValueError):
            store.commit_output(second, 'pci_requirement_generator', StageOutput(records=records, summary='requirements'))
        assert not store.records(run['id'], 'requirement')
    assert store.records(run['id'], 'applicability')[0]['status'] == status


def test_report_preserves_undetermined_compliance_and_unfinished_inventory():
    from security_auditor.product_report import product_report
    decision = Applicability(title='Scope', clause_id='8.1', status='UNDETERMINED',
                             missing_facts=['<script>CDE scope</script>'], evidence_ids=['source'], rationale='No payment deployment context')
    data = {'run': {'language': 'en'}, 'records': [decision.model_dump()], 'tasks': [
        {'stage': 'pci_mapping', 'scope': '{"clause_ids":["8.1","8.2"]}'}]}
    html = product_report('KEP', data, {'requirements': [], 'findings': []}, [])
    assert '1 / 2' in html and 'Undetermined 1' in html
    assert 'Applicable 0' in html and 'Not applicable 0' in html
    assert '&lt;script&gt;CDE scope&lt;/script&gt;' in html and '<script>' not in html
