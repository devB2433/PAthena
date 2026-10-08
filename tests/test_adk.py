import json

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from security_auditor.agents import AdkExecutor
from security_auditor.provider import OutputContractError
import pytest
from security_auditor.domain import ALLOWED_KINDS, StageOutput
from security_auditor.skills import SkillLoader, compile_instruction


class ScriptedModel(BaseLlm):
    model: str = "scripted-test"
    evidence_id: str
    _calls: int = PrivateAttr(default=0)
    _instructions: list[str] = PrivateAttr(default_factory=list)
    _tool_response: str = PrivateAttr(default="")

    async def generate_content_async(self, llm_request, stream=False):
        self._calls += 1
        self._instructions.append(str(llm_request.config.system_instruction))
        if self._calls == 1:
            content = types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name="read_evidence", args={"evidence_id": self.evidence_id}
                        )
                    )
                ],
            )
        else:
            self._tool_response = llm_request.contents[-1].model_dump_json()
            result = StageOutput(
                records=[], gaps=["本测试不做语义漏洞判断"], summary="ADK tool round trip verified"
            )
            content = types.Content(role="model", parts=[types.Part(text=result.model_dump_json())])
        yield LlmResponse(
            content=content,
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                total_token_count=50, prompt_token_count=40, candidates_token_count=10
            ),
        )


async def test_real_adk_runner_loads_full_skill_and_executes_jailed_tool(settings, store, sample_run):
    _, run, eid = sample_run
    bundle = SkillLoader(settings.skills_dir).resolve("finding_reviewer", "finding_reviewer", "0.1.0")
    tid = store.add_task(run["id"], "finding_review", {})
    _, instruction_hash = compile_instruction(bundle)
    store.start_task(tid, bundle, instruction_hash)
    model = ScriptedModel(evidence_id=eid)
    output = await AdkExecutor(settings, store, model_factory=lambda: model).execute(
        store.task(tid), bundle, {"allowed_kinds": list(ALLOWED_KINDS["finding_reviewer"]), "scope": {}}
    )
    assert output.summary == "ADK tool round trip verified"
    assert model._calls == 2
    assert all(f"[{rule}]" in model._instructions[0] for rule in bundle.rule_ids)
    assert "UNTRUSTED" in model._tool_response
    assert "query_all" in model._tool_response
    assert store.run(run["id"])["usage"] == {"requests": 2, "tokens": 100}


def test_schema_is_explicit_not_free_text():
    schema = StageOutput.model_json_schema()
    assert schema["additionalProperties"] is False
    assert "dynamic_confirmed" not in json.dumps(schema)


async def test_explicit_unbounded_adk_run_still_accounts_calls(settings, store, sample_run):
    from dataclasses import replace

    _, run, eid = sample_run
    bundle = SkillLoader(settings.skills_dir).resolve('finding_reviewer', 'finding_reviewer', '0.1.0')
    limited = store.add_task(run['id'], 'finding_review', {'attempt': 'bounded'})
    limited_model = ScriptedModel(evidence_id=eid)
    # ADK exposes invocation limits as error events; the adapter rejects them.
    with pytest.raises(OutputContractError):
        await AdkExecutor(replace(settings, agent_max_calls=1), store,
                          model_factory=lambda: limited_model).execute(
            store.task(limited), bundle, {})
    assert limited_model._calls == 1
    prior_requests = store.run(run['id'])['usage']['requests']
    unbounded = store.add_task(run['id'], 'finding_review', {'attempt': 'unbounded'})
    model = ScriptedModel(evidence_id=eid)
    output = await AdkExecutor(replace(settings, agent_max_calls=0), store,
                               model_factory=lambda: model).execute(store.task(unbounded), bundle, {})
    assert output.summary == 'ADK tool round trip verified'
    assert model._calls == 2
    assert store.run(run['id'])['usage']['requests'] == prior_requests + 2
    assert store.rows('SELECT evidence_id FROM task_reads WHERE task_id=?', (unbounded,)) == [{'evidence_id': eid}]


