import asyncio
import json

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from security_auditor.ingestion import repository_inventory
from security_auditor.mantis_engine import MantisEngine, engine_fingerprint
from security_auditor.mantis_worker import execute_job, native_path, phase_spec, query
from security_auditor.runtime import result_matrix
from security_auditor.skills import digest
from security_auditor.store import BudgetExceeded


class NativeScript(BaseLlm):
    model: str = "native-test"
    stage: str
    language: str = "en"
    dismiss: bool = False
    reject: bool = False
    source_path: str = "orders.py"
    bad_summary: bool = False
    final_artifacts: bool = False
    citation_retry: bool = False
    premature_metadata: bool = False
    _calls: int = PrivateAttr(default=0)
    _requests: list = PrivateAttr(default_factory=list)
    _tools: set = PrivateAttr(default_factory=set)

    async def generate_content_async(self, llm_request, stream=False):
        self._calls += 1
        from security_auditor.localization import output_policy
        assert output_policy(self.language) in str(llm_request.config.system_instruction)
        self._tools.update(llm_request.tools_dict)
        self._requests.append(str(llm_request.config.system_instruction) + "".join(
            c.model_dump_json() for c in llm_request.contents))
        call, args, text = None, {}, "阶段完成" if self.language == "zh-CN" else "Stage complete"
        i, stage = self._calls, self.stage
        if stage in {"history", "architect", "threat_modeler", "researcher", "reviewer", "critic"} and i == 1:
            call, args = "read_file", {"filepath": self.source_path}
        elif stage == "architect" and i == 2:
            call, args = "record_summary", {"summary": ({"components": ["orders"]} if self.bad_summary else {
                "overview": ("订单服务" if self.language == "zh-CN" else "Order service"), "key_modules": ["orders"], "architecture_summary": ("租户订单" if self.language == "zh-CN" else "Tenant orders"),
                "trust_boundaries": [("租户边界" if self.language == "zh-CN" else "Tenant boundary")], "attack_surface": ["export_orders"],
            })}
        elif stage == "threat_modeler" and i == 2:
            call, args = "record_threat_model", {"threat_model": {
                "threat_actors": [("租户" if self.language == "zh-CN" else "tenant")], "trust_boundaries": [("租户数据" if self.language == "zh-CN" else "tenant data")],
                "entry_points": ["export_orders"], "key_risks": [("数据泄露" if self.language == "zh-CN" else "data disclosure")],
                "threats": [("title: 租户数据泄露; target_component: 订单; attack_vector: 导出; impact: 数据泄露" if self.language == "zh-CN" else "title: Tenant data exposure; target_component: orders; attack_vector: export; impact: disclosure")],
            }}
        elif stage == "planner" and i == 1:
            call, args = "record_plan", {"plan": {"investigations": [
                {"title": ("跨租户导出" if self.language == "zh-CN" else "Tenant export"), "target_files": ["orders.py"], "question": ("导出是否限制租户范围？" if self.language == "zh-CN" else "Does export constrain tenants?")}
            ]}}
        elif stage == "researcher" and i == 2:
            call, args = "get_plan", {}
        elif stage == "researcher" and self.citation_retry and i == 4:
            call, args = "read_file", {"filepath": self.source_path}
        elif stage == "researcher" and (i == 3 or (self.citation_retry and i == 5)):
            call, args = "report_findings", {"report": {"findings": [{
                "title": ("跨租户导出" if self.language == "zh-CN" else "Tenant export"), "filepath": "orders.py", "line_numbers": [2],
                "description": ("导出读取全部租户数据" if self.language == "zh-CN" else "Export reads all tenants"), "code_paths": ["orders.py:2"],
                "severity": "HIGH", "mitigation": ("限制查询的租户范围" if self.language == "zh-CN" else "Constrain query"), "impact": ("数据泄露" if self.language == "zh-CN" else "Data disclosure"),
            }]}}
            if self.citation_retry and i == 3:
                args["report"]["findings"][0]["code_paths"] = ["orders.py:0"]
            if self.premature_metadata:
                args["report"]["findings"][0].update({"mantis_risk_score":9.9,
                    "production_viability":"NON_VIABLE", "repro_status":"reproduced",
                    "calibration_checklist":{"repro_failure":{"outcome":"NOT_APPLICABLE"}}})
        elif stage == "deduplicator" and i == 1:
            call = "get_findings"
        elif stage == "reviewer" and i == 2:
            text = json.dumps({"route": "false_positive" if self.dismiss else "confirmed",
                               "reason": ("公共控制有效" if self.language == "zh-CN" else "Shared control") if self.dismiss else ("缺少租户范围检查" if self.language == "zh-CN" else "Missing tenant constraint"),
                               "finding_verdicts": [{"finding_id": 1,
                                   "route": "false_positive" if self.dismiss else "confirmed",
                                   "reason": ("公共控制有效" if self.language == "zh-CN" else "Shared control") if self.dismiss else ("缺少租户范围检查" if self.language == "zh-CN" else "Missing tenant constraint")}]})
        elif stage == "critic" and i == 2:
            text = json.dumps({"route": "non_viable" if self.reject else "viable",
                               "reason": ("不可达" if self.language == "zh-CN" else "Unreachable") if self.reject else ("租户可达导出入口" if self.language == "zh-CN" else "Tenant reaches export")})
        elif stage == "calibrator":
            text = json.dumps({"finding_id": 1, "mantis_risk_score": 6.4, "priority": "HIGH",
                               "impact_score": 4, "likelihood_score": 3, "inferred_exposure": "EXPOSED",
                               "reasoning": ("原生静态风险评级" if self.language == "zh-CN" else "Native static calibration")})
        elif stage == "campaign_planner":
            text = "{}"
        if self.final_artifacts and call in {"record_summary", "record_threat_model", "record_plan", "report_findings"}:
            text, call = json.dumps(args), None
        content = types.Content(role="model", parts=[types.Part(
            function_call=types.FunctionCall(name=call, args=args)) if call else types.Part(text=text)])
        yield LlmResponse(content=content, usage_metadata=types.GenerateContentResponseUsageMetadata(
            total_token_count=50, prompt_token_count=40, candidates_token_count=10))


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", ["en", "zh-CN"])
@pytest.mark.parametrize("dismiss,reject,final_artifacts,citation_retry,premature_metadata", [
    (False, False, False, False, False), (True, False, False, False, False),
    (False, True, False, False, False), (False, False, True, False, False),
    (False, False, False, True, False), (False, False, True, True, False),
    (False, False, True, False, True)])
