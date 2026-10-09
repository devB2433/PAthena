import json
from collections import Counter

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from security_auditor.agents import AdkExecutor
from security_auditor.domain import Applicability, Requirement, StageOutput
from security_auditor.runtime import Scheduler, result_matrix
from security_auditor.skills import SkillLoader, digest
from security_auditor.standard_library import import_library, read, search
from security_auditor.provider import OutputContractError


@pytest.mark.parametrize('scope,relevance,status,include', [
    ('CODE_RELATED', 'RELEVANT', 'APPLICABLE', True),
    ('CODE_RELATED', 'POTENTIALLY_RELEVANT', 'UNDETERMINED', True),
    ('CODE_RELATED', 'UNRELATED', 'UNDETERMINED', False),
    ('CODE_RELATED', 'RELEVANT', 'NOT_APPLICABLE', False),
    ('NON_CODE', 'RELEVANT', 'APPLICABLE', False),
    ('UNKNOWN', 'POTENTIALLY_RELEVANT', 'UNDETERMINED', False),
])
def test_code_relationship_is_independent_of_formal_compliance_scope(scope, relevance, status, include):
    from security_auditor.domain import compliance_candidate
    assert compliance_candidate({'control_scope': scope, 'relevance': relevance, 'status': status}) == include


def test_excluded_controls_never_create_requirements_or_code_check_tasks(settings, store, catalog):
    run, _, _, doc, req = catalog
    sources = [read(store, run['id'], cid)[1] for cid in ['8.3.1', '8.3.2']]
    task = store.add_task(run['id'], 'pci_mapping', {
        'requirement_id': req.id, 'compliance_scope_policy': 'code_related_v2'})
    for eid in [doc, *sources]:
        store.note_read(task, eid)
    decisions = [Applicability(title=title, clause_id=cid, status='UNDETERMINED',
        relevance='RELEVANT', control_scope=scope, requirement_ids=[req.id],
        missing_facts=['Deployment scope'], evidence_ids=[doc, eid], rationale=reason)
        for title, cid, eid, scope, reason in [
            ('API identity verification', '8.3.1', sources[0], 'CODE_RELATED', 'API verifies identities'),
            ('Excluded organization control', '8.3.2', sources[1], 'NON_CODE', 'Outside assessed subsystem')]]
    store.commit_output(task, 'pci_mapper', StageOutput(records=decisions, summary='Scoped controls'))
    scheduler = Scheduler(settings, store)
    assert [cid for s in scheduler.scopes(run['id'], 'pci_requirement_generator') for cid in s['clause_ids']] == ['8.3.1']
    assert len(scheduler.scopes(run['id'], 'requirement_checker')) == 1
    assert len(store.records(run['id'], 'applicability')) == 2  # Retain the exclusion rationale.
    assert [m['clause_id'] for m in result_matrix(store, run['id'])['requirements'][0]['compliance_matches']] == ['8.3.1']
    rejected_task = store.add_task(run['id'], 'pci_requirements', {'clause_ids': ['8.3.2']})
    control = Requirement(title='Should not be generated', module='api', statement='Out-of-scope control',
        acceptance_criteria=['Interview administrator'], origin='PCI_DSS', clause_ids=['8.3.2'],
        rationale='Wrong target', evidence_ids=[sources[1]])
    store.note_read(rejected_task, sources[1])
    with pytest.raises(ValueError, match='合规需求任务'):
        store.commit_output(rejected_task, 'pci_requirement_generator', StageOutput(records=[control], summary='Wrong'))


def test_new_mapping_cannot_omit_code_relationship(store, catalog):
    run, _, _, doc, req = catalog
    eid = read(store, run['id'], '8.3.1')[1]
    task = store.add_task(run['id'], 'pci_mapping', {
        'requirement_id': req.id, 'compliance_scope_policy': 'code_related_v2'})
    for source in [doc, eid]:
        store.note_read(task, source)
    decision = Applicability(title='Missing scope', clause_id='8.3.1', status='UNDETERMINED',
        relevance='RELEVANT', requirement_ids=[req.id], missing_facts=['Deployment scope'],
        evidence_ids=[doc, eid], rationale='Missing technical scope')
    with pytest.raises(ValueError, match='代码范围'):
        store.commit_output(task, 'pci_mapper', StageOutput(records=[decision], summary='Incomplete decision'))
    assert not store.records(run['id'], 'applicability')


