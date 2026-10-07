"""Read structured model output once. No semantic repair or inferred verdicts."""
from __future__ import annotations

import uuid
import json
import re

from .provider import OutputContractError, raise_failure, parse_model_json


def attach_request(llm_request, run_id: str, task_id: str) -> str:
    from google.genai import types
    logical_id = str(uuid.uuid4())
    llm_request.config.http_options = types.HttpOptions(headers={
        "x-auditor-run": run_id, "x-auditor-task": task_id, "x-auditor-request": logical_id,
    })
    return logical_id


async def accounted_response(model, llm_request, store, run_id: str, task_id: str):
    logical_id = attach_request(llm_request, run_id, task_id)
    try:
        async for response in model.generate_content_async(llm_request, stream=False):
            failure = store.provider.failure(logical_id)
            if failure:
                raise_failure(failure)
            yield response
    except Exception:
        failure = store.provider.failure(logical_id)
        if failure:
            raise_failure(failure)
        raise
    failure = store.provider.failure(logical_id)
    if failure:
        raise_failure(failure)


def validate_native_response(response, schema):
    if response.error_code or str(response.finish_reason) in {"MAX_TOKENS", "FinishReason.MAX_TOKENS", "SAFETY", "FinishReason.SAFETY"}:
        raise OutputContractError("模型结果被截断或拒绝，阶段未完成")
    if not schema:
        return response
    parts = response.content.parts if response.content else []
    calls = [p.function_call for p in parts if p.function_call]
    if calls:
        for call in calls:
            if call.name == "set_model_response":
                try:
                    result = schema.model_validate(dict(call.args or {}))
                    check_verdict(result)
                except ValueError:
                    raise OutputContractError("模型工具结果未通过结构化校验") from None
        return response  # ordinary source navigation is not a final verdict
    text = "".join(p.text or "" for p in parts)
    try:
        result = schema.model_validate(parse_model_json(text)[0])
    except ValueError:
        raise OutputContractError("模型最终结果必须直接符合约定 JSON 结构，阶段未完成") from None
    reason = getattr(result, "reason", getattr(result, "reasoning", ""))
    if reason.startswith("Fallback:") or reason.startswith("Fallback calibration"):
        raise OutputContractError("不能把回退内容当作模型结论")
    check_verdict(result)
    return response


def check_verdict(result):
    if hasattr(result, "finding_verdicts"):
        entries = result.finding_verdicts
        ids = [str(e.finding_id if hasattr(e, "finding_id") else e["finding_id"]) for e in entries]
        if not entries or len(ids) != len(set(ids)):
            raise OutputContractError("每个候选问题必须有唯一的结构化复核结论")
    reason = getattr(result, "reason", getattr(result, "reasoning", ""))
    if reason.startswith("Fallback"):
        raise OutputContractError("不能把回退内容当作模型结论")


def validate_review_inventory(response, schema, expected_ids):
    if not schema or "finding_verdicts" not in getattr(schema, "model_fields", {}):
        return
    parts = response.content.parts if response.content else []
    calls = [p.function_call for p in parts if p.function_call]
    if calls:
        finals = [c for c in calls if c.name == "set_model_response"]
        if not finals:
            return
        value = dict(finals[-1].args or {})
    else:
        value = json.loads("".join(p.text or "" for p in parts))
    actual = [str(v["finding_id"]) for v in value.get("finding_verdicts", [])]
    if sorted(actual) != sorted(str(i) for i in expected_ids):
        raise OutputContractError("候选问题与复核结论没有完整一一对应，阶段未完成")


