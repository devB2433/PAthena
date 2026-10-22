"""Context transport regression; scripted outputs do not assess security quality."""
import json

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from security_auditor.agents import AdkExecutor
from security_auditor.domain import Fact, SourceObligation, SourceQuote
from security_auditor.runtime import Scheduler
from security_auditor.skills import SkillLoader
from security_auditor.store import now


def seed_large_facts(store, run_id):
    text = 'Exact original design statement. ' * 120
    eid = store.add_evidence(run_id, 'document', 'design.md:1', text, {})
    tid = store.add_task(run_id, 'design_model', {})
    facts = []
    for i in range(25):
        fact = Fact(id=f'fact_{i}', title=f'Original boundary {i}', module='api', fact_type='BOUNDARY',
                    basis='DECLARED', evidence_ids=[eid], rationale='Original rationale. ' * 70,
                    source_obligations=[SourceObligation(claim=f'Original boundary {i}',
                        quotes=[SourceQuote(evidence_id=eid, text=text)], subject='api', action='original',
                        modality='DECLARED', conditions=[], quantifier='UNSPECIFIED', trigger='',time_anchor='',exceptions=[])])
        facts.append(fact.model_dump())
    with store.connect() as db:
        for fact in facts:
            db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',
                       (fact['id'], run_id, tid, 'fact', json.dumps(fact,ensure_ascii=False), now()))
    return facts, eid


@pytest.mark.parametrize('role', ['requirement_generator', 'pci_mapper'])
def test_full_fact_inventory_fits_without_mutating_originals(settings,store,sample_run,role):
    _, run, _ = sample_run
    original,_ = seed_large_facts(store,run['id'])
    assert len(json.dumps(original).encode()) > 80000
    context=Scheduler(settings,store).upstream_context(run['id'],role,{'requirement_id':'none'})
    assert len(context)==len(original)
    assert len(json.dumps(context).encode()) < 20000
    assert [r['id'] for r in context]==[r['id'] for r in store.records(run['id'])]
    assert all(set(r)=={'id','kind','title','module','fact_type','basis','evidence_ids'} for r in context)
    assert store.records(run['id']) == original


class OriginalReadModel(BaseLlm):
    model:str='context-transport-fixture'
    _calls:int=PrivateAttr(default=0)
    _read_original:bool=PrivateAttr(default=False)

    async def generate_content_async(self,llm_request,stream=False):
        self._calls+=1
        context=json.loads(llm_request.contents[0].parts[0].text)
        assert len(context['upstream_records'])==25
        if self._calls==1:
            calls=[('read_upstream_records',{'record_ids':['fact_0']}),
                   ('read_evidence_batch',{'evidence_ids':context['required_document_reads']})]
        else:
            for part in llm_request.contents[-1].parts:
                if part.function_response and part.function_response.name=='read_upstream_records':
                    data=dict(part.function_response.response)
                    assert 'Original rationale.' in str(data)
                    assert 'Exact original design statement.' in str(data)
                    assert 'source_obligations' in str(data)
                    self._read_original=True
            calls=[('set_model_response',{'records':[],'gaps':[], 'summary':'Transport test only'})]
        yield LlmResponse(content=types.Content(role='model',parts=[types.Part(function_call=types.FunctionCall(
            name=name,args=args)) for name,args in calls]))


async def test_real_adk_reads_exact_large_fact_on_demand_and_original_document(settings,store,sample_run):
    _,run,_=sample_run
    original,eid=seed_large_facts(store,run['id'])
    role='requirement_generator'
    context={'scope':{},'upstream_records':Scheduler(settings,store).upstream_context(run['id'],role,{})}
    tid=store.add_task(run['id'],'requirements',{})
    model=OriginalReadModel()
    output=await AdkExecutor(settings,store,model_factory=lambda:model).execute(store.task(tid),
        SkillLoader(settings.skills_dir).resolve(role,role,'0.6.0'),context)
    assert output.summary=='Transport test only'
    assert model._calls==2 and model._read_original
    assert store.rows('SELECT evidence_id FROM task_reads WHERE task_id=?',(tid,))==[{'evidence_id':eid}]
    assert store.records(run['id'])==original


