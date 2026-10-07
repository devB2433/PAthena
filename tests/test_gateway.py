import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from security_auditor.gateway import app
import pytest


@pytest.mark.parametrize('role', ['design_analyst', 'requirement_generator', 'pci_mapper', 'pci_requirement_generator', 'requirement_reviewer', 'requirement_checker'])
def test_own_role_strict_protocol_preserves_data_fields_and_references(role):
    from security_auditor.agents import submission_model
    from security_auditor.gateway import stage_protocol
    body = {'response_format':{'type':'json_object'}, 'tools': [{'type': 'function', 'function': {'name': 'set_model_response',
                        'parameters': submission_model(role).model_json_schema()}},
                      {'type': 'function', 'function': {'name': 'read_evidence', 'parameters': {
                          'type': 'object', 'properties': {'evidence_id': {'type': 'string'}},
                          'required': ['evidence_id']}}}]}
    assert stage_protocol(body)
    assert body['thinking'] == {'type': 'disabled'}
    assert body['tool_choice'] == 'required'
    assert 'response_format' not in body
    assert all(t['function']['strict'] for t in body['tools'])
    schema = body['tools'][0]['function']['parameters']
    assert set(schema['required']) == {'records', 'summary', 'gaps'}
    def verify(node):
        if isinstance(node, list):
            for v in node:
                verify(v)
        elif isinstance(node, dict):
            assert not {'$ref', '$defs', 'oneOf', 'discriminator', 'minItems', 'minLength', 'maxLength', 'default', 'title'} & node.keys()
            if node.get('type') == 'object':
                assert node['additionalProperties'] is False
                assert set(node['required']) == set(node['properties'])
                if 'evidence_ids' in node['properties']:
                    assert 'evidence_ids' in node['required']
                    assert 'kind' in node['required']
                    assert 'Nonempty' in node['properties']['evidence_ids']['description']
                    assert 'never a clause number' in node['properties']['evidence_ids']['description']
                    assert not {'engine', 'native_id', 'native_status', 'native_data'} & node['properties'].keys()
            for k,v in node.items():
                if k == 'properties':
                    for child in v.values():
                        verify(child)
                else:
                    verify(v)
    verify(schema)
    serialized = json.dumps(schema)
    if role == 'pci_mapper':
        assert 'clause_id' in serialized and 'UNDETERMINED' in serialized
        assert 'acceptance_criteria' not in serialized
        assert schema['properties']['records']['items']['properties']['kind']['enum'] == ['applicability']
    if role == 'pci_requirement_generator':
        assert 'acceptance_criteria' in serialized and 'missing_facts' not in serialized
        assert 'origin' in body['messages'][-1]['content']
        assert 'required_fields' in body['messages'][-1]['content']
    if role == 'requirement_checker':
        groups = schema['properties']['records']['properties']
        assert set(groups) == {'assessment', 'finding'}
        assert 'finding_type' not in groups['assessment']['items']['properties']
        assert 'implementation_status' not in groups['finding']['items']['properties']


def test_own_role_strict_protocol_uses_beta_endpoint_without_extra_model_call(monkeypatch):
    from security_auditor.agents import stage_schema
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fixture-key')
    monkeypatch.delenv('AUDITOR_ANALYSIS_PROXY', raising=False)
    captured = []
    def upstream(request):
        captured.append(request)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'tool_calls': [{'id': 'final', 'type': 'function', 'function': {
                'name': 'set_model_response', 'arguments': json.dumps({'records': [], 'summary': '中文结论', 'gaps': []})}}]}}]})
    with TestClient(app) as client:
        app.state.client = httpx.AsyncClient(base_url='https://api.deepseek.com', transport=httpx.MockTransport(upstream))
        response = client.post('/v1/chat/completions', json={'model': 'deepseek-flash', 'messages': [],
            'tools': [{'type': 'function', 'function': {'name': 'set_model_response',
                       'parameters': stage_schema('requirement_generator').model_json_schema()}}]})
    assert response.status_code == 200 and len(captured) == 1
    assert str(captured[0].url) == 'https://api.deepseek.com/beta/chat/completions'
    assert 'response_format' not in json.loads(captured[0].content)
    assert json.loads(response.json()['choices'][0]['message']['tool_calls'][0]['function']['arguments']) == {
        'records': [], 'summary': '中文结论', 'gaps': []}


