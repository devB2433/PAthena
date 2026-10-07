import json
import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from security_auditor.config import contained_file
from security_auditor.domain import Assessment, Finding, Requirement, StageOutput
from security_auditor.skills import SkillLoader, compile_instruction
from security_auditor.store import BudgetExceeded


def test_skill_is_full_and_tampering_fails(settings, tmp_path):
    loader = SkillLoader(settings.skills_dir)
    bundle = loader.resolve("finding_reviewer", "finding_reviewer", "0.1.0")
    instruction, _ = compile_instruction(bundle)
    assert len(instruction) > 1000
    assert instruction.index("[FINDING_REVIEWER-13]") > 1000
    assert all(f"[{rule}]" in instruction for rule in bundle.rule_ids)
    shutil.copytree(settings.skills_dir, tmp_path / "skills")
    file = tmp_path / "skills/finding_reviewer/SKILL.md"
    file.write_text(file.read_text() + "injected")
    with pytest.raises(ValueError, match="摘要"):
        SkillLoader(tmp_path / "skills").resolve("finding_reviewer", "finding_reviewer", "0.1.0")


def test_no_missing_or_incompatible_skill_fallback(settings):
    with pytest.raises(ValueError):
        SkillLoader(settings.skills_dir).resolve("code_architect", "missing", "0.1.0")
    with pytest.raises(ValueError):
        SkillLoader(settings.skills_dir).resolve("code_architect", "code_architect", "0.2.0")


def test_path_boundaries(tmp_path):
    (tmp_path / "allowed").write_text("ok")
    (tmp_path / "link").symlink_to(tmp_path / "allowed")
    assert contained_file(tmp_path, "allowed").read_text() == "ok"
    for path in ["../allowed", "link", str(tmp_path / "allowed")]:
        with pytest.raises(ValueError):
            contained_file(tmp_path, path)


def test_cross_snapshot_evidence_rejected(store, sample_run):
    project, run, eid = sample_run
    other = store.create_run(project["id"], "full", {}, False, 100, 200000)
    tid = store.add_task(other["id"], "research", {})
    store.start_task(tid)
    finding = Finding(
        title="bad",
        module="订单模块",
        evidence_ids=[eid],
        impact="data",
        recommendation="fix",
        finding_type="STATIC_VULNERABILITY",
        rationale="source",
    )
    with pytest.raises(ValueError, match="快照"):
        store.commit_output(tid, "vulnerability_researcher", StageOutput(records=[finding], summary="result"))
    assert store.task(tid)["status"] == "RUNNING"
    assert store.records(other["id"]) == []


def test_inventory_and_atomic_commit(store, sample_run):
    _, run, eid = sample_run
    req = Requirement(
        title="租户隔离",
        module="订单模块",
        statement="隔离数据",
        acceptance_criteria=["单条", "批量"],
        origin="EXPLICIT_DESIGN",
        evidence_ids=[eid],
        rationale="doc",
    )
    first = store.add_task(run["id"], "requirements", {})
    store.start_task(first)
    store.note_read(first, eid)
    store.commit_output(first, "requirement_generator", StageOutput(records=[req], summary="baseline"))
    tid = store.add_task(run["id"], "check", {"requirement_id": req.id,
                                            "acceptance_criteria": ["单条", "批量"]})
    store.start_task(tid)
    store.note_read(tid, eid)
    assessment = Assessment(
        title="单条",
        requirement_id=req.id,
        acceptance_criterion="单条",
        module="订单模块",
        entrypoint="/orders",
        design_status="SUPPORTED",
        implementation_status="STATIC_SUPPORTED",
        evidence_ids=[eid],
        rationale="static",
    )
    with pytest.raises(ValueError, match="库存"):
        store.commit_output(tid, "requirement_checker", StageOutput(records=[assessment], summary="partial"))
    assert len(store.records(run["id"])) == 1
    second = assessment.model_copy(
        update={
            "id": "second",
            "title": "批量",
            "acceptance_criterion": "批量",
            "implementation_status": "UNKNOWN",
        }
    )
    output = StageOutput(records=[assessment, second], summary="complete inventory")
    store.commit_output(tid, "requirement_checker", output)
    store.commit_output(tid, "requirement_checker", output)
    assert len(store.records(run["id"])) == 3
    assert store.task(tid)["status"] == "SUCCEEDED"


