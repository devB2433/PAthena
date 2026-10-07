import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from security_auditor.gateway import app
from security_auditor.model_contract import validate_native_response
from security_auditor.provider import BudgetExceeded, OutputContractError, ProviderUnavailable, ProviderBlocked


def tracked(store, sample_run, monkeypatch, limit=500):
    _, run, _ = sample_run
    store.provider.configure(run["id"], limit, None)
    tid = store.add_task(run["id"], "research", {})
    monkeypatch.setenv("AUDITOR_GATEWAY_DATABASE", str(store.path))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    monkeypatch.setenv("AUDITOR_PROVIDER_RETRY_SECONDS", "0")
    monkeypatch.delenv("AUDITOR_ANALYSIS_PROXY", raising=False)
    headers = {"x-auditor-run": run["id"], "x-auditor-task": tid, "x-auditor-request": "logical-one"}
    body = {"model": "deepseek-flash", "messages": [{"role": "user", "content": "source"}]}
    return run, tid, headers, body


def completed(tokens=70):
    return {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": {"total_tokens": tokens}}


def test_outbound_tool_protocol_is_archived_without_prompt_or_credentials(store, sample_run, monkeypatch):
    from security_auditor.agents import submission_model
    run, _, headers, body = tracked(store, sample_run, monkeypatch)
    body['tools'] = [{'type': 'function', 'function': {'name': 'set_model_response',
                     'parameters': submission_model('requirement_checker').model_json_schema()}}]
    captured = []
    def upstream(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=completed())
    with TestClient(app) as client:
        app.state.client = httpx.AsyncClient(base_url='https://api.deepseek.com', transport=httpx.MockTransport(upstream))
        assert client.post('/v1/chat/completions', json=body, headers=headers).status_code == 200
    saved = store.rows('SELECT * FROM provider_protocols')[0]
    assert saved['endpoint'] == '/beta/chat/completions'
    assert json.loads(saved['tools']) == captured[0]['tools']
    groups = captured[0]['tools'][0]['function']['parameters']['properties']['records']
    assert groups['required'] == ['assessment', 'finding']
    assert 'fixture-key' not in saved['tools'] and '"source"' not in saved['tools']
    assert store.run(run['id'])['usage']['requests'] == 1


@pytest.mark.parametrize("first", [429, 500, 503, "disconnect"])
def test_transient_failure_retries_and_each_attempt_is_accounted(store, sample_run, monkeypatch, first):
    run, _, headers, body = tracked(store, sample_run, monkeypatch)
    captured = []
    def upstream(request):
        captured.append(request)
        if len(captured) == 1:
            if first == "disconnect":
                raise httpx.ReadTimeout("fixture-key must not escape", request=request)
            return httpx.Response(first, json={"secret": "fixture-key"})
        return httpx.Response(200, json=completed())
    with TestClient(app) as client:
        app.state.client = httpx.AsyncClient(base_url="https://api.deepseek.com", transport=httpx.MockTransport(upstream))
        result = client.post("/v1/chat/completions", json=body, headers=headers)
        assert result.status_code == 200 and result.json() == completed()
    usage = store.run(run["id"])["usage"]
    assert len(captured) == usage["requests"] == 2
    assert usage["known_tokens"] == 70 and usage["reserved_tokens"] == 0
    assert usage["unknown_requests"] == (0 if first == 429 else 1)
    assert not store.provider.failure("logical-one")


@pytest.mark.parametrize("status,code", [(401, "authentication"), (402, "balance"), (422, "parameters")])
def test_permanent_failure_is_not_retried_and_diagnostic_is_redacted(store, sample_run, monkeypatch, status, code):
    run, _, headers, body = tracked(store, sample_run, monkeypatch)
    calls = []
    def upstream(request):
        calls.append(1)
        return httpx.Response(status, json={"secret": "fixture-key"})
    with TestClient(app) as client:
        app.state.client = httpx.AsyncClient(base_url="https://api.deepseek.com", transport=httpx.MockTransport(upstream))
        result = client.post("/v1/chat/completions", json=body, headers=headers)
        assert result.status_code == status and "fixture-key" not in result.text
    assert len(calls) == store.run(run["id"])["usage"]["requests"] == 1
    assert store.provider.failure("logical-one") == code


def test_500_actual_requests_is_a_hard_atomic_ceiling(store, sample_run):
    _, run, _ = sample_run
    ledger = store.provider
    ledger.configure(run["id"], 500, None)
    tid = store.add_task(run["id"], "research", {})
    for i in range(499):
        ledger.finish(ledger.begin(run["id"], tid, str(i), 1, {}), None, 200, tokens=10)
    def last(i):
        try:
            return ledger.begin(run["id"], tid, "last", i, {})
        except BudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(last, range(8)))
    assert sum(r is not None for r in results) == 1
    assert ledger.usage(run["id"])["requests"] == 500
    store.recover()
    assert ledger.usage(run["id"])["unknown_requests"] == 1
    with pytest.raises(BudgetExceeded):
        ledger.begin(run["id"], tid, "after-restart", 1, {})
    ledger.configure(run["id"], 501, None)
    ledger.begin(run["id"], tid, "extended", 1, {})
    assert ledger.usage(run["id"])["requests"] == 501


