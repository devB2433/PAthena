"""Process boundary around the pinned Mantis runtime (never imported by the API)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from .config import contained_file
from .mantis_paths import native_source_path
from .skills import digest
from .localization import tr
from .store import BudgetExceeded, Store
from .provider import ProviderUnavailable, ProviderBlocked, OutputContractError
from .model_contract import accounted_response, validate_native_response, validate_review_inventory, typed_native_tool

ENGINE = Path(__file__).parent / "vendor/mantis"
SKILLS = {
    "history": "history", "architect": "architecture", "threat_modeler": "threat-model",
    "planner": "plan", "researcher": "researcher", "deduplicator": "dedupe",
    "reviewer": "review", "critic": "critic",
    "chainer": "chain", "calibrator": "calibrate",
    "reflector": "reflect", "reporter": "report",
}
EXECUTION_NODES = {"reproducer", "repro_classifier", "patcher"}
EXECUTION_TOOLS = {"run_sandbox", "run_sandbox_with_evidence", "apply_patch"}


def write_json(path: Path, value):
    temporary = path.with_suffix(path.suffix + ".pending")
    temporary.write_text(json.dumps(value, ensure_ascii=False))
    temporary.replace(path)


def native_path():
    # No target-controlled directory enters sys.path or the worker's cwd.
    sys.path.insert(0, str(ENGINE))
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ.pop("MANTIS_ALLOW_STATIC_WRITE", None)
    os.environ.pop("MANTIS_MODEL", None)
    os.environ.pop("MODEL_ID", None)
    os.environ["OPENAI_API_KEY"] = "local-gateway"
    os.environ["MANTIS_LLM_MAX_PATIENCE_SECONDS"] = "0"
    os.environ["MANTIS_MAX_TOKENS"] = "4096"
    # Use preinstalled grammar wheels, never the language-pack runtime downloader.
    import importlib
    import core.structural_index as index

    def offline_parser(language):
        providers = {"python": ("tree_sitter_python", "language"),
                     "java": ("tree_sitter_java", "language"),
                     "javascript": ("tree_sitter_javascript", "language"),
                     "typescript": ("tree_sitter_typescript", "language_typescript"),
                     "tsx": ("tree_sitter_typescript", "language_tsx"),
                     "go": ("tree_sitter_go", "language"),
                     "c": ("tree_sitter_c", "language")}
        if language not in providers:
            return None
        try:
            from tree_sitter import Language, Parser
            package, function = providers[language]
            return Parser(Language(getattr(importlib.import_module(package), function)()))
        except (ImportError, AttributeError, ValueError):
            return None

    index._load_parser = offline_parser


def phase_spec(phase: str, gateway: str, model: str, database: Path, language: str = "en") -> dict:
    """Partition the native graph and explicitly bypass its dynamic-only stages."""
    spec = json.loads((ENGINE / "workflow.json").read_text())
    prefix = {"history", "structural_index", "architect", "threat_modeler"}
    ids = prefix if phase == "model" else {n["id"] for n in spec["nodes"]} - prefix - EXECUTION_NODES
    spec["name"] = "mantis_" + phase
    spec["nodes"] = [n for n in spec["nodes"] if n["id"] in ids]
    edges = []
    for edge in spec["edges"]:
        if edge["from"] == "critic_classifier" and edge["to"] == "reproducer":
            edge = {**edge, "to": "chainer"}
        elif edge["from"] == "chainer" and edge["to"] == "patcher":
            edge = {**edge, "to": "calibrator"}
        if edge["from"] in ids and edge["to"] in ids:
            edges.append(edge)
    spec["edges"] = edges
    spec["edges"].insert(0, {"from": "START", "to": "history" if phase == "model" else "planner"})
    spec["config"].update({
        "default_model": "openai/" + model, "api_base": gateway.rstrip("/") + "/v1",
        "db_path": str(database), "retry_attempts": 1, "reasoning_effort": None,
        "sandbox": {"type": "static-only", "options": {}},
    })
    for node in spec["nodes"]:
        if node["type"] != "agent":
            continue
        node["tools"] = [tool for tool in node.get("tools", []) if tool not in EXECUTION_TOOLS]
        if node["id"] == "chainer":
            # Preserve native success-only and terminal-dismissal guards, using a
            # completed static stage instead of entering the removed reproducer.
            node["on_enter_status"] = "static_confirmed"
        node.pop("model", None)
        node.pop("reasoning_effort", None)
        if node["id"] not in SKILLS:
            continue
        from core.prompts import get_stage_prompt
        skill = ENGINE / "skills" / ("mantis-" + SKILLS[node["id"]]) / "SKILL.md"
        node["system_prompt"] = (
            f"MANTIS_STAGE:{node['id']}\n" + get_stage_prompt(node["id"])
            + "\n\nUPSTREAM FULL SKILL:\n" + skill.read_text()
            + "\n\nDEPLOYMENT ADAPTER CONTRACT:\n"
            "Use the registered native tools and SQLite workspace artifacts. The code root is the "
            "fixed read-only snapshot. This deployment has no shell or sub-agent-spawn tool; use "
            "the upstream sequential fallback. This is a fully static analysis product: execution, "
            "reproduction and patch tools are not registered, and their workflow stages are removed. "
            "Do not claim dynamic proof. Missing execution conditions alone do not dismiss a static claim. "
            "Persist stage artifacts through record_summary, record_threat_model, record_plan and "
            "report_findings as applicable. Read workspace/kb/design_context.json and "
            "workspace/kb/security_requirements.json as background. During audit planning also read "
            "workspace/kb/requirement_assessments.json; requirement gaps are leads, not confirmed exploits. "
            "For schema-bound nodes finish "
            "through set_model_response using the native bound schema."
        )
        if node["id"] == "calibrator":
            rules = ENGINE / "skills/mantis-calibrate/references/calibration_rules.md"
            node["system_prompt"] += "\n\nFULL CALIBRATION RULES:\n" + rules.read_text()
        if node["id"] == "chainer":
            node["system_prompt"] += (
                "\n\nSTATIC CHAIN ADAPTATION: Reason only from source and explicit preconditions. "
                "The upstream dynamic-success prerequisite is replaced by the reviewer's confirmed "
                "and critic's viable static verdicts. No finding has dynamic reproduction evidence. "
                "Record any chain as a static hypothesis, preserving unknown links; never claim "
                "the chain ran or promote findings to dynamic_confirmed or patch_verified."
            )
    from .localization import output_policy
    for node in spec["nodes"]:
        if node["type"] == "agent":
            node["system_prompt"] = node.get("system_prompt", "") + "\n\n" + output_policy(language)
    return spec


async def execute_job(job: dict, model_factory=None) -> dict:
    native_path()
    import core.graph_loader as graph
    from core.budget import BudgetConfig, BudgetExceededError
    from core.context import current_run_context
    from core.database import init_db, read_findings, record_artifact
    from core.llm_gateway import SecretScrubber, wrap_untrusted_content
    from core.config import ResilientLiteLlm
    from google.adk.runners import Runner
    import main as native_main

    store = Store(Path(job["application_db"]))
    run = store.run(job["run_id"])
    phase = job["phase"]
    work = Path(job["work"])
    snapshot = work / "snapshot"
    database = work / "knowledge.db"
    if not database.exists():
        # Native history/lineage may continue within a project; catalogs stay run-local.
        previous = store.rows("SELECT id,snapshot FROM runs WHERE project_id=? AND status='COMPLETED' "
                              "AND mode='full' AND created<? ORDER BY created DESC LIMIT 1",
                              (run["project_id"], run["created"]))
        if (previous and json.loads(previous[0]["snapshot"]).get("mantis_hash") == job.get("engine_hash")
                and json.loads(previous[0]["snapshot"]).get("language", "en") == run["language"]):
            prior = work.parent / previous[0]["id"] / "knowledge.db"
            if prior.is_file():
                import sqlite3
                with sqlite3.connect(prior) as origin, sqlite3.connect(database) as target:
                    origin.backup(target)
    init_db(str(database))
    native_run = run["id"]
    manifest = job["repository"]
    snapshot.mkdir(parents=True, exist_ok=True)
    for item in manifest["files"]:
        if item["status"] != "READABLE":
            continue
        destination = snapshot / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and digest(destination.read_bytes()) != item["sha256"]:
            raise ValueError("Mantis 快照已变化")
        if not destination.exists():
            original = contained_file(Path(manifest["root"]), item["path"])
            raw = original.read_bytes()
            if digest(raw) != item["sha256"]:
                raise ValueError("仓库已改变，请创建新的输入快照")
            destination.write_bytes(raw)
            destination.chmod(0o444)
    (snapshot / ".mantis_snapshot_id").write_text(job["snapshot_id"])
    record_artifact(str(database), native_run, "workspace_file", "workspace/.mantis_state.json",
                    json.dumps({"active_snapshot": {"root": str(snapshot),
                               "snapshot_id": job["snapshot_id"], "snapshot_pinned": True}}))
    records = store.records(run["id"])
    for name, kinds in [("design_context", {"fact"}), ("security_requirements", {"requirement"}),
                        ("requirement_assessments", {"assessment", "finding"})]:
        body = json.dumps([r for r in records if r["kind"] in kinds], ensure_ascii=False)
        record_artifact(str(database), native_run, "workspace_file", f"workspace/kb/{name}.json",
                        wrap_untrusted_content(SecretScrubber.scrub(body), filename=name))

    stage_tasks = {}
    read_file_path = work / (phase + "-reads.json")
    verdict_file_path = work / (phase + "-verdicts.json")
    submission_path = work / (phase + "-submissions.json")
    submissions = json.loads(submission_path.read_text()) if submission_path.exists() else []
    read_ranges = json.loads(read_file_path.read_text()) if read_file_path.exists() else []
    verdicts = json.loads(verdict_file_path.read_text()) if verdict_file_path.exists() else []
    budget_paused = False
    model_failure = None
    parent = job["task_id"]

    class BudgetModel(ResilientLiteLlm):
        def __init__(self, model, **kwargs):
            kwargs["max_retries"] = 0
            super().__init__(model=model, **kwargs)

        def _sanitize_structured_response(self, response, schema_cls):
            # Fail the stage rather than manufacture a route or calibration.
            rc = current_run_context.get()
            if rc and (rc.active_node, rc.target_file) in stage_tasks:
                store.save_model_output(stage_tasks[(rc.active_node, rc.target_file)], response.model_dump_json())
            return validate_native_response(response, schema_cls)

        async def generate_content_async(self, llm_request, stream=False):
            from .localization import output_policy
            policy = output_policy(run["language"])
            instruction = str(llm_request.config.system_instruction)
            if policy not in instruction:
                instruction += "\n\n" + policy
                llm_request.config.system_instruction = instruction
            stage = instruction.split("MANTIS_STAGE:", 1)[-1].split("\n", 1)[0]
            if stage not in SKILLS:
                stage = "campaign_planner"
            rc = current_run_context.get()
            if rc:
                rc.active_node = stage
            campaign = getattr(rc, "target_file", "") if rc else str(snapshot)
            stage_key = (stage, campaign)
            if stage_key not in stage_tasks:
                tid = store.add_task(run["id"], "mantis_" + stage,
                                     {"phase": phase, "snapshot": job["snapshot_id"], "campaign": campaign}, parent, "native")
                store.start_task(tid, instruction_hash=digest(instruction.encode()))
                stage_tasks[stage_key] = tid
            tid = stage_tasks[stage_key]
            nonlocal budget_paused, model_failure
            reservation = None
            try:
                if model_failure:
                    raise model_failure  # native catch-and-fallback paths cannot issue more requests
                if store.run(run["id"])["pause_requested"]:
                    raise BudgetExceeded("用户请求暂停")
                from .model_contract import bind_direct_tools
                bind_direct_tools(llm_request)
                if stage == "researcher":
                    from .model_contract import research_report_schema
                    for tool in llm_request.config.tools or []:
                        for declaration in tool.function_declarations or []:
                            if declaration.name == "report_findings":
                                declaration.parameters = None
                                declaration.parameters_json_schema = research_report_schema(StrictReport.model_json_schema())
                serialized = instruction + "".join(c.model_dump_json() for c in llm_request.contents)
                if model_factory:
                    reservation = store.reserve(run["id"], tid, len(serialized.encode()) + 4096)
            except BudgetExceeded:
                budget_paused = True
                raise BudgetExceededError(trigger="application_budget", current_value=0, limit_value=0, run_id=run["id"])
            actual = None
            try:
                delegate = model_factory(stage) if model_factory else super()
                iterator = delegate.generate_content_async(llm_request, stream=False) if model_factory else accounted_response(
                    delegate, llm_request, store, run["id"], tid)
                async for response in iterator:
                    store.save_model_output(tid, response.model_dump_json())
                    schema = getattr(llm_request.config, "response_schema", None)
                    if schema is None and "set_model_response" in llm_request.tools_dict:
                        schema = getattr(llm_request.tools_dict["set_model_response"], "output_schema", None)
                    validate_native_response(response, schema)
                    if stage == "calibrator":
                        from .model_contract import validate_calibration_identity
                        from core.schemas import FindingCalibration
                        prompt = "".join(p.text or "" for c in llm_request.contents for p in c.parts)
                        import re
                        match = re.search(r"(?m)^- Finding ID: (\d+)$", prompt)
                        if not match:
                            raise OutputContractError("风险评级请求缺少候选问题编号")
                        validate_native_response(response, FindingCalibration)
                        validate_calibration_identity(response, FindingCalibration, int(match[1]))
                    artifact = {"architect": ("record_summary", "summary"),
                                "threat_modeler": ("record_threat_model", "threat_model"),
                                "planner": ("record_plan", "plan"),
                                "researcher": ("report_findings", "report")}.get(stage)
                    if artifact and not any(s["stage"] == stage and s["campaign"] == campaign
                                            and s["tool"] == artifact[0] for s in submissions):
                        from .model_contract import submit_final_artifact
                        submit_final_artifact(response, artifact[1], tools.TOOLS[artifact[0]])
                    if stage == "reviewer":
                        from core.database import FALSE_POSITIVE_STATUSES
                        expected = [f["id"] for f in read_findings(str(database), run_id=native_run, scope_path=campaign)
                                    if f.get("status") not in FALSE_POSITIVE_STATUSES]
                        validate_review_inventory(response, schema, expected)
                    usage = response.usage_metadata
                    if usage is not None:
                        actual = getattr(usage, "total_token_count", None)
                    yield response
            except (ProviderUnavailable, ProviderBlocked, OutputContractError, BudgetExceeded) as exc:
                model_failure = exc
                raise
            finally:
                if reservation:
                    store.settle(reservation, actual)

    original_model, original_node = graph.LiteLlm, graph.node
    original_runner, original_main_model = native_main.Runner, native_main.ResilientLiteLlm
    original_sandbox = native_main.build_sandbox

    def snapshot_sandbox(cfg, target_path=""):
        if cfg.get("type", "static-only") not in {"static-only", "static"}:
            raise ValueError("此部署只允许静态分析")
        if not Path(target_path).resolve().is_relative_to(snapshot.resolve()):
            raise ValueError("Mantis 调查范围超出固定快照")
        # A campaign focuses attention; its source navigation can follow callers
        # anywhere in the same approved snapshot, never outside it.
        return original_sandbox(cfg, str(snapshot))

    def literal_node(obj, *args, **kwargs):
        from google.adk.agents import Agent
        if isinstance(obj, Agent) and isinstance(obj.instruction, str):
            text = obj.instruction
            obj.instruction = lambda _context: text
        return original_node(obj, *args, **kwargs)

    graph.node = literal_node
    graph.LiteLlm = BudgetModel
    # Preserve native tool behavior; only record precisely the source ranges returned.
    import tools
    from core.schemas import CodebaseSummary, ThreatModel, ReviewPlan, VulnerabilityReport, FindingSchema
    from pydantic import ConfigDict, Field, AliasChoices
    class StrictSummary(CodebaseSummary):
        model_config = ConfigDict(extra="forbid")
        architecture_summary: str = ""
        trust_boundaries: list[str] = []
        attack_surface: list[str] = []
    class StrictThreatModel(ThreatModel):
        model_config = ConfigDict(extra="forbid")
    class StrictReport(VulnerabilityReport):
        findings: list[FindingSchema] = Field(validation_alias=AliasChoices("findings", "vulnerabilities"))
    strict_tools = {}
    def tool_failure(exc):
        nonlocal model_failure
        model_failure = exc
    def tool_submitted(name, value):
        rc = current_run_context.get()
        campaign = rc.target_file if rc else str(snapshot)
        stage = rc.active_node if rc else ""
        submissions.append({"stage": stage, "campaign": campaign, "tool": name, "parameters": value})
        key = (stage, campaign)
        if key in stage_tasks:
            store.save_model_submission(stage_tasks[key], stage, name, campaign, value)
        write_json(submission_path, submissions)
    def candidate_fields(value):
        from .model_contract import project_research_findings
        rc = current_run_context.get()
        return project_research_findings(value) if rc and rc.active_node == "researcher" else value
    from .model_contract import project_declared_fields
    for name, schema, parameter, meaningful in [
        ("record_summary", StrictSummary, "summary", lambda x: bool(x.overview.strip() and x.key_modules)),
        ("record_threat_model", StrictThreatModel, "threat_model", lambda x: bool(x.entry_points or x.trust_boundaries or x.threats)),
        ("record_plan", ReviewPlan, "plan", None),
        ("report_findings", StrictReport, "report", lambda x: all(
            f.title and f.description and f.impact and f.filepath and f.line_numbers for f in x.findings)),
    ]:
        strict_tools[name] = tools.TOOLS[name]
        tools.TOOLS[name] = typed_native_tool(strict_tools[name], schema, parameter, meaningful,
                                             tool_submitted, tool_failure,
                                             preserve_citation_research=name == "report_findings",
                                             project_fields=(candidate_fields if name == "report_findings" else
                                                 project_declared_fields(schema) if name in {
                                                     "record_summary", "record_threat_model"} else None))
    original_calibration_parser = graph._parse_finding_calibration
    def strict_calibration(*args, **kwargs):
        from .model_contract import parse_calibration
        from core.schemas import FindingCalibration
        text = args[0] if args else kwargs["resp_text"]
        expected_id = args[1] if len(args) > 1 else kwargs["f_id"]
        return parse_calibration(text, expected_id, FindingCalibration)
    graph._parse_finding_calibration = strict_calibration
    original_read = tools.TOOLS["read_file"]

    async def read_file(filepath: str, start_line: int = 0, end_line: int = 0) -> str:
        result = await original_read(filepath, start_line, end_line)
        if "UNTRUSTED_SOURCE_CODE_DATA_START" in result and not filepath.startswith("workspace/"):
            # Native read_file caps large responses. Do not mark its omitted tail as read.
            try:
                path = native_source_path(snapshot, filepath)
                source = contained_file(snapshot, path)
                raw_text = source.read_bytes().decode("utf-8", errors="replace")
                lines = raw_text.splitlines()
                lo, hi = max(1, start_line), min(end_line or len(lines), len(lines))
                if "[TRUNCATED: File exceeds" in result:
                    from tools.research_tools import MAX_READ_SIZE
                    selected = "".join(raw_text.splitlines(keepends=True)[lo - 1:hi])
                    hi = lo + selected[:MAX_READ_SIZE].count("\n") - 1
                read_ranges.append({"path": path, "start": lo, "end": hi,
                                    "stage": current_run_context.get().active_node})
                write_json(read_file_path, read_ranges)
            except (ValueError, UnicodeError):
                pass
        return result

    tools.TOOLS["read_file"] = read_file
    tools.research_tools.read_file = read_file
    original_boundary = tools.TOOLS["get_function_boundary"]

    async def get_function_boundary(filepath: str, line: int) -> str:
        result = await original_boundary(filepath, line)
        import re
        match = re.search(r"\] (.+):(\d+)-(\d+)", result.split("\n", 1)[0])
        if match and "[TRUNCATED:" not in result:
            path = native_source_path(snapshot, match[1])
            read_ranges.append({"path": path, "start": int(match[2]), "end": int(match[3]),
                                "stage": current_run_context.get().active_node})
            write_json(read_file_path, read_ranges)
        return result

    tools.TOOLS["get_function_boundary"] = get_function_boundary
    spec = phase_spec(phase, job["gateway"], job["model_id"], database, run["language"])
    layout = work / (phase + "-workflow.json")
    layout.write_text(json.dumps(spec, ensure_ascii=False))
    spec["config"].update({"sessions_db_path": str(work / (phase + "-sessions.db")),
                            "kb_snapshot_id": job["snapshot_id"]})
    layout.write_text(json.dumps(spec, ensure_ascii=False))
    class ObservedRunner(Runner):
        async def run_async(self, **kwargs):
            async for event in super().run_async(**kwargs):
                node_path = getattr(getattr(event, "node_info", None), "path", None)
                stage = node_path.split("/")[-1].split("@")[0] if node_path else event.author
                rc = current_run_context.get()
                if rc and stage:
                    rc.active_node = stage
                if event.content:
                    for part in event.content.parts or []:
                        if part.function_call and part.function_call.name == "set_model_response":
                            verdicts.append({"stage": stage, "verdict": dict(part.function_call.args or {})})
                        if part.text and stage in {"reviewer", "critic", "reproducer"}:
                            try:
                                verdicts.append({"stage": stage, "verdict": json.loads(part.text)})
                            except ValueError:
                                pass
                    write_json(verdict_file_path, verdicts)
                yield event
    native_main.Runner = ObservedRunner
    native_main.ResilientLiteLlm = BudgetModel
    native_main.build_sandbox = snapshot_sandbox
    try:
        code = await native_main.pipeline(
            str(snapshot), workflow_path=str(layout), auto_configure=False, load_local=False,
            db_override=str(database), model_override="openai/" + job["model_id"],
            api_base_override=job["gateway"].rstrip("/") + "/v1", sandbox_override="static-only",
            resume_run_id=native_run, enable_compaction=False, enable_context_cache=False,
            budget_config=BudgetConfig(max_tokens=0, max_llm_calls=run["max_requests"],
                                       max_wall_clock_seconds=3600, max_graph_steps=500,
                                       max_node_visits=20, max_node_tool_calls=100),
            scan_mode_override="whole" if phase == "model" else "auto", assume_yes=True,
            path_root=str(snapshot), parallel=1,
        )
        if model_failure:
            raise model_failure
        if budget_paused or code == 2:
            raise BudgetExceeded("累计预算不足或用户暂停")
        if code:
            raise ValueError("Mantis 工作流未完成，保留原始运行记录")
        required = {"architect": "record_summary", "threat_modeler": "record_threat_model",
                    "planner": "record_plan", "researcher": "report_findings"}
        for stage, campaign in stage_tasks:
            if stage in required and not any(s["stage"] == stage and s["tool"] == required[stage]
                                            and s["campaign"] == campaign for s in submissions):
                raise OutputContractError(f"Mantis {stage} 未提交约定结构化产物，阶段未完成")
        artifacts = store_native_artifacts(database, native_run)
        if phase == "model" and not any(a["artifact_type"] == "threat_model" for a in artifacts):
            raise ValueError("Mantis 未提交威胁模型，不能视为建模完成")
        if phase == "model":
            summaries = [json.loads(a["content"]) for a in artifacts if a["artifact_type"] == "summary"]
            if not any(s.get("overview") and s.get("key_modules") for s in summaries):
                raise OutputContractError("Mantis 未提交有效结构化系统摘要，建模未完成")
        for tid in stage_tasks.values():
            store.finish_service(tid, {"engine": "mantis", "gaps": []})
        from core.correlator import correlate
        findings = read_findings(str(database), run_id=native_run)
        return {"engine": "mantis", "phase": phase, "findings": findings, "artifacts": artifacts,
                "verdicts": verdicts, "reads": read_ranges, "correlations": correlate(findings),
                "snapshot_id": job["snapshot_id"], "gaps": [
                    tr("Mantis 静态模式：没有动态复现、攻击链动态验证或补丁验证", run["language"]),
                    tr("Mantis 根据原生 auto 策略选择调查范围；无子 Agent 工具时采用上游顺序降级路径", run["language"]),
                ]}
    except BaseException:
        for tid in stage_tasks.values():
            if store.task(tid)["status"] == "RUNNING":
                store.fail_task(tid, "Mantis 阶段尚未完成", "PENDING")
        raise
    finally:
        graph.LiteLlm, graph.node = original_model, original_node
        native_main.Runner, native_main.ResilientLiteLlm = original_runner, original_main_model
        native_main.build_sandbox = original_sandbox
        tools.TOOLS["read_file"] = original_read
        tools.research_tools.read_file = original_read
        tools.TOOLS["get_function_boundary"] = original_boundary
        tools.TOOLS.update(strict_tools)
        graph._parse_finding_calibration = original_calibration_parser


def store_native_artifacts(database: Path, run_id: str) -> list[dict]:
    import sqlite3
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT artifact_type,filepath,content,metadata_json FROM campaign_artifacts WHERE run_id=?", (run_id,)
        )]


def query(job: dict) -> dict:
    native_path()
    from core.context import RunContext, current_run_context
    from tools import structural_tools
    work = Path(job["work"])
    token = current_run_context.set(RunContext(
        str(work / "snapshot"), str(work / "knowledge.db"), run_id=job["run_id"],
        snapshot_id=job["snapshot_id"], path_root=str(work / "snapshot"),
    ))
    try:
        if job["operation"] == "get_model":
            from tools.research_tools import get_summary, get_threat_model
            return {"result": json.dumps({"summary": get_summary(), "threat_model": get_threat_model()})}
        allowed = {name: getattr(structural_tools, name) for name in
                   ("find_symbol", "find_callers", "find_callees", "get_function_boundary")}
        result = allowed[job["operation"]](**job["args"])
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        read = None
        if job["operation"] == "get_function_boundary" and "[TRUNCATED:" not in result:
            import re
            match = re.search(r"\] (.+):(\d+)-(\d+)", result.split("\n", 1)[0])
            if match:
                read = {"path": match[1], "start": int(match[2]), "end": int(match[3])}
        return {"result": result, "read": read}
    finally:
        current_run_context.reset(token)


def main():
    encoded = Path(sys.argv[1]).read_text()
    job = json.loads(encoded)
    output = Path(sys.argv[2])
    try:
        result = query(job) if job.get("operation") else asyncio.run(execute_job(job))
    except BudgetExceeded:
        result = {"error": "budget", "message": "累计预算不足或用户暂停"}
    except (ProviderUnavailable, ProviderBlocked, OutputContractError) as exc:
        result = {"error": type(exc).__name__, "message": str(exc), "exception_type": type(exc).__name__, "code": exc.code}
    except Exception as exc:
        from core.budget import BudgetExceededError
        result = {"error": "budget" if isinstance(exc, BudgetExceededError) else "engine",
                  "message": "Mantis 静态审计未完成", "exception_type": type(exc).__name__}
    result["job_hash"] = digest(encoded.encode())
    write_json(output, result)


if __name__ == "__main__":
    main()