@pytest.fixture
def catalog(settings, store, sample_run):
    _, run, _ = sample_run
    root = settings.state_dir/'standard-fixture'
    root.mkdir()
    clauses = [{'id':cid, 'parent_id':cid.rsplit('.',1)[0], 'parent_context':'Identity and logging',
                'defined_approach':normative, 'applicability_notes':'Applies to in-scope systems',
                'testing_procedures':'Examine the declared configuration', 'guidance':'Examples are not normative',
                'source':{'file':'fixture.pdf','pdf_pages':[page],'context_ids':['scope']},
                'text':normative+'\nTesting: Examine configuration\nGuidance: examples'}
               for cid,normative,page in [('8.3.1','Authenticate identities before granting access',8),
                                          ('8.3.2','Protect authentication credentials during transmission',9),
                                          ('10.2.1','Record audit events for privileged activity',10)]]
    raw=''.join(json.dumps(c)+'\n' for c in clauses).encode()
    (root/'clauses.jsonl').write_bytes(raw)
    context=json.dumps({'id':'scope','text':'Deployment determines CDE scope',
                        'source':{'file':'fixture.pdf','pdf_pages':[1]}}).encode()
    (root/'contexts.jsonl').write_bytes(context)
    manifest={'root':str(root),'version':'4.0.1','manifest_hash':'fixture-hash',
              'clauses_hash':digest(raw),'contexts_hash':digest(context),
              'requirement_ids':[c['id'] for c in clauses], 'context_ids':['scope']}
    library_id=import_library(store,manifest)
    snapshot={'standard':{**manifest,'library_id':library_id},'standard_use_policy':'structured_library_requirement_matching_v1'}
    with store.connect() as db:
        db.execute('UPDATE runs SET snapshot=? WHERE id=?',(json.dumps(snapshot),run['id']))
    doc=store.add_evidence(run['id'],'document','design.md:1','Validate JWT identities before API access.',{})
    tid=store.add_task(run['id'],'requirements',{})
    store.note_read(tid,doc)
    req=Requirement(title='JWT authentication',module='api',statement='Validate JWT identity.',
                    acceptance_criteria=['Invalid JWT is denied'],origin='EXPLICIT_DESIGN',
                    evidence_ids=[doc],rationale='Design requirement')
    store.commit_output(tid,'requirement_generator',StageOutput(records=[req],summary='Requirements'))
    return run, manifest, library_id, doc, req


def test_shared_catalog_is_structured_idempotent_and_search_does_not_cite(store, catalog):
    run,manifest,key,_,_=catalog
    assert import_library(store,manifest)==key
    assert len(store.rows('SELECT * FROM standard_versions'))==1
    assert len(store.rows('SELECT * FROM standard_clauses'))==3
    found=search(store,run['id'],'authentication','8',limit=1)
    assert found['items'][0]['clause_id']=='8.3.2' and found['items'][0]['read_required']
    assert not store.evidence(run['id'],'standard')
    clause,eid=read(store,run['id'],'8.3.1')
    assert clause['defined_approach'] != clause['testing_procedures']
    assert store.read_evidence(run['id'],eid)['metadata']['source']['pdf_pages']==[8]
    assert len(store.evidence(run['id'],'standard'))==1
    assert read(store,run['id'],'8.3.1')[1]==eid
    assert search(store,run['id'],'audit','8')['items']==[]


def test_catalog_version_and_content_tampering_cannot_create_sources(store,catalog):
    run,_,key,_,_=catalog
    with store.connect() as db:
        db.execute("UPDATE standard_clauses SET payload='{}' WHERE pack_id=? AND clause_id='8.3.1'",(key,))
    with pytest.raises(ValueError,match='摘要'):
        read(store,run['id'],'8.3.1')
    assert not store.evidence(run['id'],'standard')
    with pytest.raises(ValueError,match='不属于'):
        read(store,run['id'],'99.1')


