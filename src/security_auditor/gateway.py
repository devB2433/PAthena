from __future__ import annotations

import json
import logging
import os
import socket
import time
import asyncio
import random
from pathlib import Path
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from .provider import BudgetExceeded, ProviderLedger, MESSAGES, category, parse_model_json

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database = os.getenv("AUDITOR_GATEWAY_DATABASE")
    app.state.ledger = ProviderLedger(Path(database)) if database else None
    app.state.capacity = asyncio.Semaphore(int(os.getenv("AUDITOR_PROVIDER_CONCURRENCY", "2")))
    app.state.client = httpx.AsyncClient(
        base_url="https://api.deepseek.com",
        timeout=httpx.Timeout(connect=15, read=660, write=60, pool=15),
        limits=httpx.Limits(max_keepalive_connections=0),
        follow_redirects=False,
        trust_env=False,
    )
    proxy = os.getenv("AUDITOR_ANALYSIS_PROXY")
    app.state.proxy = (
        httpx.AsyncClient(base_url=proxy, timeout=180, trust_env=False, follow_redirects=False)
        if proxy
        else None
    )
    yield
    await app.state.client.aclose()
    if app.state.proxy:
        await app.state.proxy.aclose()


app = FastAPI(title="本地模型网关", lifespan=lifespan, docs_url=None, redoc_url=None)


def stage_protocol(body: dict) -> bool:
    """Bind own analysis roles to the provider's strict tool transport.

    Only Schema is adapted. Returned content is never repaired or interpreted.
    Mantis tools and numeric calibration retain their existing protocols.
    """
    tools = body.get('tools') or []
    final = next((t.get('function', {}) for t in tools
                  if t.get('function', {}).get('name') == 'set_model_response'), {})
    if not {'records', 'gaps', 'summary'} <= final.get('parameters', {}).get('properties', {}).keys():
        return False

    def strict_schema(root):
        definitions = root.get('$defs', {})
        def convert(node):
            if not isinstance(node, dict):
                return node
            if '$ref' in node:
                ref = node['$ref']
                if not ref.startswith('#/$defs/') or ref[8:] not in definitions:
                    raise HTTPException(400, '阶段 Schema 引用无法解析')
                return convert(definitions[ref[8:]])
            converted = {key: node[key] for key in
                         ('type', 'description', 'enum', 'pattern', 'format', 'minimum', 'maximum',
                          'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf') if key in node}
            if 'const' in node:
                converted['enum'] = [node['const']]
            for union in ('oneOf', 'anyOf'):
                if union in node:
                    converted['anyOf'] = [convert(entry) for entry in node[union]]
            if 'items' in node:
                converted['items'] = convert(node['items'])
            if node.get('type') == 'object':
                properties = node.get('properties', {})
                # These are framework-owned annotations, never model outputs.
                if {'id', 'title', 'rationale', 'evidence_ids'} <= properties.keys():
                    properties = {k: v for k, v in properties.items() if k not in
                                  {'engine', 'native_id', 'native_status', 'native_data', 'native_score'}}
                converted['properties'] = {k: convert(v) for k, v in properties.items()}
                if 'evidence_ids' in converted['properties']:
                    converted['properties']['evidence_ids']['description'] = (
                        'Nonempty list of exact source handles actually read in this task, from the item enum. '
                        'Use source handles such as S0001, never a clause number such as 8.3.2. '
                        'For applicability, include the handle of the full clause source. Never leave this list empty.')
                converted['required'] = list(properties)
                converted['additionalProperties'] = False
            return converted
        return convert(root)

    for tool in tools:
        function = tool['function']
        function['parameters'] = strict_schema(function.get('parameters', {'type': 'object', 'properties': {}}))
        function['strict'] = True
    # DeepSeek requires non-thinking mode for forced tool choice. Explicitly
    # bind it rather than relying on a changing supplier default.
    body['thinking'] = {'type': 'disabled'}
    body['tool_choice'] = 'required'
    # Tool arguments use the strict function schema. Content JSON mode is a
    # separate transport; do not impose both response envelopes on this call.
    body.pop('response_format',None)
    record_schema = final['parameters']['properties']['records']
    lists = record_schema.get('properties', {}) if record_schema.get('type') == 'object' else None
    def fields_for(node):
        properties = node.get('items', {}).get('properties', {})
        return {'required_fields': list(properties),
                'fixed_values': {key: value['enum'][0] for key,value in properties.items()
                                 if len(value.get('enum', []))==1}}
    inventory = ({key: fields_for(value) for key,value in lists.items()} if lists is not None
                 else {'records': fields_for(record_schema)})
    frame = {'records': {name: [] for name in lists} if lists is not None else [], 'gaps': [],
             'summary': '<your nonempty stage summary in the configured output language>'}
    body['messages'] = [*body.get('messages', []), {'role': 'system', 'content': (
        'The final submission transport overrides any generic StageOutput layout description. '
        'Use set_model_response with this exact envelope, populating its result lists from your analysis: '
        + json.dumps(frame, ensure_ascii=False) + '. '
        'Every record must include every field in this inventory, with the declared fixed values: '
        + json.dumps(inventory, ensure_ascii=False) + '. Do not omit origin, kind or clause_ids '
        'when they occur in the inventory. Do not add fields outside the declared inventory. '
        'Every shown list and key is required, including empty lists. Do not omit a list because '
        'there are no results of that type. Never put fields from another type in a record. '
        'Source reading tools may be used before submitting. The final tool ends this invocation; '
        'the program persists it directly and cannot repair missing fields or reinterpret prose. '
        'Produce valid JSON. Keep explanations concise and cite original evidence IDs rather than '
        'copying entire source clauses. Use typographic quotation marks inside prose fields instead '
        'of unescaped ASCII double quotes. Only cite IDs actually provided or read in this task. '
        'For every applicability result include the source handle returned with the full clause read '
        'in evidence_ids. A clause_id (for example 8.3.2), requirement_id or context_id is not a source '
        'handle and must never appear in evidence_ids. Use only values from the evidence_ids item enum. '
        'The source field of read_standard_clause contains the exact handle; the structured_clause.id '
        'is only the clause number. Context source handles can be additional citations.') }]
    return True


