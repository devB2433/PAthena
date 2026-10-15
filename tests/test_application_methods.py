"""Exercise real tools and completion gates; no claim of LLM judgment accuracy."""
import json

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from security_auditor.agents import AdkExecutor
from security_auditor.provider import OutputContractError
from security_auditor.skills import SkillLoader


class PaginatedSearch(BaseLlm):
    model: str = 'method-protocol-test'
    _calls: int = PrivateAttr(default=0)
    _hits: list = PrivateAttr(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        self._calls += 1
        offset = 0
        done = False
        for part in llm_request.contents[-1].parts:
            if part.function_response:
                result = dict(part.function_response.response)
                assert result['total'] == 55 and result['read_required']
                self._hits.extend(result['items'])
                offset = result['next_offset']
                done = not result['has_more']
        if done:
            part = types.Part(text=json.dumps({'records': [], 'gaps': ['No semantic assessment in this test'],
                                              'summary': 'Source search pages complete'}))
        else:
            part = types.Part(function_call=types.FunctionCall(name='search_evidence', args={
                'query': 'shared_control', 'source_type': 'document', 'limit': 50, 'offset': offset}))
        yield LlmResponse(content=types.Content(role='model', parts=[part]))


async def test_registered_source_search_can_reach_matches_beyond_first_page(settings, store, sample_run):
    _, run, _ = sample_run
    for index in range(55):
        store.add_evidence(run['id'], 'document', f'design:{index:03d}', 'shared_control context', {})
    store.add_evidence(run['id'], 'code', 'not-a-document.py:1', 'shared_control', {})
    tid = store.add_task(run['id'], 'pci_mapping', {})
    bundle = SkillLoader(settings.skills_dir).resolve('pci_mapper', 'pci_mapper', '0.5.1')
    model = PaginatedSearch()
    output = await AdkExecutor(settings, store, model_factory=lambda: model).execute(store.task(tid), bundle, {})
    assert output.summary == 'Source search pages complete' and model._calls == 3
    assert len(model._hits) == len({row['id'] for row in model._hits}) == 55
    assert all(row['source_type'] == 'document' for row in model._hits)
    assert not store.rows('SELECT * FROM task_reads WHERE task_id=?', (tid,))


class DocumentCompletion(BaseLlm):
    model: str = 'method-completion-test'
    read_all: bool
    use_final_tool: bool
    _calls: int = PrivateAttr(default=0)
    _required: list = PrivateAttr(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        self._calls += 1
        if self._calls == 1:
            context = json.loads(llm_request.contents[0].parts[0].text)
            self._required = context['required_document_reads']
            assert len(self._required) == 2
            ids = self._required if self.read_all else self._required[:1]
            part = types.Part(function_call=types.FunctionCall(name='read_evidence_batch', args={'evidence_ids': ids}))
        else:
            payload = {'records': [], 'gaps': [], 'summary': 'No additional security requirements in test'}
            part = (types.Part(function_call=types.FunctionCall(name='set_model_response', args=payload))
                    if self.use_final_tool else types.Part(text=json.dumps(payload)))
        yield LlmResponse(content=types.Content(role='model', parts=[part]))


@pytest.mark.parametrize('role', ['design_analyst', 'requirement_generator'])
@pytest.mark.parametrize('read_all', [False, True])
@pytest.mark.parametrize('use_final_tool', [False, True])
async def test_document_completion_cannot_skip_readable_assigned_material(
        settings, store, sample_run, role, read_all, use_final_tool):
    _, run, _ = sample_run
    ids = [store.add_evidence(run['id'], 'document', f'design:{i}', f'Business flow {i}', {}) for i in range(2)]
    tid = store.add_task(run['id'], 'document_method', {'evidence_ids': ids} if role == 'design_analyst' else {})
    bundle = SkillLoader(settings.skills_dir).resolve(role, role, '0.5.1')
    model = DocumentCompletion(read_all=read_all, use_final_tool=use_final_tool)
    executor = AdkExecutor(settings, store, model_factory=lambda: model)
    if read_all:
        assert (await executor.execute(store.task(tid), bundle, {})).summary
    else:
        with pytest.raises(OutputContractError, match='设计材料尚未全部读取'):
            await executor.execute(store.task(tid), bundle, {})
    assert model._calls == 2  # An invalid final submission is never repaired by another model turn.
    assert not store.records(run['id'])