def test_retry_cannot_cross_remaining_budget(store, sample_run, monkeypatch):
    run, _, headers, body = tracked(store, sample_run, monkeypatch, limit=1)
    calls = []
    def upstream(request):
        calls.append(1)
        return httpx.Response(503)
    with TestClient(app) as client:
        app.state.client = httpx.AsyncClient(base_url="https://api.deepseek.com", transport=httpx.MockTransport(upstream))
        assert client.post("/v1/chat/completions", json=body, headers=headers).status_code == 409
    assert len(calls) == 1 and store.provider.failure("logical-one") == "budget"
    assert store.run(run["id"])["usage"]["requests"] == 1


def test_truncated_response_is_archived_without_model_repair(store, sample_run, monkeypatch):
    run, _, headers, body = tracked(store, sample_run, monkeypatch)
    response = completed(100)
    response["choices"][0]["finish_reason"] = "length"
    calls = []
    def upstream(request):
        calls.append(1)
        return httpx.Response(200, json=response)
    with TestClient(app) as client:
        app.state.client = httpx.AsyncClient(base_url="https://api.deepseek.com", transport=httpx.MockTransport(upstream))
        result = client.post("/v1/chat/completions", json=body, headers=headers)
        assert result.json()["error"]["code"] == "output"
    assert len(calls) == 1
    saved = store.rows("SELECT response FROM provider_requests")[0]["response"]
    assert json.loads(saved) == response
    assert store.run(run["id"])["usage"]["known_tokens"] == 100
    assert not store.records(run["id"])


def test_prose_cannot_be_converted_to_a_verdict_or_calibration():
    from security_auditor.mantis_worker import native_path
    native_path()
    from core.schemas import ReviewVerdict, FindingCalibration
    response = LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Probably false positive")]))
    for schema in (ReviewVerdict, FindingCalibration):
        with pytest.raises(OutputContractError):
            validate_native_response(response, schema)
    assert response.content.parts[0].text == "Probably false positive"


def test_empty_summary_or_unknown_fields_are_rejected_before_persistence():
    from security_auditor.mantis_worker import native_path
    from security_auditor.model_contract import typed_native_tool
    from pydantic import ConfigDict
    native_path()
    from core.schemas import CodebaseSummary
    class Strict(CodebaseSummary):
        model_config = ConfigDict(extra="forbid")
    writes = []
    def record_summary(summary):
        writes.append(summary)
        return "SUCCESS"
    wrapped = typed_native_tool(record_summary, Strict, "summary", lambda s: bool(s.overview and s.key_modules))
    for value in ({}, {"components": ["auth"]}):
        with pytest.raises(OutputContractError):
            wrapped(summary=value)
    assert not writes
    wrapped(summary={"overview": "Orders", "key_modules": ["auth"]})
    assert writes[0].overview == "Orders"


def test_explicit_citations_and_known_wrapper_are_parsed_without_a_model():
    from security_auditor.mantis_worker import native_path
    from security_auditor.model_contract import typed_native_tool
    native_path()
    from core.schemas import VulnerabilityReport
    saved = []
    def report_findings(report):
        saved.append(report.model_dump())
        return "SUCCESS"
    wrapped = typed_native_tool(report_findings, VulnerabilityReport, "report",
                                 lambda r: all(f.filepath and f.line_numbers for f in r.findings))
    value = {"report": {"findings": [{"id": 42, "title": "Candidate", "description": "Code issue", "impact": "Disclosure",
               "code_paths": ["auth.py:66", "auth.py:68", "main.py:141", "auth.py:L79-L81"]}]}}
    wrapped(report=value)
    finding = saved[0]["findings"][0]
    assert finding["id"] == "42"
    assert finding["filepath"] == "auth.py"
    assert finding["line_numbers"] == [66, 68, 79, 80, 81]
    assert finding["code_paths"] == value["report"]["findings"][0]["code_paths"]
    with pytest.raises(OutputContractError):
        wrapped(report={"findings": [{"title": "No location", "description": "Issue", "impact": "Disclosure"}]})
    assert len(saved) == 1