async def test_native_mantis_graph_tools_and_static_verdicts(settings, store, tmp_path, monkeypatch, dismiss, reject, final_artifacts, citation_retry,premature_metadata, selected):
    native_path()
    from core.environments.static_env import StaticOnlyEnvironment
    execution_attempts = []

    async def forbid_execution(*args, **kwargs):
        execution_attempts.append((args, kwargs))
        raise AssertionError("A static audit must not invoke execution or patch application")

    monkeypatch.setattr(StaticOnlyEnvironment, "execute", forbid_execution)
    monkeypatch.setattr(StaticOnlyEnvironment, "apply_patch", forbid_execution)
    repository = tmp_path / "repository"
    repository.mkdir()
    marker = tmp_path / "target-executed"
    (repository / "orders.py").write_text(
        "def export_orders(db):\n    return db.query_all()\n"
        f"\nopen({str(marker)!r}, 'w').write('executed')\n")
    manifest = {"root": str(repository), "files": repository_inventory(repository, 50)}
    project = store.create_project("native-test")
    run = store.create_run(project["id"], "full", {"repository": manifest, "mantis_hash": engine_fingerprint(), "language": selected},
                           False, 100, 200000)
    engine = MantisEngine(settings, store)
    work = engine.workspace(run["id"])
    work.mkdir(parents=True)
    models = {}

    def factory(stage):
        return models.setdefault(stage, NativeScript(stage=stage, language=selected, dismiss=dismiss, reject=reject,
                                  final_artifacts=final_artifacts,
                                  citation_retry=citation_retry,
                                  premature_metadata=premature_metadata,
                                  source_path=str(work / "snapshot/orders.py")))

    exports = []
    for phase in ("model", "audit"):
        tid = store.add_task(run["id"], "mantis_" + phase, {}, wave="engine")
        store.start_task(tid)
        job = {"phase": phase, "run_id": run["id"], "task_id": tid,
            "work": str(work), "repository": manifest, "application_db": str(store.path),
            "gateway": "http://invalid.test", "model_id": "deepseek-flash", "snapshot_id": "fixed-snapshot"}
        if phase == "audit" and final_artifacts and citation_retry:
            from security_auditor.provider import OutputContractError
            with pytest.raises(OutputContractError, match="源码引用"):
                await execute_job(job, factory)
            assert models["researcher"]._calls == 3
            assert not store.records(run["id"], "finding") and not marker.exists()
            return
        export = await execute_job(job, factory)
        # Materialized native export allows mapping to be retried without another model call.
        exports.append(export)
        if phase == "model":
            summary = next(a for a in export["artifacts"] if a["artifact_type"] == "summary")
            assert json.loads(summary["content"])["attack_surface"] == ["export_orders"]
        output = engine.map_output(run["id"], tid, export)
        store.commit_output(tid, "mantis_engine", output)
    assert not marker.exists()
    assert all(r["path"] == "orders.py" for export in exports for r in export["reads"])
    from security_auditor.repair_native_reads import recover_reads
    recovered = recover_reads(work, "model", manifest)
    assert recovered and all(r["path"] == "orders.py" for r in recovered)
    assert not execution_attempts
    assert not {"reproducer", "repro_classifier", "patcher"} & models.keys()
    assert not {"run_sandbox", "run_sandbox_with_evidence", "apply_patch"} & set().union(
        *(m._tools for m in models.values()))
    assert exports[1]["findings"][0]["status"] == (
        "false_positive" if dismiss else "non_viable" if reject else "static_confirmed")
    assert "record_plan" in "".join(models["researcher"]._requests) or "investigations" in "".join(models["researcher"]._requests)
    matrix = result_matrix(store, run["id"])
    assert matrix["findings"][0]["review_status"] == ("FALSE_POSITIVE" if dismiss or reject else "STATIC_SUPPORTED")
    if not dismiss and not reject:
        risk = store.records(run["id"], "risk")[0]
        assert risk["score"] == 6.4  # Never replace native score by 4*3.
        if premature_metadata:
            assert exports[1]["findings"][0].get('repro_status') != 'reproduced'
            assert any('NOT_APPLICABLE' in r['content'] for r in store.rows('SELECT content FROM model_outputs'))
        assert "chainer" in models
    else:
        assert "chainer" not in models
    assert store.run(run["id"])["usage"]["requests"] > 10
    if final_artifacts:
        assert models["architect"]._calls == 2 and models["researcher"]._calls == 3
        # Exact final artifacts persisted immediately, without a further model
        # turn to interpret or re-submit them through a tool.
        submissions = json.loads((work / "audit-submissions.json").read_text())
        assert any(s["stage"] == "researcher" and s["tool"] == "report_findings" for s in submissions)
    if citation_retry:
        assert models["researcher"]._calls == 6
        assert "citation verification failed" in models["researcher"]._requests[3]
        assert exports[1]["findings"][0]["line_numbers"] == [2]
        assert len(exports[1]["findings"]) == 1
    assert any(r["kind"] == "threat" for r in store.records(run["id"]))
    native_path()
    result = await asyncio.to_thread(query, {"operation": "get_function_boundary",
                    "args": {"filepath": "orders.py", "line": 2},
                    "work": str(work), "run_id": run["id"], "snapshot_id": "fixed-snapshot"})
    assert "def export_orders" in result["result"]
    assert result["read"]["end"] == 2