def test_related_scope_unknown_enters_code_check_inventory_without_claiming_applicable(settings,store,catalog):
    run,_,_,doc,req=catalog
    scheduler=Scheduler(settings,store)
    assert scheduler.scopes(run['id'],'pci_mapper')==[{'requirement_id':req.id,'compliance_scope_policy':'code_related_v2'}]
    _,eid=read(store,run['id'],'8.3.1')
    task=store.add_task(run['id'],'pci_mapping',{'requirement_id':req.id,
                                             'compliance_scope_policy':'code_related_v2'})
    for source in [doc,eid]:
        store.note_read(task,source)
    match=Applicability(title='Related access control',clause_id='8.3.1',status='UNDETERMINED',
        relevance='POTENTIALLY_RELEVANT',control_scope='CODE_RELATED',requirement_ids=[req.id],
                        applicability_conditions=['If deployed into the CDE'],missing_facts=['Deployment scope'],
                        rationale='Authentication control matches the design; CDE scope is unknown',evidence_ids=[doc,eid])
    store.commit_output(task,'pci_mapper',StageOutput(records=[match],summary='Matched'))
    scopes=scheduler.scopes(run['id'],'pci_requirement_generator')
    assert [cid for s in scopes for cid in s['clause_ids']]==['8.3.1']
    upstream=scheduler.upstream_context(run['id'],'pci_requirement_generator',scopes[0])
    projected=next(r for r in upstream if r['kind']=='applicability')
    assert 'rationale' not in projected
    assert projected['id']==match.id and 'missing_facts' not in projected
    assert projected['applicability_conditions']==match.applicability_conditions
    assert store.records(run['id'],'applicability')[0]['rationale']==match.rationale
    new=Requirement(title='Conditional authentication control',module='api',
                    statement='For in-scope deployment authenticate identities.',acceptance_criteria=['Verify identity'],
                    origin='PCI_DSS',clause_ids=['8.3.1'],rationale='Candidate control',evidence_ids=[eid])
    tid=store.add_task(run['id'],'pci_requirements',scopes[0])
    store.note_read(tid,eid)
    store.commit_output(tid,'pci_requirement_generator',StageOutput(records=[new],summary='Conditional requirements'))
    assert len(scheduler.scopes(run['id'],'requirement_checker'))==2
    assert Counter(r['origin'] for r in store.records(run['id'],'requirement'))=={'EXPLICIT_DESIGN':1,'PCI_DSS':1}
    assert result_matrix(store,run['id'])['requirements'][0]['compliance_matches'][0]['status']=='UNDETERMINED'


