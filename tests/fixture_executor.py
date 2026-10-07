from security_auditor.domain import StageOutput
from security_auditor.skills import SkillBundle
from security_auditor.store import Store


class FixtureExecutor:
    """Deterministic fixture executor. Never used for real projects or compliance claims."""

    def __init__(self, store: Store):
        self.store = store

    async def execute(self, task: dict, bundle: SkillBundle, context: dict) -> StageOutput:
        from security_auditor.domain import Assessment, Fact, Finding, Requirement, Review, Risk, Threat

        reservation = self.store.reserve(task["run_id"], task["id"], 300)
        self.store.settle(reservation, 300)
        doc = self.store.evidence(task["run_id"], "document")[0]["id"]
        code = self.store.evidence(task["run_id"], "code")
        refs = [doc] + ([code[0]["id"]] if code else [])
        for eid in refs:
            self.store.note_read(task["id"], eid)
        role = bundle.skill_id
        records = []
        if role in {"design_analyst", "code_architect", "vulnerability_planner"}:
            records = [
                Fact(
                    title="订单模块与租户数据边界",
                    module="订单模块",
                    fact_type="BOUNDARY",
                    basis="OBSERVED" if role == "code_architect" else "DECLARED",
                    evidence_ids=refs,
                    rationale="构造样例展示订单接口和租户隔离要求。",
                )
            ]
        elif role == "requirement_generator":
            records = [
                Requirement(
                    title="订单访问必须执行租户隔离",
                    module="订单模块",
                    statement="读取及批量导出订单必须限制为当前租户范围",
                    acceptance_criteria=["单条查询校验租户", "批量导出限制租户范围"],
                    origin="EXPLICIT_DESIGN",
                    evidence_ids=[doc],
                    rationale="构造设计材料明确要求租户只能访问自己的订单。",
                )
            ]
        elif role == "threat_modeler":
            records = [
                Threat(
                    title="批量导出跨租户访问",
                    module="订单模块",
                    attacker="普通租户用户",
                    entrypoint="GET /orders/export",
                    trust_boundary="租户数据边界",
                    preconditions=["已登录租户可调用导出接口"],
                    impact="其他租户订单泄露",
                    evidence_ids=refs,
                    rationale="构造代码用于展示共享控制与入口检查。",
                )
            ]
        elif role == "requirement_checker":
            for ac in task["scope"]["acceptance_criteria"]:
                records.append(
                    Assessment(
                        title=ac,
                        requirement_id=task["scope"]["requirement_id"],
                        acceptance_criterion=ac,
                        module="订单模块",
                        entrypoint="/orders/export" if "批量" in ac else "/orders/{id}",
                        design_status="SUPPORTED",
                        implementation_status=("VIOLATED" if "批量" in ac else "STATIC_SUPPORTED"),
                        evidence_ids=refs,
                        rationale="演示结果：单条有租户检查，导出未限制租户。",
                    )
                )
        elif role == "vulnerability_researcher" and task["wave"] == "deep":
            records = [
                Finding(
                    title="订单导出缺少租户范围约束",
                    module="订单模块",
                    finding_type="STATIC_VULNERABILITY",
                    evidence_ids=refs,
                    impact="跨租户订单数据泄露",
                    attack_preconditions=["租户用户可调用导出接口"],
                    recommendation="在共享查询层绑定当前租户并覆盖批量接口。",
                    rationale="这是预设构造样例结果，不代表模型已经完成真实分析。",
                )
            ]
        elif role in {"requirement_reviewer", "finding_reviewer", "finding_critic"}:
            records = [
                Review(
                    title="样例证据复核",
                    subject_id=sid,
                    verdict="SUPPORTED",
                    evidence_ids=refs,
                    checked_rules=list(bundle.rule_ids),
                    rationale="演示预设证据与反证检查。",
                )
                for sid in task["scope"].get("subject_ids", [])
            ]
        elif role == "risk_calibrator":
            records = [
                Risk(
                    title="跨租户数据泄露风险",
                    subject_id=sid,
                    severity="HIGH",
                    evidence_strength="STATIC_SUPPORTED",
                    impact_score=4,
                    likelihood_score=4,
                    evidence_ids=refs,
                    rationale="演示预设业务影响，不是实际模型评级。",
                )
                for sid in task["scope"].get("subject_ids", [])
            ]
        return StageOutput(records=records, gaps=[], summary="构造样例阶段完成；未调用真实模型。")


class FixtureMantis:
    """API test adapter only. Native graph behavior is covered in test_mantis.py."""

    def __init__(self, settings, store):
        from security_auditor.mantis_engine import MantisEngine
        self.engine = MantisEngine(settings, store)
        self.fingerprint = self.engine.fingerprint
        self.store = store
        self.fixture = FixtureExecutor(store)

    def workspace(self, run_id):
        return self.engine.workspace(run_id)

    async def execute(self, run_id, phase, parent):
        from security_auditor.skills import SkillLoader
        from security_auditor.config import ROOT
        tid = self.store.add_task(run_id, "mantis_" + phase, {}, parent, "engine")
        self.store.start_task(tid)
        task = self.store.task(tid)
        loader = SkillLoader(ROOT / "skills")
        records = []
        for role in (["code_architect", "threat_modeler"] if phase == "model" else
                     ["vulnerability_researcher", "risk_calibrator"]):
            bundle = loader.resolve(role, role, "0.1.0")
            task["wave"] = "deep"
            task["scope"] = {"subject_ids": [r.id for r in records if r.kind == "finding"]}
            output = await self.fixture.execute(task, bundle, {})
            for record in output.records:
                record.engine = "mantis"
                if record.kind == "finding":
                    record.status = "STATIC_SUPPORTED"
                    record.native_status = "static_confirmed"
                if record.kind == "risk":
                    record.native_score = 6.4
                records.append(record)
        self.store.commit_output(tid, "mantis_engine", StageOutput(records=records, summary="Test adapter"))