def test_gateway_fixed_endpoint_and_json_format(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    monkeypatch.setenv("AUDITOR_MODEL_ID", "deepseek-flash")
    monkeypatch.delenv("AUDITOR_ANALYSIS_PROXY", raising=False)
    captured = []

    def upstream(request):
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"records":[]}'}}]})

    with TestClient(app) as client:
        app.state.client = httpx.AsyncClient(
            base_url="https://api.deepseek.com", transport=httpx.MockTransport(upstream)
        )
        body = {
            "model": "deepseek-flash",
            "messages": [{"role": "user", "content": "output json"}],
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "result",
                    "schema": {
                        "type": "object",
                        "properties": {"summary": {"type": "string"}},
                        "required": ["summary"],
                        "additionalProperties": False,
                    },
                },
            },
        }
        assert client.post("/v1/chat/completions", json=body).status_code == 200
        assert str(captured[0].url) == "https://api.deepseek.com/chat/completions"
        forwarded = json.loads(captured[0].content)
        assert forwarded["response_format"] == {"type": "json_object"}
        assert forwarded["thinking"] == {"type": "disabled"}
        assert forwarded["messages"][0] == body["messages"][0]
        schema_message = forwarded["messages"][-1]
        assert schema_message["role"] == "system"
        assert json.loads(schema_message["content"].split("\n", 1)[1]) == body["response_format"]["json_schema"]["schema"]
        assert (
            client.post("/v1/chat/completions", json={**body, "api_base": "https://evil.example"}).status_code
            == 400
        )
        assert client.post("/v1/chat/completions", json={**body, "model": "unregistered"}).status_code == 400
        assert len(captured) == 1


def test_browser_proxy_and_model_route_isolation(monkeypatch):
    monkeypatch.setenv("AUDITOR_ANALYSIS_PROXY", "http://analysis-service:8080")
    monkeypatch.setattr("security_auditor.gateway.socket.gethostbyname", lambda name: "172.20.0.2")

    def internal(request):
        assert request.url.host == "analysis-service"
        return httpx.Response(200, content=b"fixture workspace", headers={"content-type": "text/html"})

    with TestClient(app) as client:
        app.state.proxy = httpx.AsyncClient(
            base_url="http://analysis-service:8080", transport=httpx.MockTransport(internal)
        )
        assert client.get("/").text == "fixture workspace"
        assert client.post("/v1/chat/completions", json={}).status_code == 403


def test_browser_setting_update_is_relayed_without_opening_model_routes(monkeypatch):
    monkeypatch.setenv('AUDITOR_ANALYSIS_PROXY', 'http://analysis-service:8080')
    captured = []
    def internal(request):
        captured.append(request)
        assert request.url.host == 'analysis-service'
        assert request.method == 'PUT' and request.url.path == '/api/v1/settings'
        assert json.loads(request.content) == {'language': 'zh-CN'}
        return httpx.Response(200, json={'language': 'zh-CN'})
    with TestClient(app) as client:
        app.state.proxy = httpx.AsyncClient(base_url='http://analysis-service:8080', transport=httpx.MockTransport(internal))
        response = client.put('/api/v1/settings', json={'language': 'zh-CN'})
        assert response.status_code == 200 and response.json()['language'] == 'zh-CN'
        assert client.put('/api/v1/projects', json={}).status_code == 405
        assert client.put('/v1/chat/completions', json={}).status_code == 404
    assert len(captured) == 1


def test_missing_key_is_explicit(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("AUDITOR_ANALYSIS_PROXY", raising=False)
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "missing_key"
        assert client.post("/v1/chat/completions", json={}).status_code == 503


def test_native_tool_requests_use_json_mode_without_repair(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    monkeypatch.delenv("AUDITOR_ANALYSIS_PROXY", raising=False)
    captured = []
    def upstream(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [
            {"function": {"name": "report_findings", "arguments": '{"report":{"findings":[]}}}'}}
        ]}, "finish_reason": "tool_calls"}]})
    with TestClient(app) as client:
        app.state.client = httpx.AsyncClient(base_url="https://api.deepseek.com", transport=httpx.MockTransport(upstream))
        response = client.post("/v1/chat/completions", json={"model": "deepseek-flash",
            "messages": [{"role": "user", "content": "Audit source"}],
            "tools": [{"type": "function", "function": {"name": "report_findings"}}]})
        assert captured[0]["response_format"] == {"type": "json_object"}
        assert response.json()["error"]["code"] == "output"
    assert len(captured) == 1  # an extra brace stays an error, never an LLM repair