async def test_disabled_budget_ignores_configured_adk_call_limit(settings, store, sample_run):
    from dataclasses import replace
    _, run, eid = sample_run
    bundle = SkillLoader(settings.skills_dir).resolve('finding_reviewer', 'finding_reviewer', '0.1.0')
    tid = store.add_task(run['id'], 'finding_review', {})
    model = ScriptedModel(evidence_id=eid)
    output = await AdkExecutor(replace(settings, enforce_budgets=False, agent_max_calls=1), store,
                               model_factory=lambda: model).execute(store.task(tid), bundle, {})
    assert output.summary == 'ADK tool round trip verified'
    assert model._calls == 2
    assert store.run(run['id'])['usage']['requests'] == 2


async def test_oversized_input_is_not_misreported_as_an_invalid_model_response(settings,store,sample_run):
    _,run,eid=sample_run
    task=store.add_task(run['id'],'finding_review',{})
    bundle=SkillLoader(settings.skills_dir).resolve('finding_reviewer','finding_reviewer','0.1.0')
    model=ScriptedModel(evidence_id=eid)
    with pytest.raises(OutputContractError) as error:
        await AdkExecutor(settings,store,model_factory=lambda:model).execute(
            store.task(task),bundle,{'oversized_input':'x'*80001})
    assert error.value.code=='input_context'
    assert model._calls==0 and store.run(run['id'])['usage']['requests']==0
    assert not store.rows('SELECT * FROM model_outputs WHERE task_id=?',(task,))


async def test_litellm_adapter_routes_tool_and_structured_response(settings, store, sample_run, monkeypatch):
    from google.adk.models.lite_llm import LiteLLMClient
    from litellm import ModelResponse

    _, run, eid = sample_run
    bundle = SkillLoader(settings.skills_dir).resolve("finding_reviewer", "finding_reviewer", "0.1.0")
    tid = store.add_task(run["id"], "finding_review", {"fixture": "adapter"})
    store.start_task(tid, bundle, compile_instruction(bundle)[1])
    captured = []

    async def completion(self, model, messages, tools, **kwargs):
        captured.append({"model": model, "messages": messages, "tools": tools, **kwargs})
        if len(captured) == 1:
            response = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call1",
                        "type": "function",
                        "function": {"name": "read_evidence", "arguments": json.dumps({"evidence_id": eid})},
                    }
                ],
            }
            reason = "tool_calls"
        else:
            response = {
                "role": "assistant",
                "content": StageOutput(summary="adapter checked").model_dump_json(),
            }
            reason = "stop"
        return ModelResponse(
            model=settings.model_id,
            choices=[{"index": 0, "message": response, "finish_reason": reason}],
            usage={"total_tokens": 60, "prompt_tokens": 40, "completion_tokens": 20},
        )

    monkeypatch.setattr(LiteLLMClient, "acompletion", completion)
    output = await AdkExecutor(settings, store).execute(
        store.task(tid), bundle, {"allowed_kinds": ["review"], "scope": {}}
    )
    assert output.summary == "adapter checked"
    assert captured[0]["api_base"] == settings.gateway + "/v1"
    assert captured[0]['temperature'] == 0.0
    assert captured[0]["model"] == f"openai/{settings.model_id}"
    assert captured[0]["extra_headers"]["x-auditor-run"] == run["id"]
    assert captured[0]["extra_headers"]["x-auditor-task"] == tid
    assert captured[0]["extra_headers"]["x-auditor-request"] != captured[1]["extra_headers"]["x-auditor-request"]
    assert "UNTRUSTED" in json.dumps(captured[1]["messages"])
    assert store.rows("SELECT evidence_id FROM task_reads WHERE task_id=?", (tid,)) == [{"evidence_id": eid}]


async def test_requirement_role_cannot_read_code(settings, store, sample_run):
    _, run, eid = sample_run
    bundle = SkillLoader(settings.skills_dir).resolve(
        "requirement_generator", "requirement_generator", "0.1.0"
    )
    tid = store.add_task(run["id"], "requirements", {})
    store.start_task(tid, bundle, compile_instruction(bundle)[1])
    model = ScriptedModel(evidence_id=eid)
    try:
        await AdkExecutor(settings, store, model_factory=lambda: model).execute(
            store.task(tid), bundle, {"allowed_kinds": ["requirement"], "scope": {}}
        )
    except ValueError:
        pass
    assert not store.rows("SELECT * FROM task_reads WHERE task_id=?", (tid,))