@pytest.mark.asyncio
async def test_native_invalid_submission_stops_without_a_repair_turn(settings, store, tmp_path):
    from security_auditor.provider import OutputContractError
    root = tmp_path / "repo"
    root.mkdir()
    (root / "orders.py").write_text("def export_orders(db):\n    return db.query_all()\n")
    manifest = {"root": str(root), "files": repository_inventory(root, 50)}
    project = store.create_project("invalid output fixture")
    run = store.create_run(project["id"], "code_only", {"repository": manifest}, False, 100, 200000)
    tid = store.add_task(run["id"], "model", {}, wave="engine")
    models = {}
    def factory(stage):
        return models.setdefault(stage, NativeScript(stage=stage, bad_summary=True))
    work = MantisEngine(settings, store).workspace(run["id"])
    work.mkdir(parents=True)
    with pytest.raises(OutputContractError):
        await execute_job({"phase": "model", "run_id": run["id"], "task_id": tid,
            "work": str(work), "repository": manifest, "application_db": str(store.path),
            "gateway": "http://invalid.test", "model_id": "deepseek-flash", "snapshot_id": "fixed"}, factory)
    assert models["architect"]._calls == 2
    assert "threat_modeler" not in models
    assert not store.records(run["id"])
    assert store.rows("SELECT content FROM model_outputs")