@pytest.mark.parametrize('per_finding',[False,True])
def test_native_verdict_prompt_uses_only_current_stage_fields(monkeypatch,per_finding):
    monkeypatch.setenv('DEEPSEEK_API_KEY','fixture-key')
    monkeypatch.delenv('AUDITOR_ANALYSIS_PROXY',raising=False)
    fields={'route':{'type':'string'},'reason':{'type':'string'}}
    if per_finding:
        fields['finding_verdicts']={'type':'array','items':{'type':'object'}}
    parameters={'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}
    captured=[]
    def upstream(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{
            'content':'{"route":"viable","reason":"Original stage result"}'}}]})
    with TestClient(app) as client:
        app.state.client=httpx.AsyncClient(base_url='https://api.deepseek.com',transport=httpx.MockTransport(upstream))
        result=client.post('/v1/chat/completions',json={'model':'deepseek-flash','messages':[],
            'tools':[{'type':'function','function':{'name':'get_findings'}},
                     {'type':'function','function':{'name':'set_model_response','parameters':parameters}}]})
    assert result.status_code==200
    assert captured[0]['tools'][1]['function']['parameters']==parameters
    prompt=next(m['content'] for m in captured[0]['messages'] if 'declared top-level fields' in m['content'])
    assert 'declared top-level fields are '+json.dumps(list(fields))+'.' in prompt
    assert result.json()['choices'][0]['message']['content']=='{"route":"viable","reason":"Original stage result"}'


def test_calibration_uses_official_strict_tool_endpoint_with_original_typed_fields(monkeypatch):
    from security_auditor.mantis_worker import native_path
    native_path()
    from core.schemas import FindingCalibration
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    monkeypatch.delenv("AUDITOR_ANALYSIS_PROXY", raising=False)
    captured = []
    def upstream(request):
        captured.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{"function": {
            "name": "set_model_response", "arguments": json.dumps({"finding_id":1,"mantis_risk_score":6.4})
        }}]},"finish_reason":"tool_calls"}]})
    with TestClient(app) as client:
        app.state.client = httpx.AsyncClient(base_url="https://api.deepseek.com",transport=httpx.MockTransport(upstream))
        response = client.post('/v1/chat/completions',json={"model":"deepseek-flash",
            "messages":[{"role":"user","content":"Calibrate finding 1"}],
            "tools":[{"type":"function","function":{"name":"set_model_response",
                "parameters":FindingCalibration.model_json_schema()}}]})
        assert response.status_code == 200
    url, body = captured[0]
    assert url == 'https://api.deepseek.com/beta/chat/completions'
    function = body['tools'][0]['function']
    assert function['strict'] is True
    schema = function['parameters']
    assert schema['additionalProperties'] is False
    assert schema['required'] == list(schema['properties'])
    assert schema['properties']['mantis_risk_score']['minimum'] == 0.1
    assert schema['properties']['mantis_risk_score']['maximum'] == 10.0
    assert set(FindingCalibration.model_json_schema()['required']) <= schema['properties'].keys()
    assert body['tool_choice']['function']['name'] == 'set_model_response'
    assert body['max_tokens'] == 16384