@pytest.mark.parametrize("text", ['Finished', '{"report":{"findings":[]}}}',
    '{"status":"NO_FINDINGS"}', '{"report":{"findings":[]},"status":"done"}',
    '{"type":"json_object","content":{"report":{"findings":[]}},"status":"done"}'])
def test_final_artifact_markers_and_invalid_json_do_not_become_results(text):
    from security_auditor.model_contract import submit_final_artifact
    calls = []
    response = LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))
    with pytest.raises(OutputContractError):
        submit_final_artifact(response, "report", lambda **kw: calls.append(kw))
    assert not calls


@pytest.mark.parametrize("text", ['{"report":{"findings":[]}}',
    '{"type":"json_object","content":{"report":{"findings":[]}}}'])
def test_explicit_empty_final_artifact_is_persisted_once_without_model_interpretation(text):
    from security_auditor.model_contract import submit_final_artifact
    calls = []
    response = LlmResponse(content=types.Content(role="model", parts=[
        types.Part(text=text)]))
    assert submit_final_artifact(response, "report", lambda **kw: calls.append(kw))
    assert calls == [{"report": {"findings": []}}]


def test_research_schema_references_resolve_at_function_parameter_root():
    from security_auditor.mantis_worker import native_path
    from security_auditor.model_contract import research_report_schema, RESEARCH_FIELDS
    native_path()
    from core.schemas import VulnerabilityReport
    schema = research_report_schema(VulnerabilityReport.model_json_schema())
    assert "$defs" not in schema["properties"]["report"]
    assert set(schema["$defs"]["FindingSchema"]["properties"]) <= RESEARCH_FIELDS
    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                target = schema
                for key in value["$ref"].removeprefix("#/").split("/"):
                    target = target[key]
                assert isinstance(target, dict)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(schema)


def test_artifact_annotations_never_replace_declared_values_or_create_missing_results():
    from security_auditor.model_contract import project_declared_fields, typed_native_tool
    from security_auditor.mantis_worker import native_path
    native_path()
    from core.schemas import ThreatModel
    writes = []
    def record_threat_model(threat_model):
        writes.append(threat_model.model_dump())
        return "SUCCESS"
    project = project_declared_fields(ThreatModel)
    wrapped = typed_native_tool(record_threat_model, ThreatModel, "threat_model",
        lambda v: bool(v.entry_points or v.trust_boundaries or v.threats), project_fields=project)
    raw = {"threats": ["Source-labelled threat"], "threats_note": "Extra narrative"}
    wrapped(threat_model=raw)
    assert raw['threats_note'] == 'Extra narrative'
    assert writes[0]['threats'] == raw['threats'] and 'threats_note' not in writes[0]
    with pytest.raises(OutputContractError):
        wrapped(threat_model={"threats_note": "No actual threat"})
    assert len(writes) == 1


@pytest.mark.asyncio
async def test_direct_mantis_calibration_tool_survives_real_litellm_conversion():
    from security_auditor.mantis_worker import native_path
    from security_auditor.model_contract import bind_direct_tools
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.lite_llm import _get_completion_inputs
    from google.adk.tools.set_model_response_tool import SetModelResponseTool
    native_path()
    from core.schemas import FindingCalibration
    req = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="Calibrate")])],
        config=types.GenerateContentConfig(tools=[SetModelResponseTool(FindingCalibration)]))
    bind_direct_tools(req)
    _, tools, _, _, _ = await _get_completion_inputs(req, "openai/deepseek-flash")
    assert tools[0]["function"]["name"] == "set_model_response"
    assert "mantis_risk_score" in tools[0]["function"]["parameters"]["properties"]
    assert req.tools_dict["set_model_response"].output_schema is FindingCalibration


@pytest.mark.parametrize("text", ['Score: 90', 'Risk is LOW', '{}',
    '```json\n{"finding_id":1}\n```'])
def test_calibration_never_infers_scores_from_text_or_defaults(text):
    from security_auditor.mantis_worker import native_path
    from security_auditor.model_contract import parse_calibration
    native_path()
    from core.schemas import FindingCalibration
    with pytest.raises(OutputContractError):
        parse_calibration(text, 1, FindingCalibration)