def submit_final_artifact(response, parameter: str, submit):
    """Persist an exact final JSON artifact through the same native tool gate.

    A final report is already the model's result, not a request for another
    interpretation. Completion markers, prose and mixed objects are rejected.
    """
    parts = response.content.parts if response.content else []
    if any(part.function_call for part in parts):
        return False
    try:
        value = parse_model_json("".join(part.text or "" for part in parts))[0]
        if isinstance(value, dict) and set(value) == {"type", "content"} and value["type"] == "json_object":
            # An explicit transport envelope carries the unchanged artifact.
            # Never extract a result from prose or an ambiguous mixed object.
            value = value["content"]
        if not isinstance(value, dict) or set(value) != {parameter}:
            raise ValueError("missing exact artifact envelope")
        result = submit(**{parameter: value[parameter]})
        if isinstance(result, str) and result.upper().startswith(("ERROR", "FAIL")):
            # A final message has ended analysis; it cannot masquerade as a
            # successful artifact when the native citation gate rejected it.
            raise OutputContractError("最终产物的源码引用未通过校验，阶段未完成")
    except ValueError as exc:
        if isinstance(exc, OutputContractError):
            raise
        raise OutputContractError("模型最终返回缺少约定结构化产物，阶段未完成") from None
    return True


def bind_direct_tools(llm_request):
    """Mantis direct LlmRequest paths supply BaseTool, unlike ADK Agent paths.

    LiteLlm serializes google.types.Tool declarations only. Resolve the
    original declaration and retain its output schema for local validation.
    """
    from google.genai import types
    converted = []
    for tool in llm_request.config.tools or []:
        if isinstance(tool, types.Tool):
            converted.append(tool)
        elif hasattr(tool, "_get_declaration"):
            declaration = tool._get_declaration()
            if declaration is None:
                raise OutputContractError("模型工具缺少明确的参数定义")
            llm_request.tools_dict[declaration.name] = tool
            converted.append(types.Tool(function_declarations=[declaration]))
        else:
            raise OutputContractError("模型工具未转换为明确的参数定义")
    llm_request.config.tools = converted


def validate_calibration_identity(response, schema, expected_id: int):
    parts = response.content.parts if response.content else []
    calls = [p.function_call for p in parts if p.function_call]
    if calls:
        finals = [c for c in calls if c.name == "set_model_response"]
        if not finals:
            raise OutputContractError("风险评级必须提交结构化结果")
        result = schema.model_validate(dict(finals[-1].args or {}))
    else:
        result = schema.model_validate(parse_model_json("".join(p.text or "" for p in parts))[0])
    if result.finding_id != expected_id:
        raise OutputContractError("风险评级与候选问题编号不一致，阶段未完成")


def parse_calibration(text: str, expected_id: int, schema):
    try:
        result = schema.model_validate(parse_model_json(text)[0])
    except ValueError:
        raise OutputContractError("风险评级必须直接符合约定 JSON 结构，不能从文字或默认值生成分数") from None
    check_verdict(result)
    if result.finding_id != expected_id:
        raise OutputContractError("风险评级与候选问题编号不一致，阶段未完成")
    return result


def typed_native_tool(original, schema, parameter: str, meaningful=None, on_submit=None, on_error=None,
                      preserve_citation_research=False, project_fields=None):
    import inspect

    def wrapper(**kwargs):
        value = kwargs[parameter]
        if hasattr(value, "model_dump"):
            value = value.model_dump()
        # Only a redundant wrapper with the exact declared parameter name is
        # recognized. Unknown fields and arbitrary natural language stay errors.
        for _ in range(2):
            if isinstance(value, dict) and set(value) == {parameter}:
                value = value[parameter]
            else:
                break
        if project_fields:
            value = project_fields(value)
        if parameter == "report" and isinstance(value, dict):
            findings = value.get("findings", value.get("vulnerabilities"))
            if isinstance(findings, list):
                for finding in findings:
                    if isinstance(finding, dict):
                        # Mantis get_findings returns SQLite integer IDs, while
                        # its report schema declares string IDs. Preserve the
                        # same identity when those rows are submitted again.
                        if type(finding.get("id")) is int and finding["id"] > 0:
                            finding["id"] = str(finding["id"])
                        normalize_locations(finding)
        try:
            parsed = schema.model_validate(value) if isinstance(value, dict) else value
            if meaningful and not meaningful(parsed):
                raise ValueError("empty result")
        except ValueError:
            error = OutputContractError("模型提交的阶段产物为空或不符合结构，阶段未完成")
            if on_error:
                on_error(error)
            raise error from None
        # These native tools accept their Pydantic artifact object as well as
        # a dict. Preserve declared extension fields in the persisted JSON.
        result = original(**{parameter: parsed})
        if isinstance(result, str) and result.upper().startswith(("ERROR", "FAIL")):
            if preserve_citation_research and result.startswith(
                    "Error: citation verification failed and findings were NOT saved:"):
                # Preserve Mantis' original source-investigation tool loop.
                # Nothing was saved; the researcher must read real source and
                # submit a valid report before a completion receipt exists.
                return result
            error = OutputContractError("原生阶段产物未通过校验，阶段未完成")
            if on_error:
                on_error(error)
            raise error
        if on_submit:
            on_submit(original.__name__, parsed.model_dump())
        return result

    wrapper.__name__, wrapper.__doc__ = original.__name__, original.__doc__
    wrapper.__annotations__ = {parameter: schema, "return": str}
    wrapper.__signature__ = inspect.Signature([
        inspect.Parameter(parameter, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=schema)
    ], return_annotation=str)
    return wrapper