def calibration_protocol(body: dict) -> str:
    """Strict numeric verdict transport for Mantis' direct calibration calls.

    The native optional free-form checklist is carried in reasoning/sanity text
    rather than an unconstrained JSON map. All original typed rating fields and
    numeric bounds survive. No returned value is inferred or rewritten.
    """
    tools = body.get("tools") or []
    if len(tools) != 1:
        return "/chat/completions"
    function = tools[0].get("function", {})
    schema = function.get("parameters", {})
    properties = schema.get("properties", {})
    if function.get("name") != "set_model_response" or not {"finding_id", "mantis_risk_score"} <= properties.keys():
        return "/chat/completions"
    supported = {}
    for name, original in properties.items():
        choices = original.get("anyOf", [original])
        choices = [c for c in choices if c.get("type") != "null"]
        if len(choices) != 1 or choices[0].get("type") not in {"string", "integer", "number", "boolean"}:
            if name in schema.get("required", []):
                raise HTTPException(400, "评级必需参数不符合严格工具协议")
            continue
        supported[name] = {k: v for k, v in choices[0].items()
                           if k in {"type", "enum", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}}
        if original.get("description"):
            supported[name]["description"] = original["description"]
    function["parameters"] = {"type": "object", "properties": supported,
                               "required": list(supported), "additionalProperties": False}
    function["strict"] = True
    body["tool_choice"] = {"type": "function", "function": {"name": "set_model_response"}}
    body["max_tokens"] = max(body.get("max_tokens") or 4096, 16384)
    body["messages"] = [*body["messages"], {"role": "system", "content": (
        "Return this finding's complete numeric calibration through set_model_response. "
        "Use reasoning and sanity_triage_applied for all checklist evaluations and fired rules. "
        "Preserve the requested finding_id. Do not invent default scores or execution results."
    )}]
    return "/beta/chat/completions"


@app.get("/health")
def health():
    return {
        "status": "ready" if os.getenv("DEEPSEEK_API_KEY") else "missing_key",
        "remote_host": "api.deepseek.com",
    }