def test_calibration_cannot_overwrite_a_wrong_finding_id():
    from security_auditor.mantis_worker import native_path
    from security_auditor.model_contract import parse_calibration,validate_calibration_identity
    native_path()
    from core.schemas import FindingCalibration
    value = {"finding_id": 2, "mantis_risk_score": 6.4, "priority": "HIGH",
             "impact_score": 4, "likelihood_score": 3, "reasoning": "Static analysis"}
    with pytest.raises(OutputContractError):
        parse_calibration(json.dumps(value), 1, FindingCalibration)
    response = LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=
        types.FunctionCall(name="set_model_response", args=value))]))
    with pytest.raises(OutputContractError):
        validate_calibration_identity(response, FindingCalibration, 1)


def test_escaped_protocol_whitespace_preserves_all_string_values():
    from security_auditor.provider import parse_model_json
    value = {"text": 'Keep literal \\n, a "quote", \\\\ path and an actual\nnewline',
             "nested": [{"value": 6.4, "flag": False, "unknown": None}]}
    original = json.dumps(value, ensure_ascii=False, indent=2)
    malformed = original.replace('\n', '\\n')
    decoded, projection = parse_model_json(malformed)
    assert decoded == value and projection == original
    assert parse_model_json(original) == (value, original)


@pytest.mark.parametrize("text", ['{\\n"a":1 "b":2}', '{\\n"a":1', '{"a":1}}',
    '{"a":"unterminated}', 'Score: 90'])
def test_protocol_whitespace_conversion_cannot_fill_syntax_or_semantics(text):
    from security_auditor.provider import parse_model_json
    with pytest.raises(ValueError):
        parse_model_json(text)


def test_gateway_preserves_original_and_records_deterministic_projection(store,sample_run,monkeypatch):
    run, _, headers, body = tracked(store,sample_run,monkeypatch)
    value = {"report": {"findings": []}}
    original = json.dumps(value, indent=2).replace('\n', '\\n')
    raw = {"choices": [{"message": {"content": original}, "finish_reason": "stop"}],
           "usage": {"total_tokens": 70}}
    body['tools'] = [{"type": "function", "function": {"name": "report_findings"}}]
    with TestClient(app) as client:
        app.state.client = httpx.AsyncClient(base_url="https://api.deepseek.com",
            transport=httpx.MockTransport(lambda _: httpx.Response(200,json=raw)))
        response = client.post('/v1/chat/completions',json=body,headers=headers)
        assert json.loads(response.json()['choices'][0]['message']['content']) == value
    row = store.rows('SELECT id,response FROM provider_requests WHERE run_id=?',(run['id'],))[0]
    assert json.loads(row['response'])['choices'][0]['message']['content'] == original
    assert store.rows('SELECT rule FROM provider_output_transforms WHERE request_id=?',(row['id'],))[0]['rule'] == 'escaped_whitespace_outside_strings_v1'
    assert store.provider.usage(run['id'])['requests'] == 1


def test_late_supplier_usage_reconciles_unknown_without_releasing_it_early(store, sample_run):
    _, run, _ = sample_run
    store.provider.configure(run["id"], 500, None)
    tid = store.add_task(run["id"], "research", {})
    rid = store.provider.begin(run["id"], tid, "pending", 1, {})
    store.recover()
    assert store.provider.usage(run["id"])["unknown_requests"] == 1
    store.provider.finish(rid, None, 200, json.dumps(completed()), tokens=70)
    usage = store.provider.usage(run["id"])
    assert usage["known_tokens"] == 70 and usage["unknown_tokens"] == 0


def test_mid_response_disconnect_is_retried_without_splicing_results(store, sample_run, monkeypatch):
    run, _, headers, body = tracked(store, sample_run, monkeypatch)
    calls = []
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'\n\n'
            yield b'{"choices":['
            raise httpx.RemoteProtocolError("fixture-key")
    def upstream(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, stream=Broken())
        return httpx.Response(200, json=completed())
    with TestClient(app) as client:
        app.state.client = httpx.AsyncClient(base_url="https://api.deepseek.com", transport=httpx.MockTransport(upstream))
        response = client.post("/v1/chat/completions", json=body, headers=headers)
        assert response.json() == completed()
    assert len(calls) == 2
    assert store.provider.usage(run["id"])["unknown_requests"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [(ProviderUnavailable("暂时不可用"), "WAITING"),
    (ProviderBlocked("余额不足"), "PAUSED"), (OutputContractError("无效结构"), "FAILED")])