@pytest.mark.parametrize('bad_source', [False, True])
async def test_real_adk_reads_catalog_and_directly_commits_requirement_clause_match(settings,store,catalog,bad_source):
    run,_,_,doc,req=catalog
    task=store.add_task(run['id'],'pci_mapping',{'requirement_id':req.id,
                                             'compliance_scope_policy':'code_related_v2'})
    bundle=SkillLoader(settings.skills_dir).resolve('pci_mapper','pci_mapper','0.1.0')
    class CatalogModel(BaseLlm):
        model:str='structured-catalog-fixture'
        _calls:int=PrivateAttr(default=0)
        async def generate_content_async(self,llm_request,stream=False):
            self._calls+=1
            declaration = next(d for tool in llm_request.config.tools for d in tool.function_declarations
                               if d.name == 'set_model_response')
            fields = declaration.parameters_json_schema['$defs']['Applicability']
            assert 'control_scope' in fields['required']
            assert fields['properties']['control_scope']['enum'] == ['CODE_RELATED', 'NON_CODE', 'UNKNOWN']
            if self._calls==1:
                calls=[('read_evidence',{'evidence_id':doc}),('read_upstream_records',{'record_ids':[req.id]}),
                       ('search_standard_library',{'query':'authentication','section':'8','limit':8,'offset':0})]
            elif self._calls==2:
                calls=[('read_standard_clause',{'clause_id':'8.3.1'})]
            else:
                eid=store.evidence(run['id'],'standard')[0]['id']
                args=Applicability(id='catalog-match',title='Matched requirement',clause_id='8.3.1',
                     status='UNDETERMINED',relevance='RELEVANT',control_scope='CODE_RELATED',requirement_ids=[req.id],
                     applicability_conditions=['In-scope deployment'],missing_facts=['CDE scope'],
                     evidence_ids=[doc,'8.3.1' if bad_source else eid],rationale='Related identity control; deployment unknown').model_dump()
                calls=[('set_model_response',{'records':[args],'gaps':[],'summary':'Matched to structured data'})]
            yield LlmResponse(content=types.Content(role='model',parts=[types.Part(function_call=types.FunctionCall(name=n,args=a)) for n,a in calls]))
    model=CatalogModel()
    if bad_source:
        with pytest.raises(OutputContractError, match='来源编号'):
            await AdkExecutor(settings,store,model_factory=lambda:model).execute(
                store.task(task),bundle,{'scope':{'requirement_id':req.id},'upstream_records':[req.model_dump()]})
        assert model._calls==3
        assert not store.records(run['id'],'applicability')
        assert any('8.3.1' in r['content'] for r in store.rows('SELECT * FROM model_outputs WHERE task_id=?',(task,)))
        return
    output=await AdkExecutor(settings,store,model_factory=lambda:model).execute(
        store.task(task),bundle,{'scope':{'requirement_id':req.id},'upstream_records':[req.model_dump()]})
    store.commit_output(task,'pci_mapper',output)
    assert model._calls==3
    assert store.records(run['id'],'applicability')[0]['requirement_ids']==[req.id]
    assert len(store.rows('SELECT * FROM task_reads WHERE task_id=?',(task,)))==2


@pytest.mark.parametrize('committed', [False, True])
def test_atomic_control_inventory_preserves_success_and_archives_replaced_failures(settings,store,catalog,committed):
    run,_,_,doc,req=catalog
    sources=[read(store,run['id'],cid)[1] for cid in ['8.3.1','8.3.2']]
    mapping=store.add_task(run['id'],'pci_mapping',{'requirement_id':req.id})
    for eid in [doc,*sources]:
        store.note_read(mapping,eid)
    matches=[Applicability(title='Candidate control',clause_id=cid,status='UNDETERMINED',
                           relevance='RELEVANT',requirement_ids=[req.id],missing_facts=['Deployment scope'],
                           applicability_conditions=['In-scope deployment'],evidence_ids=[doc,eid],rationale='Related control')
             for cid,eid in zip(['8.3.1','8.3.2'],sources)]
    store.commit_output(mapping,'pci_mapper',StageOutput(records=matches,summary='Matches'))
    parent=store.add_task(run['id'],'pci_requirements',{'stage':'pci_requirements'},wave='stage')
    old_scope={'clause_ids':['8.3.1','8.3.2'],'evidence_ids':sources,'context_evidence_ids':[]}
    old=store.add_task(run['id'],'pci_requirements',old_scope,parent)
    archived=store.save_model_output(old,'original provider response')
    reservation=store.reserve(run['id'],old,100)
    store.settle(reservation,7)
    before_usage=store.run(run['id'])['usage']
    if committed:
        for eid in sources:
            store.note_read(old,eid)
        control=Requirement(title='Authentication protection',module='api',statement='For in-scope access protect identities.',
                            acceptance_criteria=['Control is present'],origin='PCI_DSS',clause_ids=old_scope['clause_ids'],
                            evidence_ids=sources,rationale='Normative controls')
        store.commit_output(old,'pci_requirement_generator',StageOutput(records=[control],summary='Committed controls'))
    else:
        store.fail_task(old,'Missing exact clause citation')
    scheduler=Scheduler(settings,store)
    scopes=scheduler.scopes(run['id'],'pci_requirement_generator')
    for scope in scopes:
        store.add_task(run['id'],'pci_requirements',scope,parent)
    scheduler.retire_replaced_clause_batches(run['id'],parent,scopes)
    assert store.run(run['id'])['usage']==before_usage
    assert store.rows('SELECT content FROM model_outputs WHERE id=?',(archived,))[0]['content']=='original provider response'
    if committed:
        assert scopes==[old_scope] and store.task(old)['status']=='SUCCEEDED'
        assert store.records(run['id'],'requirement')[-1]['id']==control.id
    else:
        assert all(len(s['clause_ids'])==1 for s in scopes)
        receipt=store.task(old)['result']
        assert store.task(old)['status']=='SKIPPED' and receipt['superseded']
        assert receipt['original_error']=='Missing exact clause citation'
        assert {cid for tid in receipt['replaced_by'] for cid in store.task(tid)['scope']['clause_ids']}==set(old_scope['clause_ids'])


