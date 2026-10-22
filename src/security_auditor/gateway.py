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
                        '必须非空：从元素枚举中选择本任务实际读取的准确来源句柄。使用 S0001 等来源句柄，'
                        '禁止使用 8.3.2 等条款号。适用性判断必须包含完整条款原文的来源句柄。')
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
             'summary': '<按配置语言填写非空阶段摘要>'}
    body['messages'] = [*body.get('messages', []), {'role': 'system', 'content': (
        '最终提交协议优先于通用 StageOutput 布局说明。使用 set_model_response 按以下准确结构提交，'
        '结果列表须来自本次分析：' + json.dumps(frame, ensure_ascii=False) + '。'
        '每条记录必须包含以下库存中的每个字段，并遵守固定值：' + json.dumps(inventory, ensure_ascii=False) + '。'
        '库存中存在 origin、kind 或 clause_ids 时禁止省略；禁止添加未声明字段。每个列表和键均必填，'
        '没有该类结果也必须显式提交空列表。禁止在记录中混入其他类型的字段。提交前可使用来源读取工具。'
        '最终工具会结束本次调用，程序将直接保存结果，不能修复缺失字段或重新解释自然语言。'
        '必须生成有效 JSON，解释简洁，引用原始来源编号，避免复制整段条款。在自然语言字段中使用中文引号，'
        '避免未经转义的 ASCII 双引号。仅可引用本任务已提供或实际读取的编号。每项 applicability 的 '
        'evidence_ids 必须包含读取完整条款时返回的来源句柄。clause_id（例如 8.3.2）、requirement_id '
        '和 context_id 均不是来源句柄，禁止放入 evidence_ids；仅使用该数组元素枚举内的值。'
        'read_standard_clause 的 source 字段含准确句柄，structured_clause.id 仅为条款编号；'
        '上下文来源句柄可以作为附加引用。') }]
    return True


def editor_protocol(body: dict) -> bool:
    functions = [t.get('function', {}) for t in body.get('tools', [])]
    if len(functions) != 1 or functions[0].get('name') != 'submit_chinese_edits':
        return False
    functions[0]['strict'] = True
    body['tool_choice'] = {'type': 'function', 'function': {'name': 'submit_chinese_edits'}}
    body.pop('response_format', None)
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
        "通过 set_model_response 提交本风险的完整数值评级。将检查表评估和触发规则写入 reasoning 与 "
        "sanity_triage_applied。必须保留指定 finding_id，禁止编造默认分数或执行结果。"
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
            '产物提交遵循严格数据协议。必须通过声明的工具 Schema 提交完整对象，仅使用已声明字段，禁止添加未登记键。'
            'record_summary.summary 使用 overview、key_modules、tech_stack、architecture_summary、trust_boundaries、attack_surface。'
            'record_threat_model.threat_model 使用 threat_actors、trust_boundaries、entry_points、key_risks、threats。'
            '额外叙述写入工作区笔记。findings 为空列表时也必须显式提交报告；非空条目必须包含 title、description、'
            'impact、filepath 和 line_numbers。report_findings 的准确参数是 {"report":{"findings":[...]}}，禁止再嵌套 report。'
            '也可在最终 JSON 消息中返回完全相同的参数结构，程序将经过同一工具校验后保存；优先调用已声明的提交工具。'
            '最终摘要结构是 {"summary":{...}}，最终威胁模型是 {"threat_model":{...}}，最终计划是 {"plan":{...}}。'
            '必须返回实际值，禁止返回 Schema、response_format、type/json_object 或 properties 包装。'
            '代码引用也可通过 code_paths 使用 file:line。程序直接校验和保存返回数据，不会做语义修复。'
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
                '本次为受 Schema 约束的静态审计阶段。按需使用只读工具检查源码。复核完成后必须调用 '
                'set_model_response，严格匹配绑定的 Schema，禁止用自然语言结束。当前声明的顶层字段为 '
                + json.dumps(verdict_fields) + '。禁止复制其他阶段字段；仅当此列表显式包含 finding_verdicts 时才允许提交该字段。'
                '禁止提交未登记字段。Schema 支持 finding_verdicts 时，必须逐项提交被检查风险的判断，保留已登记 finding_id。'
                '具体判断由本阶段完整技能决定。禁止声称进行了执行或动态验证。'
            ),
        }]
    if tool_names and not body.get("response_format"):
        # Keep navigation and artifact submission on one JSON protocol. This
        # constrains generation; it never reparses or repairs a bad response.
        # The local argument/schema gates below remain mandatory.
        body["response_format"] = {"type": "json_object"}
        body["messages"] = [*body["messages"], {"role": "system", "content": (
            '每个函数的参数必须为有效 JSON。最终消息必须为 JSON 对象，完整对象后禁止追加文字或未配对括号。'
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
                        '最终答案必须为符合以下 Schema 的 JSON 实例，禁止返回 Schema 本身或 response_format 包装。'
                        '请先使用工具读取材料，再提交最终答案。\n'
                        + json.dumps(schema, ensure_ascii=False)
                    ),
                },
            ]
        body["response_format"] = {"type": "json_object"}
    body["thinking"] = {"type": "disabled"}
    upstream_path = '/beta/chat/completions' if stage_protocol(body) or editor_protocol(body) else calibration_protocol(body)
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