def test_dynamic_states_not_in_static_contract():
    with pytest.raises(ValidationError):
        Finding(
            title="bad",
            module="订单模块",
            evidence_ids=[],
            impact="data",
            recommendation="fix",
            finding_type="STATIC_VULNERABILITY",
            rationale="source",
            status="dynamic_confirmed",
        )


def test_budget_reservations_are_atomic_and_survive_restart(store, sample_run, settings):
    _, run, _ = sample_run
    with store.connect() as db:
        db.execute("UPDATE runs SET max_requests=1 WHERE id=?", (run["id"],))
    tid = store.add_task(run["id"], "research", {})

    def reserve():
        try:
            return store.reserve(run["id"], tid, 1000)
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        reserved = list(pool.map(lambda _: reserve(), range(4)))
    assert len([r for r in reserved if r]) == 1
    store.recover()
    assert store.run(run["id"])["usage"] == {"requests": 1, "tokens": 1000}
    with pytest.raises(BudgetExceeded):
        store.reserve(run["id"], tid, 1000)


def test_unlimited_budget_is_explicit_per_run_and_keeps_usage(store, sample_run):
    project, run, _ = sample_run
    other = store.create_run(project["id"], "code_only", {}, False, 0, 0)
    tid = store.add_task(run["id"], "planner", {}, wave="native")
    blocked = store.add_task(other["id"], "planner", {}, wave="native")
    with store.connect() as db:
        db.execute("UPDATE runs SET max_requests=0,max_tokens=0 WHERE id=?", (run["id"],))
        db.execute("INSERT INTO run_budget_policy VALUES(?,1)", (run["id"],))
    for _ in range(15):
        request = store.reserve(run["id"], tid, 1_000_000)
        store.settle(request, 500_000)
    assert store.run(run["id"])["unlimited_budget"]
    assert store.run(run["id"])["usage"] == {"requests": 15, "tokens": 7_500_000}
    assert not store.run(other["id"])["unlimited_budget"]
    with pytest.raises(BudgetExceeded):
        store.reserve(other["id"], blocked, 1)
    own = store.add_task(run["id"], "design", {})
    for _ in range(12):
        store.settle(store.reserve(run["id"], own, 100), 100)
    with pytest.raises(BudgetExceeded):
        store.reserve(run["id"], own, 100)


def test_idempotency_key_does_not_reuse_other_snapshot(store, sample_run):
    project, _, _ = sample_run
    first = store.create_run(project["id"], "full", {"v": 1}, False, 10, 1000, "click")
    second = store.create_run(project["id"], "full", {"v": 1}, False, 10, 1000, "click")
    assert first["id"] == second["id"]
    with pytest.raises(ValueError):
        store.create_run(project["id"], "full", {"v": 2}, False, 10, 1000, "click")


def test_evidence_integrity(store, sample_run):
    _, run, eid = sample_run
    with store.connect() as db:
        db.execute("UPDATE evidence SET content='tampered' WHERE id=?", (eid,))
    with pytest.raises(ValueError, match="摘要"):
        store.read_evidence(run["id"], eid)


def test_workflow_bindings(settings):
    workflow = json.loads(settings.workflow.read_text())
    seen = set()
    for node in workflow["nodes"]:
        assert set(node["depends_on"]) <= seen
        seen.add(node["id"])
        if node["kind"] == "agent":
            SkillLoader(settings.skills_dir).resolve(node["role"], node["skill_id"], node["skill_version"])


def test_citation_must_have_been_read_by_this_task(store, sample_run):
    _, run, eid = sample_run
    tid = store.add_task(run["id"], "research", {})
    store.start_task(tid)
    finding = Finding(
        title="引用核对",
        module="订单模块",
        evidence_ids=[eid],
        impact="data",
        recommendation="fix",
        finding_type="STATIC_VULNERABILITY",
        rationale="source",
    )
    output = StageOutput(records=[finding], summary="candidate")
    with pytest.raises(ValueError, match="实际读取"):
        store.commit_output(tid, "vulnerability_researcher", output)
    store.note_read(tid, eid)
    store.commit_output(tid, "vulnerability_researcher", output)
    assert store.task(tid)["status"] == "SUCCEEDED"