@pytest.mark.parametrize('case', ['valid','exact_echo','missing_statement','foreign_clause'])
async def test_atomic_provenance_is_task_owned_and_never_repairs_analytical_output(settings,store,catalog,case):
    run,_,_,doc,req=catalog
    _,eid=read(store,run['id'],'8.3.1')
    mapping=store.add_task(run['id'],'pci_mapping',{'requirement_id':req.id})
    for source in [doc,eid]:
        store.note_read(mapping,source)
    match=Applicability(title='Related control',clause_id='8.3.1',status='UNDETERMINED',
                        relevance='RELEVANT',requirement_ids=[req.id],missing_facts=['CDE scope'],
                        applicability_conditions=['In-scope deployment'],rationale='Conditional control',evidence_ids=[doc,eid])
    store.commit_output(mapping,'pci_mapper',StageOutput(records=[match],summary='Matched'))
    scheduler=Scheduler(settings,store)
    scope=scheduler.scopes(run['id'],'pci_requirement_generator')[0]
    tid=store.add_task(run['id'],'pci_requirements',scope)
    bundle=SkillLoader(settings.skills_dir).resolve('pci_requirement_generator','pci_requirement_generator','0.1.0')
    expected=Requirement(id='owned-result',title='Authenticate identity',module='api',
                         statement='For in-scope deployment authenticate identity.',acceptance_criteria=['Identity is authenticated'],
                         origin='PCI_DSS',clause_ids=['8.3.1'],evidence_ids=[doc,eid],rationale='Original normative control')
    raw=expected.model_dump(exclude_none=True)
    raw.pop('origin')
    raw.pop('clause_ids')
    if case=='missing_statement':
        raw.pop('statement')
    elif case=='foreign_clause':
        raw['clause_ids']=['999.1']
    elif case=='exact_echo':
        raw.update(origin='PCI_DSS',clause_ids=['8.3.1'])
    class OwnedModel(BaseLlm):
        model:str='task-owned-fixture'
        _calls:int=PrivateAttr(default=0)
        async def generate_content_async(self,llm_request,stream=False):
            self._calls+=1
            declaration=next(d for tool in llm_request.config.tools for d in tool.function_declarations if d.name=='set_model_response')
            fields=declaration.parameters_json_schema['$defs']['ScopedPCIRequirement']['properties']
            assert not {'origin','clause_ids'} & fields.keys()
            yield LlmResponse(content=types.Content(role='model',parts=[types.Part(function_call=types.FunctionCall(
                name='set_model_response',args={'records':[raw],'gaps':[],'summary':'Complete typed result'}))]))
    model=OwnedModel()
    executor=AdkExecutor(settings,store,model_factory=lambda:model)
    context={'scope':scope,'upstream_records':scheduler.upstream_context(run['id'],'pci_requirement_generator',scope)}
    if case not in {'valid','exact_echo'}:
        with pytest.raises(OutputContractError):
            await executor.execute(store.task(tid),bundle,context)
        assert not store.rows('SELECT * FROM records WHERE task_id=?',(tid,))
    else:
        output=await executor.execute(store.task(tid),bundle,context)
        assert output.records[0].model_dump()==expected.model_dump()
        store.commit_output(tid,'pci_requirement_generator',output)
    assert model._calls==1
    archives=store.rows('SELECT content FROM model_outputs WHERE task_id=?',(tid,))
    assert any(json.loads(r['content']).get('records')==[raw] for r in archives)
    assert any(json.loads(r['content']).get('transport')=='task_owned_requirement_provenance_v1' for r in archives)