RESEARCH_FIELDS = frozenset({"id", "title", "description", "impact", "severity", "privileges_required",
    "attacker_position", "user_interaction", "status", "code_paths", "filepath", "line_numbers",
    "mitigation", "remediation", "history", "cwe", "signature", "lineage_id", "discovery_commit",
    "reasoning", "repro_hints", "duplicate_of", "possible_duplicate_of", "constituent_findings"})


def project_declared_fields(schema):
    """Ignore undeclared annotations, retaining every declared value unchanged.

    Raw responses are already archived. Missing or invalid declared values
    still fail the same validation; annotations never supply those values.
    """
    fields = frozenset(schema.model_fields)
    return lambda value: {k: v for k, v in value.items() if k in fields} if isinstance(value, dict) else value


def project_research_findings(value):
    """Only researcher-owned fields become candidates; later stages own verdicts.

    Original response remains archived. Never allow producer annotations to
    set viability, reproduction, patch state or calibrated scores.
    """
    if not isinstance(value, dict):
        return value
    field = "findings" if "findings" in value else "vulnerabilities"
    findings = value.get(field)
    if not isinstance(findings, list):
        return value
    return {**value, field: [{k: v for k, v in f.items() if k in RESEARCH_FIELDS}
                            if isinstance(f, dict) else f for f in findings]}


def research_report_schema(schema: dict):
    """Narrow the producer's advertised fields, retaining native definitions."""
    import copy
    result = copy.deepcopy(schema)
    finding = result["$defs"]["FindingSchema"]
    finding["properties"] = {k: v for k, v in finding["properties"].items() if k in RESEARCH_FIELDS}
    finding["additionalProperties"] = False
    # Prune unreachable definitions so later-stage matrices are not advertised.
    wanted = set()
    def references(obj):
        if isinstance(obj, dict):
            if "$ref" in obj:
                name = obj["$ref"].rsplit("/", 1)[-1]
                if name not in wanted:
                    wanted.add(name)
                    references(result["$defs"][name])
            for v in obj.values():
                references(v)
        elif isinstance(obj, list):
            for v in obj:
                references(v)
    references({k: v for k, v in result.items() if k != "$defs"})
    definitions = {k: v for k, v in result.pop("$defs").items() if k in wanted}
    # References are rooted at the function parameter document, not report.
    return {"type": "object", "$defs": definitions,
            "properties": {"report": result}, "required": ["report"]}


def normalize_locations(finding: dict):
    """Mechanical conversion of explicit model citations, never code inference.

    First line-qualified citation is the primary location. Line numbers from
    other files must not be assigned to it; all original code_paths survive.
    """
    locations = []
    for citation in finding.get("code_paths") or []:
        match = re.fullmatch(r"([^:\n]+):L?(\d+)(?:-L?(\d+))?", str(citation))
        if match:
            lo, hi = int(match[2]), int(match[3] or match[2])
            if 1 <= lo <= hi and hi - lo < 10000:
                locations.append((match[1], lo, hi))
    if not finding.get("filepath") and locations:
        finding["filepath"] = locations[0][0]
    if finding.get("filepath") and not finding.get("line_numbers"):
        finding["line_numbers"] = sorted({line for path, lo, hi in locations
                                           if path == finding["filepath"] for line in range(lo, hi + 1)})