def test_provider_disconnect_is_explicit_and_does_not_repeat_request(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    monkeypatch.delenv("AUDITOR_ANALYSIS_PROXY", raising=False)
    calls = []

    def disconnected(request):
        calls.append(request)
        raise httpx.RemoteProtocolError("diagnostic fixture-key", request=request)

    with TestClient(app) as client:
        app.state.client = httpx.AsyncClient(
            base_url="https://api.deepseek.com", transport=httpx.MockTransport(disconnected)
        )
        response = client.post("/v1/chat/completions", json={
            "model": "deepseek-flash", "messages": [{"role": "user", "content": "test"}],
        })
        assert response.status_code == 503
        assert "连接中断" in response.json()["detail"]
        assert "fixture-key" not in response.text
        assert len(calls) == 1


def test_non_streaming_keepalive_whitespace_is_accepted(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    monkeypatch.delenv("AUDITOR_ANALYSIS_PROXY", raising=False)

    def keepalive(request):
        assert request.extensions["timeout"]["read"] == 660
        return httpx.Response(200, content=b'\n\n \r\n{"choices":[{"message":{"content":"ok"}}]}')

    with TestClient(app) as client:
        assert app.state.client.timeout.read == 660
        app.state.client = httpx.AsyncClient(
            base_url="https://api.deepseek.com", timeout=app.state.client.timeout,
            transport=httpx.MockTransport(keepalive),
        )
        response = client.post("/v1/chat/completions", json={
            "model": "deepseek-flash", "messages": [{"role": "user", "content": "test"}],
        })
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "ok"


async def test_keepalive_reaches_analysis_before_completion(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    monkeypatch.delenv("AUDITOR_ANALYSIS_PROXY", raising=False)
    delivered = asyncio.Event()
    closed = []

    class QueuedResponse(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"\n\n"
            await delivered.wait()
            yield b'{"choices":[{"message":{"content":"ok"}}]}'

        async def aclose(self):
            closed.append(True)

    async def observed_app(scope, receive, send):
        async def observed_send(message):
            if message["type"] == "http.response.body" and message.get("body") == b"\n\n":
                delivered.set()
            await send(message)
        await app(scope, receive, observed_send)

    async with app.router.lifespan_context(app):
        await app.state.client.aclose()
        app.state.client = httpx.AsyncClient(
            base_url="https://api.deepseek.com",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=QueuedResponse())),
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observed_app), base_url="http://testserver") as client:
            response = await asyncio.wait_for(client.post("/v1/chat/completions", json={
                "model": "deepseek-flash", "messages": [{"role": "user", "content": "test"}],
            }), timeout=2)
            assert response.json()["choices"][0]["message"]["content"] == "ok"
    assert delivered.is_set() and closed


def test_native_artifact_exports_have_room_for_complete_arguments(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    monkeypatch.setenv("AUDITOR_MANTIS_OUTPUT_TOKENS", "16384")
    monkeypatch.delenv("AUDITOR_ANALYSIS_PROXY", raising=False)
    requests = []

    def upstream(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    with TestClient(app) as client:
        app.state.client = httpx.AsyncClient(
            base_url="https://api.deepseek.com", transport=httpx.MockTransport(upstream)
        )
        body = {"model": "deepseek-flash", "messages": [], "max_tokens": 4096}
        assert client.post("/v1/chat/completions", json=body).status_code == 200
        assert client.post("/v1/chat/completions", json={**body, "tools": [
            {"type": "function", "function": {"name": "record_threat_model"}},
        ]}).status_code == 200
        assert client.post("/v1/chat/completions", json={**body, "tools": [
            {"type": "function", "function": {"name": "write_file"}},
        ]}).status_code == 200
        assert [r["max_tokens"] for r in requests] == [4096, 16384, 16384]


def test_native_schema_bound_review_has_trusted_completion_instruction(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    monkeypatch.setenv("AUDITOR_MANTIS_OUTPUT_TOKENS", "16384")
    monkeypatch.delenv("AUDITOR_ANALYSIS_PROXY", raising=False)
    requests = []

    def upstream(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    with TestClient(app) as client:
        app.state.client = httpx.AsyncClient(
            base_url="https://api.deepseek.com", transport=httpx.MockTransport(upstream)
        )
        body = {"model": "deepseek-flash", "messages": [{"role": "user", "content": "inspect"}],
                "max_tokens": 4096, "tools": [
                    {"type": "function", "function": {"name": "get_findings"}},
                    {"type": "function", "function": {"name": "set_model_response"}},
                ]}
        assert client.post("/v1/chat/completions", json=body).status_code == 200
        forwarded = requests[0]
        assert forwarded["messages"][:len(body["messages"])] == body["messages"]
        assert forwarded["tools"] == body["tools"]
        assert forwarded["max_tokens"] == 16384
        instruction = next(m for m in forwarded["messages"][len(body["messages"]):]
                           if "MUST call set_model_response" in m["content"])
        assert instruction["role"] == "system"
        assert "MUST call set_model_response" in instruction["content"]
        assert "finding_verdicts" in instruction["content"]