@pytest.mark.parametrize('complete', [True, False])
async def test_explicit_stage_submission_ends_without_another_model_call(settings, store, sample_run, complete):
    from security_auditor.agents import stage_schema
    _, run, _ = sample_run
    tid = store.add_task(run['id'], 'requirements', {})
    bundle = SkillLoader(settings.skills_dir).resolve('requirement_generator', 'requirement_generator', '0.1.0')
    class SubmissionModel(BaseLlm):
        model: str = 'submission-test'
        _calls: int = PrivateAttr(default=0)
        async def generate_content_async(self, llm_request, stream=False):
            self._calls += 1
            assert self._calls == 1  # no repair, summary generation, or translation
            declarations = [f for tool in llm_request.config.tools or [] for f in tool.function_declarations or []]
            final = next(f for f in declarations if f.name == 'set_model_response')
            assert set(final.parameters_json_schema['required']) == {'summary', 'records', 'gaps'}
            assert set(final.parameters_json_schema['$defs']) == {'Requirement'}
            args = {'records': [], 'gaps': ['未发现支付范围说明']}
            if complete:
                args['summary'] = '安全需求分析完成'
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(
                function_call=types.FunctionCall(name='set_model_response', args=args))]))
    model = SubmissionModel()
    executor = AdkExecutor(settings, store, model_factory=lambda: model)
    if complete:
        output = await executor.execute(store.task(tid), bundle, {})
        assert output.summary == '安全需求分析完成'
    else:
        with pytest.raises(OutputContractError):
            await executor.execute(store.task(tid), bundle, {})
        assert not store.records(run['id'])
    assert model._calls == 1
    raw = json.loads(store.rows('SELECT content FROM model_outputs WHERE task_id=? ORDER BY created', (tid,))[0]['content'])
    assert ('summary' in raw) == complete
    assert set(stage_schema('design_analyst').model_json_schema()['$defs']) == {'Fact'}


@pytest.mark.parametrize('failure_type', ['transport', 'balance', 'budget'])
async def test_adk_error_event_preserves_retry_pause_and_budget_categories(settings, store, sample_run, monkeypatch, failure_type):
    from security_auditor.provider import ProviderUnavailable, ProviderBlocked, BudgetExceeded
    from security_auditor import agents
    _, run, _ = sample_run
    tid = store.add_task(run['id'], 'requirements', {})
    bundle = SkillLoader(settings.skills_dir).resolve('requirement_generator', 'requirement_generator', '0.1.0')
    failure = {'transport': ProviderUnavailable('timeout', code='transport'),
               'balance': ProviderBlocked('balance', code='balance'),
               'budget': BudgetExceeded('budget')}[failure_type]
    calls = []
    async def response(*args):
        calls.append(1)
        raise failure
        yield  # async iterator used by the production model adapter
    monkeypatch.setattr(agents, 'accounted_response', response)
    with pytest.raises(type(failure)) as error:
        await AdkExecutor(settings, store).execute(store.task(tid), bundle, {})
    assert error.value is failure
    assert calls == [1]