def test_pinned_graph_and_full_skills_have_native_branches(tmp_path):
    native_path()
    spec = phase_spec("audit", "http://gateway:8081", "deepseek-flash", tmp_path / "native.db")
    from security_auditor.mantis_worker import ENGINE
    upstream = json.loads((ENGINE / "workflow.json").read_text())
    static_edges = [{"from": "critic_classifier", "to": "chainer", "on": "viable"},
                    {"from": "chainer", "to": "calibrator"}]
    for edge in spec["edges"][1:]:
        assert edge in upstream["edges"] or edge in static_edges
    assert all(edge in spec["edges"] for edge in static_edges)
    assert not {"reproducer", "repro_classifier", "patcher"} & {n["id"] for n in spec["nodes"]}
    for phase in ("model", "audit"):
        nodes = phase_spec(phase, "http://gateway:8081", "deepseek-flash", tmp_path / "native.db")["nodes"]
        assert not {"run_sandbox", "run_sandbox_with_evidence", "apply_patch"} & {
            tool for node in nodes for tool in node.get("tools", [])}
    for node in spec["nodes"]:
        if node["type"] == "agent":
            assert "UPSTREAM FULL SKILL" in node["system_prompt"]
    assert any(e.get("on") == "confirmed" for e in spec["edges"])
    assert digest((ENGINE / "core/graph_loader.py").read_bytes()) == json.loads(
        (ENGINE / "SOURCE.json").read_text())["files"]["core/graph_loader.py"]["sha256"]


def test_document_tasks_default_whole_and_split_only_for_context(settings, store, sample_run):
    from security_auditor.runtime import Scheduler
    _, run, _ = sample_run
    for i in range(35):
        store.add_evidence(run["id"], "document", f"design:{i}", "Design section", {})
    scheduler = Scheduler(settings, store)
    scopes = scheduler.scopes(run["id"], "design_analyst")
    assert len(scopes) == 1  # Never split merely because there are over 20 blocks.
    assert len(scopes[0]["evidence_ids"]) == 35
    for i in range(5):
        store.add_evidence(run["id"], "document", f"large:{i}", "安全设计" * 3000, {})
    scopes = scheduler.scopes(run["id"], "design_analyst")
    assert len(scopes) > 1
    assert {eid for scope in scopes for eid in scope["evidence_ids"]} == {
        row["id"] for row in store.evidence(run["id"], "document")}


