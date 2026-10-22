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