async def test_program_recovers_transient_failure_and_keeps_completed_stages(fixture_settings, store, error, expected):
    from security_auditor.api import create_app, RunRequest
    # Use the actual API snapshot builder, but never a live model or target execution.
    class Engine:
        fingerprint = "fixture"
        phases = []
        async def execute(self, run_id, phase, parent):
            self.phases.append(phase)
            if phase == "audit" and self.phases.count("audit") == 1:
                raise error
    engine = Engine()
    application = create_app(fixture_settings, executor=object(), mantis_executor=engine)
    project = application.state.store.create_project("failure fixture")
    # Call route synchronously without starting the polling worker yet.
    start = next(r.endpoint for r in application.routes if r.path.endswith('/{project_id}/runs') and 'POST' in r.methods)
    run = start(project["id"], RunRequest(mode="code_only", repository_id="fixture"), None)
    scheduler = application.state.scheduler
    await scheduler.execute_run(run["id"])
    assert application.state.store.run(run["id"])["status"] == expected
    assert application.state.store.run(run["id"])["issue"]["code"] == error.code
    if expected == "WAITING":
        with application.state.store.connect() as db:
            db.execute("UPDATE run_recovery SET wake_at=? WHERE run_id=?", (time.time() - 1, run["id"]))
        worker = asyncio.create_task(scheduler.serve())
        for _ in range(50):
            if application.state.store.run(run["id"])["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)
        scheduler.stop.set()
        await worker
        assert application.state.store.run(run["id"])["status"] == "COMPLETED"
        assert engine.phases == ["model", "audit", "audit"]


@pytest.mark.asyncio
@pytest.mark.parametrize("user_paused", [False, True])
async def test_service_shutdown_preserves_user_pause_and_resumes_other_runs(fixture_settings, user_paused):
    from security_auditor.api import create_app, RunRequest
    entered = asyncio.Event()
    class Engine:
        fingerprint = "fixture"
        phases = []
        async def execute(self, run_id, phase, parent):
            self.phases.append(phase)
            if phase == "audit" and self.phases.count("audit") == 1:
                entered.set()
                await asyncio.Event().wait()
    engine = Engine()
    app = create_app(fixture_settings, executor=object(), mantis_executor=engine)
    s, scheduler = app.state.store, app.state.scheduler
    project = s.create_project("restart fixture")
    start = next(r.endpoint for r in app.routes if r.path.endswith('/{project_id}/runs') and 'POST' in r.methods)
    run = start(project["id"], RunRequest(mode="code_only", repository_id="fixture"), None)
    job = asyncio.create_task(scheduler.execute_run(run["id"]))
    await asyncio.wait_for(entered.wait(), 5)
    if user_paused:
        with s.connect() as db:
            db.execute("UPDATE runs SET pause_requested=1 WHERE id=?", (run["id"],))
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert s.run(run["id"])["status"] == ("PAUSED" if user_paused else "PENDING")
    worker = asyncio.create_task(scheduler.serve())
    for _ in range(50):
        if s.run(run["id"])["status"] == "COMPLETED" or user_paused:
            break
        await asyncio.sleep(0.02)
    scheduler.stop.set()
    await worker
    assert engine.phases == (["model", "audit"] if user_paused else ["model", "audit", "audit"])
    assert s.run(run["id"])["status"] == ("PAUSED" if user_paused else "COMPLETED")


def test_successful_request_resets_consecutive_outage_counter(store, sample_run):
    _, run, _ = sample_run
    ledger = store.provider
    ledger.configure(run['id'], 500, None)
    tid = store.add_task(run['id'], 'research', {})
    with store.connect() as db:
        db.execute('INSERT INTO run_recovery VALUES(?,5,0)', (run['id'],))
    failed = ledger.begin(run['id'], tid, 'failed', 1, {})
    ledger.finish(failed, 'unavailable', 503)
    assert store.run(run['id'])['recovery']['attempts'] == 5
    success = ledger.begin(run['id'], tid, 'success', 1, {})
    ledger.finish(success, None, 200, tokens=70)
    assert store.run(run['id'])['recovery'] is None
    assert store.run(run['id'])['usage']['requests'] == 2


@pytest.mark.parametrize('code,klass', [('balance', ProviderBlocked), ('authentication', ProviderBlocked),
                                     ('parameters', OutputContractError), ('unavailable', ProviderUnavailable)])
def test_failure_category_survives_exception_protocol(code, klass):
    from security_auditor.provider import raise_failure
    with pytest.raises(klass) as caught:
        raise_failure(code)
    assert caught.value.code == code