@app.post("/v1/chat/completions")
async def completion(request: Request):
    proxy = os.getenv("AUDITOR_ANALYSIS_PROXY")
    if proxy:
        # The published browser port cannot use the credential-bearing model route.
        from urllib.parse import urlparse

        allowed_ip = socket.gethostbyname(urlparse(proxy).hostname)
        if request.client.host != allowed_ip:
            raise HTTPException(403, "模型接口只接受分析服务调用")
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise HTTPException(503, "模型网关未配置 DeepSeek 密钥")
    raw = await request.body()
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(413, "模型请求超过限额")
    body = await request.json()
    model = os.getenv("AUDITOR_MODEL_ID", "deepseek-flash")
    if body.get("model") != model:
        raise HTTPException(400, "模型未登记")
    if body.get("stream"):
        raise HTTPException(400, "首版网关使用非流式模型响应")
    allowed = {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "response_format",
        "temperature",
        "max_tokens",
        "stream",
    }
    if any(field not in allowed for field in body):
        raise HTTPException(400, "请求包含未经验证的模型参数")
    tool_names = {tool.get("function", {}).get("name") for tool in body.get("tools", [])}
    if tool_names & {"record_summary", "record_threat_model", "record_plan", "report_findings"}:
        body["messages"] = [*body["messages"], {"role": "system", "content": (
            "Artifact submission is a strict data protocol. Submit a complete object through the "
            "declared tool schema, using only its declared fields. Do not add undocumented keys. "
            "record_summary.summary uses overview, key_modules, tech_stack, architecture_summary, "
            "trust_boundaries, attack_surface. record_threat_model.threat_model uses threat_actors, "
            "trust_boundaries, entry_points, key_risks, threats. Additional narrative belongs in "
            "workspace notes. Submit an explicit report even when findings is an empty list. "
            "Nonempty findings require title, description, impact, filepath and line_numbers. "
            "The exact report_findings arguments are {\"report\":{\"findings\":[...]}}; "
            "do not nest a second report object. Alternatively, the final JSON message may contain "
            "exactly that same parameter envelope; the program will persist it through the same tool gate. "
            "Prefer calling the declared submission tool. A final summary message is exactly "
            "{\"summary\":{...}}; a final threat model is exactly {\"threat_model\":{...}}; "
            "a final plan is exactly {\"plan\":{...}}. Return actual values, never a schema, "
            "response_format, type/json_object or properties wrapper. "
            "Code citations can also use code_paths file:line. "
            "The program will validate and persist your returned data directly, without semantic repair."
        )}]
    native_output = int(os.getenv("AUDITOR_MANTIS_OUTPUT_TOKENS", "0"))
    if native_output:
        if not 4096 <= native_output <= 32768:
            raise HTTPException(503, "Mantis 模型输出额度配置无效")
        native_tools = {"record_summary", "record_threat_model", "record_plan", "report_findings",
                        "write_file", "get_findings"}
        if tool_names & native_tools:
            # Mantis exports whole KB/model objects through function arguments.
            # Its fixed 4096-token deployment default truncates these arguments.
            body["max_tokens"] = max(body.get("max_tokens") or 4096, native_output)
    if {"get_findings", "set_model_response"} <= tool_names:
        bound = next(tool['function'].get('parameters',{}) for tool in body['tools']
                     if tool.get('function',{}).get('name')=='set_model_response')
        verdict_fields = list(bound.get('properties',{}))
        body["messages"] = [*body["messages"], {
            "role": "system",
            "content": (
                "This is a schema-bound Mantis static audit stage. Use the available read-only "
                "tools to inspect source as needed. When the review is complete, you MUST call "
                "set_model_response with the exact bound schema, rather than finish with prose. "
                "This invocation's declared top-level fields are " + json.dumps(verdict_fields) + ". "
                "Do not copy fields from another review stage. Do not include finding_verdicts "
                "unless it is explicitly in this field list. No undocumented fields are accepted. "
                "Where the schema supports finding_verdicts, provide a separate verdict for every "
                "finding inspected, preserving its recorded finding_id. The stage's full skill "
                "determines each verdict. Do not claim execution or dynamic validation."
            ),
        }]
    if tool_names and not body.get("response_format"):
        # Keep navigation and artifact submission on one JSON protocol. This
        # constrains generation; it never reparses or repairs a bad response.
        # The local argument/schema gates below remain mandatory.
        body["response_format"] = {"type": "json_object"}
        body["messages"] = [*body["messages"], {"role": "system", "content": (
            "Use valid JSON for every function's arguments. Return a JSON object for any final "
            "message; never append text or unmatched brackets after the complete JSON object."
        )}]
    if body.get("response_format", {}).get("type") == "json_schema":
        schema = body["response_format"].get("json_schema", {}).get("schema")
        if schema:
            # JSON object mode guarantees syntax only. Keep the ADK schema visible
            # to the model; the analysis service still validates it with Pydantic.
            body["messages"] = [
                *body["messages"],
                {
                    "role": "system",
                    "content": (
                        "For the final answer, return a JSON instance matching this schema. "
                        "Do not return the schema or response_format wrapper. "
                        "Use the available tools to read materials before your final answer.\n"
                        + json.dumps(schema, ensure_ascii=False)
                    ),
                },
            ]
        body["response_format"] = {"type": "json_object"}
    body["thinking"] = {"type": "disabled"}
    upstream_path = '/beta/chat/completions' if stage_protocol(body) else calibration_protocol(body)
    ledger = request.app.state.ledger
    run_id, task_id, logical_id = (request.headers.get(name) for name in
                                  ("x-auditor-run", "x-auditor-task", "x-auditor-request"))
    if ledger and not all((run_id, task_id, logical_id)):
        raise HTTPException(400, "模型请求缺少任务标识")
    attempts = int(os.getenv("AUDITOR_PROVIDER_ATTEMPTS", "3")) if ledger else 1
    attempt = 0
    current_id = None

    async def backoff(response=None):
        suggested = None
        if response is not None:
            try:
                suggested = float(response.headers.get("retry-after", ""))
            except ValueError:
                pass
        base = float(os.getenv("AUDITOR_PROVIDER_RETRY_SECONDS", "2"))
        delay = min(60, max(suggested or 0, base * 2 ** (attempt - 1) * random.uniform(0.8, 1.2)))
        await asyncio.sleep(delay)

    async def send_attempt():
        nonlocal attempt, current_id
        while True:
            attempt += 1
            try:
                current_id = ledger.begin(run_id, task_id, logical_id, attempt, body) if ledger else None
                if ledger:
                    ledger.note_protocol(current_id, upstream_path, body.get('tools', []))
            except BudgetExceeded as exc:
                ledger.fault(logical_id, "pause" if "用户" in str(exc) else "budget")
                raise HTTPException(409, str(exc)) from None
            except ValueError:
                raise HTTPException(400, "模型请求任务标识无效") from None
            started = time.monotonic()
            try:
                upstream_request = request.app.state.client.build_request(
                    "POST", upstream_path, json=body, headers={"Authorization": f"Bearer {key}"})
                response = await request.app.state.client.send(upstream_request, stream=True)
            except httpx.RequestError as exc:
                logger.warning("Provider connection failed: type=%s elapsed=%.1fs input_bytes=%d",
                               type(exc).__name__, time.monotonic() - started, len(raw))
                if ledger:
                    ledger.finish(current_id, "transport", None)
                if attempt < attempts:
                    await backoff()
                    continue
                raise HTTPException(503, MESSAGES["transport"]) from None
            if response.status_code == 200:
                return response
            code = category(response.status_code)
            if ledger:
                ledger.finish(current_id, code, response.status_code,
                              definitely_unbilled=response.status_code in (400, 401, 402, 403, 422, 429))
            await response.aclose()
            if code in {"rate_limit", "unavailable"} and attempt < attempts:
                await backoff(response)
                continue
            raise HTTPException(response.status_code, MESSAGES[code])

    # Capacity is held through response consumption; release even when a caller
    # disconnects. Only whitespace is relayed before a complete result is saved.
    await request.app.state.capacity.acquire()
    try:
        response = await send_attempt()
    except BaseException:
        request.app.state.capacity.release()
        raise

    async def result_bytes():
        nonlocal response
        try:
            while True:
                chunks = []
                try:
                    async for chunk in response.aiter_bytes():
                        if not chunks and not chunk.strip():
                            yield chunk
                        else:
                            chunks.append(chunk)
                    payload = b"".join(chunks)
                    text = payload.decode("utf-8")
                    tokens = None
                    transformed = []
                    try:
                        parsed = json.loads(text)
                        tokens = (parsed.get("usage") or {}).get("total_tokens")
                        choices = parsed.get("choices")
                        if not isinstance(choices, list) or not choices:
                            raise ValueError("missing choices")
                        if any(c.get("finish_reason") in {"length", "content_filter"} for c in choices):
                            raise ValueError("incomplete result")
                        for index, choice in enumerate(choices):
                            message = choice.get("message", {})
                            for call_index, call in enumerate(message.get("tool_calls") or []):
                                args = call["function"]["arguments"]
                                value, projection = parse_model_json(args)
                                if not isinstance(value, dict):
                                    raise ValueError("invalid arguments")
                                if projection != args:
                                    call["function"]["arguments"] = projection
                                    transformed.append(f"choices.{index}.tool_calls.{call_index}.arguments")
                            if body.get("response_format", {}).get("type") == "json_object" and message.get("content"):
                                content = message["content"]
                                # Navigation commentary is permitted alongside tools;
                                # only final structured content is decoded here.
                                if not message.get("tool_calls"):
                                    try:
                                        _, projection = parse_model_json(content)
                                    except ValueError:
                                        # Unbound history/navigation stages may end
                                        # with prose. Required artifact and verdict
                                        # schemas are enforced by the caller.
                                        projection = content
                                    if projection != content:
                                        message["content"] = projection
                                        transformed.append(f"choices.{index}.message.content")
                    except (ValueError, KeyError, TypeError, AttributeError):
                        if ledger:
                            ledger.finish(current_id, "output", 200, text, tokens=tokens)
                        yield json.dumps({"error": {"message": MESSAGES["output"], "code": "output", "type": "output_contract"}}).encode()
                        return
                    if ledger:
                        ledger.finish(current_id, None, 200, text, tokens=tokens)
                        if transformed:
                            ledger.note_transform(current_id, transformed)
                    if transformed:
                        payload = json.dumps(parsed, ensure_ascii=False).encode()
                    yield payload
                    return
                except (httpx.RequestError, UnicodeError):
                    if ledger:
                        ledger.finish(current_id, "transport", 200)
                    await response.aclose()
                    if attempt >= attempts:
                        yield json.dumps({"error": {"message": MESSAGES["transport"], "code": "transport", "type": "provider_unavailable"}}).encode()
                        return
                    await backoff()
                    try:
                        response = await send_attempt()
                    except HTTPException as exc:
                        yield json.dumps({"error": {"message": exc.detail, "code": "provider", "type": "provider_error"}}).encode()
                        return
        finally:
            if ledger and current_id:
                ledger.finish(current_id, "transport", None)  # no-op if settled
            await response.aclose()
            request.app.state.capacity.release()

    return StreamingResponse(result_bytes(), media_type="application/json")


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT"])
async def analysis_proxy(path: str, request: Request):
    """Relay UI traffic while the analysis container remains on an internal-only network."""
    if path.startswith("v1/") or not request.app.state.proxy:
        raise HTTPException(404)
    if request.method == 'PUT' and path != 'api/v1/settings':
        raise HTTPException(405)
    if path.startswith("/") or any(part == ".." for part in path.split("/")):
        raise HTTPException(400, "页面路径无效")
    if int(request.headers.get("content-length", "0")) > 26 * 1024 * 1024:
        raise HTTPException(413, "请求超过材料上传限额")
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"connection", "transfer-encoding", "content-length", "authorization"}
    }
    upstream_request = request.app.state.proxy.build_request(
        request.method, "/" + path, params=request.query_params, headers=headers, content=await request.body()
    )
    if upstream_request.url.host != request.app.state.proxy.base_url.host:
        raise HTTPException(400, "页面请求越界")
    upstream = await request.app.state.proxy.send(upstream_request, stream=True)
    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in {"connection", "transfer-encoding", "content-length", "content-encoding"}
    }
    return StreamingResponse(
        upstream.aiter_bytes(),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(upstream.aclose),
    )