async def test_final_json_uses_the_same_declared_role_schema(settings, store, sample_run):
    _, run, _ = sample_run
    eid = store.add_evidence(run['id'], 'document', 'design:1', 'Authentication is required.', {})
    tid = store.add_task(run['id'], 'requirements', {})
    store.start_task(tid)
    bundle = SkillLoader(settings.skills_dir).resolve('requirement_generator', 'requirement_generator', '0.1.0')
    submitted = {'records': [{'id': 'req-auth', 'title': 'Authentication', 'module': 'API',
                             'statement': 'Require authentication.', 'acceptance_criteria': ['Reject unauthenticated requests.'],
                             'origin': 'EXPLICIT_DESIGN', 'evidence_ids': [eid], 'rationale': 'Original design'}],
                 'gaps': [], 'summary': 'Requirement analysis'}
    class Model(BaseLlm):
        model: str = 'role-schema-test'
        _calls: int = PrivateAttr(default=0)
        async def generate_content_async(self, llm_request, stream=False):
            self._calls += 1
            if self._calls == 1:
                part = types.Part(function_call=types.FunctionCall(name='read_evidence', args={'evidence_id': eid}))
            else:
                assert self._calls == 2
                declarations = [f for tool in llm_request.config.tools or [] for f in tool.function_declarations or []]
                final = next(f for f in declarations if f.name == 'set_model_response')
                assert final.parameters_json_schema['$defs']['Requirement']['properties']['evidence_ids']['items']['enum'] == ['S0001']
                part = types.Part(text=json.dumps(submitted))
            yield LlmResponse(content=types.Content(role='model', parts=[part]))
    model = Model()
    output = await AdkExecutor(settings, store, model_factory=lambda: model).execute(store.task(tid), bundle, {})
    store.commit_output(tid, 'requirement_generator', output)
    assert model._calls == 2
    record = store.records(run['id'])[0]
    assert record['kind'] == 'requirement'  # literal default in the advertised Requirement schema
    assert all(record[key] == value for key, value in submitted['records'][0].items())
    assert json.loads(store.rows('SELECT content FROM model_outputs WHERE task_id=?', (tid,))[0]['content']) == submitted


@pytest.mark.parametrize('mixed_fields', [False, True])
async def test_pci_submission_commits_unchanged_or_stops_without_repair(settings, store, sample_run, mixed_fields):
    _, run, _ = sample_run
    eid = store.add_evidence(run['id'], 'standard', 'PCI:1.1', 'Scope requirement', {'clause_id': '1.1'})
    doc = store.add_evidence(run['id'], 'document', 'KEP:1', 'Original authentication design', {})
    tid = store.add_task(run['id'], 'pci_mapping', {'clause_ids': ['1.1'], 'evidence_ids': [eid]})
    store.start_task(tid)
    record = {'id': 'scope-1', 'kind': 'applicability', 'title': '支付范围待确定',
              'evidence_ids': [eid], 'rationale': '未声明支付卡数据环境', 'clause_id': '1.1',
              'status': 'UNDETERMINED', 'missing_facts': ['是否涉及持卡人数据']}
    if mixed_fields:
        record.update(statement='', origin='PCI_DSS', acceptance_criteria=[])
    submitted = {'records': [record],
                 'gaps': ['支付范围待补充'], 'summary': '条款适用性分析完成'}
    class Model(BaseLlm):
        model: str = 'grouped-pci-test'
        _calls: int = PrivateAttr(default=0)
        async def generate_content_async(self, llm_request, stream=False):
            self._calls += 1
            assert self._calls == 1
            context = json.loads(llm_request.contents[0].parts[0].text)
            handle = context['target_references']['1.1']
            assert handle.startswith('S') and len(handle) == 5
            assert 'Scope requirement' in context['target_materials'][handle]
            assert 'Original authentication design' in next(iter(context['design_materials'].values()))
            declarations = [f for tool in llm_request.config.tools or [] for f in tool.function_declarations or []]
            final = next(f for f in declarations if f.name == 'set_model_response')
            definitions = final.parameters_json_schema['$defs']
            assert definitions['Applicability']['properties']['clause_id']['enum'] == ['1.1']
            assert set(definitions['Applicability']['properties']['evidence_ids']['items']['enum']) == {'S0001', 'S0002'}
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(
                function_call=types.FunctionCall(name='set_model_response', args=submitted))]))
    model = Model()
    bundle = SkillLoader(settings.skills_dir).resolve('pci_mapper', 'pci_mapper', '0.1.0')
    executor = AdkExecutor(settings, store, model_factory=lambda: model)
    if mixed_fields:
        with pytest.raises(OutputContractError):
            await executor.execute(store.task(tid), bundle, {'upstream_records': [{'evidence_ids': [doc]}]})
        assert not store.records(run['id'])
    else:
        output = await executor.execute(store.task(tid), bundle, {'upstream_records': [{'evidence_ids': [doc]}]})
        store.commit_output(tid, 'pci_mapper', output)
        assert all(store.records(run['id'])[0][key] == value for key, value in record.items())
    assert model._calls == 1
    assert json.loads(store.rows('SELECT content FROM model_outputs WHERE task_id=? ORDER BY created', (tid,))[0]['content']) == submitted