class MappingIdentityModel(BaseLlm):
    model: str = 'mapping-identity-fixture'
    sources: list[str]
    conflicting: bool = False
    _calls: int = PrivateAttr(default=0)

    async def generate_content_async(self, llm_request, stream=False):
        self._calls += 1
        if self._calls == 1:
            calls = [('read_evidence_batch', {'evidence_ids': self.sources})]
        else:
            declaration = next(d for t in llm_request.config.tools for d in t.function_declarations if d.name == 'set_model_response')
            props = declaration.parameters_json_schema['$defs']['ScopedApplicability']['properties']
            assert 'requirement_ids' not in props
            assert 'task_owned_mapping_provenance_v1' in str(llm_request.config.system_instruction)
            record = {'id':'negative_decision','kind':'applicability','title':'Transport fixture',
                      'clause_id':'8.2.1','status':'UNDETERMINED','relevance':'UNRELATED',
                      'control_scope':'CODE_RELATED','control_ids':[], 'evidence_ids':self.sources,
                      'applicability_conditions':[], 'missing_facts':['Formal scope unknown'],
                      'rationale':'Original excluded decision, not amended by the program'}
            if self.conflicting:
                record['requirement_ids'] = ['another_requirement']
            calls = [('set_model_response', {'records':[record],'gaps':[],'summary':'Original mapping output'})]
        yield LlmResponse(content=types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(
            name=name,args=args)) for name,args in calls]))


@pytest.mark.parametrize('conflicting', [False,True])
async def test_mapper_subject_is_owned_by_task_without_changing_excluded_verdict(settings,store,sample_run,conflicting):
    from security_auditor.domain import Requirement,StageOutput
    from security_auditor.provider import OutputContractError
    _,run,_=sample_run
    with store.connect() as db:
        db.execute('UPDATE runs SET snapshot=? WHERE id=?',(json.dumps({'standard':{'requirement_ids':['8.2.1']}}),run['id']))
    doc=store.add_evidence(run['id'],'document','design.md:1','Original fixture design.',{})
    standard=store.add_evidence(run['id'],'standard','PCI 8.2.1','Original fixture control.',{'clause_id':'8.2.1'})
    req=Requirement(id='subject_requirement',title='Existing requirement',module='api',statement='Original requirement',
                    acceptance_criteria=['Original criterion'],origin='EXPLICIT_DESIGN',evidence_ids=[doc],rationale='Fixture')
    seed=store.add_task(run['id'],'requirements',{})
    store.note_read(seed,doc)
    store.commit_output(seed,'requirement_generator',StageOutput(records=[req],summary='Original requirement'))
    scope={'requirement_id':req.id,'compliance_scope_policy':'code_related_v2'}
    tid=store.add_task(run['id'],'pci_mapping',scope)
    model=MappingIdentityModel(sources=[doc,standard],conflicting=conflicting)
    executor=AdkExecutor(settings,store,model_factory=lambda:model)
    bundle=SkillLoader(settings.skills_dir).resolve('pci_mapper','pci_mapper','0.6.1')
    if conflicting:
        with pytest.raises(OutputContractError):
            await executor.execute(store.task(tid),bundle,{'scope':scope,'upstream_records':[req.model_dump()]})
        assert not store.records(run['id'],'applicability')
    else:
        output=await executor.execute(store.task(tid),bundle,{'scope':scope,'upstream_records':[req.model_dump()]})
        store.commit_output(tid,'pci_mapper',output)
        result=store.records(run['id'],'applicability')[0]
        assert result['requirement_ids']==[req.id]
        assert result['status']=='UNDETERMINED' and result['relevance']=='UNRELATED'
        assert result['rationale']=='Original excluded decision, not amended by the program'
        assert result['missing_facts']==['Formal scope unknown']
        receipts=[json.loads(r['content']) for r in store.rows('SELECT content FROM model_outputs WHERE task_id=?',(tid,)) if r['content'].startswith('{')]
        assert any(r.get('transport')=='task_owned_mapping_provenance_v1' for r in receipts)
    assert model._calls==2  # Invalid associations are not sent for a repair inference.


@pytest.mark.parametrize('control_ids,clause_id', [(['different_control'],'8.3.1'),(['subject_control'],'8.3.2'),([], '8.3.1')])
def test_fixed_control_transport_rejects_conflicting_echoes(control_ids,clause_id):
    from security_auditor.agents import scoped_control_submission_model
    from pydantic import ValidationError
    record={'id':'decision','title':'Fixture','rationale':'Original verdict','kind':'applicability',
            'status':'NOT_APPLICABLE','relevance':'UNRELATED','control_scope':'NON_CODE',
            'control_ids':control_ids,'clause_id':clause_id}
    with pytest.raises(ValidationError):
        scoped_control_submission_model('subject_control','8.3.1').model_validate(
            {'records':[record],'gaps':[],'summary':'Fixture'})
