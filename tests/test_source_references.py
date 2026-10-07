import pytest

from security_auditor.provider import OutputContractError
from security_auditor.source_references import SourceReferences


def test_exact_source_projection_preserves_content_and_never_fuzzy_matches():
    refs = SourceReferences()
    refs.register(['original-source-abc', 'original-source-def'])
    refs.register(['original-source-abc', 'original-source-ghi'])
    assert refs.by_handle == {'S0001': 'original-source-abc', 'S0002': 'original-source-def',
                              'S0003': 'original-source-ghi'}
    wire = {'records': [{'id': 'requirement-1', 'evidence_ids': ['S0002'],
                         'counter_evidence_ids': ['S0001'], 'statement': 'S0002 is an exact quote',
                         'requirement_id': 'requirement-0'}], 'summary': 'Review', 'gaps': []}
    result = refs.decode_submission(wire)
    assert result['records'][0]['evidence_ids'] == ['original-source-def']
    assert result['records'][0]['counter_evidence_ids'] == ['original-source-abc']
    assert result['records'][0]['statement'] == wire['records'][0]['statement']
    assert result['records'][0]['requirement_id'] == 'requirement-0'
    assert wire['records'][0]['evidence_ids'] == ['S0002']
    for bad in ['S001', 'S9999', 'original-source-ab', 'original-source-abd']:
        with pytest.raises(OutputContractError):
            refs.decode_submission({'records': [{'evidence_ids': [bad]}]})


async def test_adk_direct_short_source_submission_persists_original_ids(settings, store, sample_run):
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from pydantic import PrivateAttr
    from security_auditor.agents import AdkExecutor
    from security_auditor.skills import SkillLoader
    _, run, _ = sample_run
    eid = store.add_evidence(run['id'], 'standard', 'PCI:1.1', 'Formal requirement', {'clause_id': '1.1'})
    tid = store.add_task(run['id'], 'pci_mapping', {'clause_ids': ['1.1'], 'evidence_ids': [eid]})
    store.start_task(tid)

    class Model(BaseLlm):
        model: str = 'source-handle-test'
        _calls: int = PrivateAttr(default=0)

        async def generate_content_async(self, request, stream=False):
            import json
            self._calls += 1
            assert self._calls == 1
            context = json.loads(request.contents[0].parts[0].text)
            handle = context['target_references']['1.1']
            assert handle == 'S0001'
            assert eid not in json.dumps(context)
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(
                function_call=types.FunctionCall(name='set_model_response', args={
                    'records': [{'id': 'app-1', 'kind': 'applicability', 'title': '范围待确认',
                                 'clause_id': '1.1', 'status': 'UNDETERMINED',
                                 'missing_facts': ['持卡人数据环境范围'], 'evidence_ids': [handle],
                                 'rationale': '文档未声明部署范围'}], 'gaps': [], 'summary': '适用性待确认'}))]))
    model = Model()
    bundle = SkillLoader(settings.skills_dir).resolve('pci_mapper', 'pci_mapper', '0.1.0')
    output = await AdkExecutor(settings, store, model_factory=lambda: model).execute(store.task(tid), bundle, {})
    store.commit_output(tid, 'pci_mapper', output)
    assert store.records(run['id'])[0]['evidence_ids'] == [eid]
    assert store.records(run['id'])[0]['rationale'] == '文档未声明部署范围'
    assert model._calls == 1