@pytest.mark.asyncio
@pytest.mark.parametrize('enforce_budgets', [True, False])
async def test_native_budget_pause_preserves_cumulative_usage(settings, store, tmp_path, enforce_budgets):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "orders.py").write_text("def export_orders(db):\n    return db.query_all()\n")
    manifest = {"root": str(repo), "files": repository_inventory(repo, 10)}
    project = store.create_project("budget")
    run = store.create_run(project["id"], "full", {"repository": manifest}, False, 2, 200000)
    work = settings.state_dir / "mantis" / run["id"]
    work.mkdir(parents=True)
    task = store.add_task(run["id"], "threat_model", {}, wave="engine")
    store.start_task(task)
    models = {}
    def factory(stage):
        return models.setdefault(stage, NativeScript(stage=stage))
    job = {"phase": "model", "run_id": run["id"], "task_id": task, "work": str(work),
           "repository": manifest, "application_db": str(store.path), "gateway": "http://invalid.test",
           "model_id": "deepseek-flash", "snapshot_id": "budget-snapshot",
           "enforce_budgets": enforce_budgets}
    if not enforce_budgets:
        result = await execute_job(job, factory)
        assert result['artifacts']
        assert store.run(run['id'])['usage']['requests'] > 2
        assert store.run(run['id'])['max_requests'] == 2  # historical configuration retained
        return
    with pytest.raises(BudgetExceeded):
        await execute_job(job, factory)
    assert store.run(run["id"])["usage"] == {"requests": 2, "tokens": 100}
    with pytest.raises(BudgetExceeded):
        await execute_job(job, factory)
    assert store.run(run["id"])["usage"]["requests"] == 2


def test_native_cross_file_navigation_and_offline_java_go(tmp_path):
    native_path()
    from core.structural_index import build_structural_index
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "sink.py").write_text("def sink(value):\n    return value\n")
    (root / "api.py").write_text("from sink import sink\ndef endpoint(value):\n    return sink(value)\n")
    (root / "Auth.java").write_text("class Auth { boolean allowed(String user) { return user != null; } }")
    (root / "auth.go").write_text('package auth\nfunc validate(token string) bool { return token != "" }\n'
                                'func Authenticate(token string) bool { return validate(token) }\n')
    from core.structural_index import state_dir_for_db
    work = tmp_path
    result = build_structural_index(str(root), state_dir_for_db(str(work / "knowledge.db")), "offline")
    assert result["coverage"]["indexed_files"] == 4
    callers = query({"operation": "find_callers", "args": {"symbol": "sink"}, "work": str(work),
                     "run_id": "navigation-test", "snapshot_id": "offline"})
    assert "api.py" in callers["result"]
    function = query({"operation": "get_function_boundary", "args": {"filepath": "Auth.java", "line": 1},
                      "work": str(work), "run_id": "navigation-test", "snapshot_id": "offline"})
    assert "boolean allowed" in function["result"]
    go_callers = query({"operation":"find_callers", "args":{"symbol":"validate"},
                       "work":str(work), "run_id":"navigation-test", "snapshot_id":"offline"})
    assert 'Authenticate' in go_callers['result']
    go_function = query({"operation":"get_function_boundary", "args":{"filepath":"auth.go","line":3},
                        "work":str(work), "run_id":"navigation-test", "snapshot_id":"offline"})
    assert 'func Authenticate' in go_function['result']


@pytest.mark.asyncio
async def test_native_export_retry_reuses_valid_result_and_recovers_partial_file(settings, store, sample_run, monkeypatch):
    _, run, _ = sample_run
    engine = MantisEngine(settings, store)
    work = engine.workspace(run["id"])
    work.mkdir(parents=True)
    output = work / "model-output.json"
    output.write_text('{"engine":')  # Simulate an interrupted old-format write.
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)

        class Process:
            returncode = 0

            async def wait(self):
                encoded = (work / "model-input.json").read_text()
                output.write_text(json.dumps({"engine": "mantis", "job_hash": digest(encoded.encode())}))
                return 0

        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert (await engine.invoke(run["id"], {"phase": "model"}, "model"))["engine"] == "mantis"
    assert (await engine.invoke(run["id"], {"phase": "model"}, "model"))["engine"] == "mantis"
    assert len(calls) == 1