@pytest.mark.parametrize('case',['omitted_links','exact_echo','foreign_assessment','foreign_finding','missing_criterion','stale_citation'])
async def test_checker_searches_paths_filters_before_limit_and_rebinds_read_sources(settings, store, sample_run,case):
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from pydantic import PrivateAttr
    from security_auditor.agents import AdkExecutor
    from security_auditor.domain import Requirement, StageOutput
    from security_auditor.skills import SkillLoader
    _, run, _ = sample_run
    for index in range(51):
        store.add_evidence(run['id'], 'document', f'A-doc-{index:03d}', 'needle design claim', {})
    code = store.add_evidence(run['id'], 'code', 'auth.go:1', 'func needle() {}', {})
    document = store.add_evidence(run['id'], 'document', 'design.md:1', 'Required original design material', {})
    stale = store.add_evidence(run['id'],'code','previous.go:1','func previous() {}',{})
    req = Requirement(id='req-original', title='Auth', module='Auth', statement='Validate',
                      acceptance_criteria=['Keep the exact criterion.'], origin='EXPLICIT_DESIGN',
                      evidence_ids=[document], rationale='Original criterion')
    generator = store.add_task(run['id'], 'requirements', {})
    store.note_read(generator, document)
    store.commit_output(generator, 'requirement_generator', StageOutput(records=[req], summary='Requirement'))
    tid = store.add_task(run['id'], 'requirement_check',
                         {'requirement_id': req.id, 'acceptance_criteria': req.acceptance_criteria})
    store.start_task(tid)
    # A prior failed invocation's reads must not unlock a fresh model session.
    store.note_read(tid, code)
    store.note_read(tid, stale)

    class Model(BaseLlm):
        model: str = 'read-enum-test'
        _calls: int = PrivateAttr(default=0)
        _handle: str = PrivateAttr(default='')

        async def generate_content_async(self, request, stream=False):
            self._calls += 1
            if self._calls==1:
                assert 'Required original design material' in ''.join(c.model_dump_json() for c in request.contents)
                assert store.rows('SELECT 1 FROM task_reads WHERE task_id=? AND evidence_id=?',(tid,document))
            declaration = next(d for t in request.config.tools for d in (t.function_declarations or [])
                               if d.name=='set_model_response')
            status_enum=declaration.parameters_json_schema['$defs']['ScopedAssessment']['properties']['implementation_status']['enum']
            if self._calls<=3:
                assert status_enum==['UNKNOWN','EXTERNAL_EVIDENCE_REQUIRED']
            else:
                assert 'STATIC_SUPPORTED' in status_enum
            if self._calls in (2, 3):
                responses = [p.function_response for c in request.contents for p in c.parts if p.function_response]
                items = responses[-1].response['items']
                assert len(items) == 1 and items[0]['locator'] == 'auth.go:1'
                self._handle = items[0]['id']
            if self._calls in (1, 2):
                name, args = 'search_evidence', {'query': 'needle' if self._calls == 1 else 'auth.go',
                                               'source_type': 'code', 'limit': 1}
            elif self._calls == 3:
                name, args = 'read_evidence', {'evidence_id': self._handle}
            else:
                assert self._calls == 4
                final = next(d for t in request.config.tools for d in (t.function_declarations or [])
                             if d.name == 'set_model_response')
                fields = final.parameters_json_schema['$defs']['ScopedAssessment']['properties']
                assert fields['id']['pattern'] == '^'+tid+'_[a-zA-Z0-9_-]+$'
                assert self._handle in fields['evidence_ids']['items']['enum']
                assert fields['acceptance_criterion']['enum'] == req.acceptance_criteria
                assert 'requirement_id' not in fields
                assert 'requirement_ids' not in final.parameters_json_schema['$defs']['ScopedFinding']['properties']
                name, args = 'set_model_response', {
                    'records': {'assessment': [{'id':tid+'_assessment_1','title':'Auth','kind':'assessment',
                        'acceptance_criterion':req.acceptance_criteria[0],
                        'module':'Auth','entrypoint':'needle','design_status':'UNKNOWN',
                        'implementation_status':'STATIC_SUPPORTED','evidence_ids':[self._handle],
                        'rationale':'Read original source','counter_evidence_ids':[]}], 'finding':[]},
                    'summary':'Submitted unchanged','gaps':[]}
                finding={'id':tid+'_finding_1','title':'More source required','kind':'finding',
                         'module':'Auth','finding_type':'IMPLEMENTATION_GAP','status':'NEEDS_EVIDENCE',
                         'attack_preconditions':[],'impact':'Potential unverified path','recommendation':'Inspect remaining path',
                         'evidence_ids':[self._handle],'rationale':'Original candidate text'}
                args['records']['finding']=[finding]
                assessment=args['records']['assessment'][0]
                if case=='exact_echo':
                    assessment['requirement_id']=req.id
                    finding['requirement_ids']=[req.id]
                elif case=='foreign_assessment':
                    assessment['requirement_id']='foreign'
                elif case=='foreign_finding':
                    finding['requirement_ids']=['foreign']
                elif case=='missing_criterion':
                    assessment.pop('acceptance_criterion')
                elif case=='stale_citation':
                    assessment['evidence_ids']=[stale]
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(
                function_call=types.FunctionCall(name=name, args=args))]))
    model = Model()
    bundle = SkillLoader(settings.skills_dir).resolve('requirement_checker','requirement_checker','0.1.0')
    executor=AdkExecutor(settings,store,model_factory=lambda:model)
    if case in {'omitted_links','exact_echo'}:
        output = await executor.execute(store.task(tid),bundle,{})
        store.commit_output(tid,'requirement_checker',output)
        assert store.records(run['id'],'assessment')[0]['evidence_ids'] == [code]
        assert store.records(run['id'],'assessment')[0]['requirement_id']==req.id
        assert store.records(run['id'],'assessment')[0]['acceptance_criterion']==req.acceptance_criteria[0]
        assert store.records(run['id'],'finding')[0]['requirement_ids']==[req.id]
        assert store.records(run['id'],'finding')[0]['rationale']=='Original candidate text'
    else:
        with pytest.raises(OutputContractError):
            await executor.execute(store.task(tid),bundle,{})
        assert not store.rows('SELECT 1 FROM records WHERE task_id=?',(tid,))
    assert any('task_owned_check_provenance_v1' in row['content'] for row in
               store.rows('SELECT content FROM model_outputs WHERE task_id=?',(tid,)))
    assert model._calls == 4
