from security_auditor.domain import StageOutput
from security_auditor.skills import SkillBundle
from security_auditor.store import Store


class FixtureExecutor:
    """Deterministic fixture executor. Never used for real projects or compliance claims."""

    def __init__(self, store: Store):
        self.store = store

    async def execute(self, task: dict, bundle: SkillBundle, context: dict) -> StageOutput:
        from security_auditor.domain import Assessment, Fact, Requirement, Review, SourceQuote, SourceObligation, ClaimReview, LogicComparison

        reservation = self.store.reserve(task["run_id"], task["id"], 300)
        self.store.settle(reservation, 300)
        doc = self.store.evidence(task["run_id"], "document")[0]["id"]
        code = self.store.evidence(task["run_id"], "code")
        refs = [doc] + ([code[0]["id"]] if code else [])
        for eid in refs:
            self.store.note_read(task["id"], eid)
        role = bundle.skill_id
        records = []
        if role == "design_analyst":
            records = [
                Fact(
                    title="订单模块与租户数据边界",
                    module="订单模块",
                    fact_type="BOUNDARY",
                    basis="DECLARED",
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
        elif role == "requirement_reviewer":
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
        # Transport fixtures populate the production v4 fields. These scripted
        # support decisions are not analysis-quality tests.
        quote = SourceQuote(evidence_id=doc, text=self.store.read_evidence(task['run_id'], doc)['content'])
        for record in records:
            if isinstance(record, (Fact, Requirement)):
                claims = [record.statement, *record.acceptance_criteria] if isinstance(record, Requirement) else [record.title]
                record.source_obligations = [SourceObligation(claim=c, quotes=[quote], subject='Fixture',
                    action='Fixture', modality='DECLARED', conditions=[], quantifier='UNSPECIFIED',
                    trigger='', time_anchor='', exceptions=[]) for c in claims]
            elif isinstance(record, Review):
                target = next(r for r in self.store.records(task['run_id'], 'requirement') if r['id'] == record.subject_id)
                record.claim_reviews = [ClaimReview(claim=c, status='SUPPORTED', source_meaning='Transport fixture',
                    quotes=[quote], issue_codes=[], explanation='Scripted fixture')
                    for c in [target['statement'], *target['acceptance_criteria']]]
                for claim in record.claim_reviews:
                    claim.logic_comparisons = [LogicComparison(action='Fixture', predicate='Fixture',
                        source_polarity='UNSPECIFIED', claim_polarity='UNSPECIFIED',
                        source_quantifier='UNSPECIFIED', claim_quantifier='UNSPECIFIED')]
        return StageOutput(records=records, gaps=[], summary="构造样例阶段完成；未调用真实模型。")


class FixtureMantis:
    """Scripted application transport fixture, using the production skill compiler.

    This does not evaluate analysis accuracy. The actual native driver and
    real ADK calls with the same repository skills are exercised in test_mantis.
    """

    def __init__(self, settings, store):
        from security_auditor.mantis_engine import MantisEngine
        self.engine = MantisEngine(settings, store)
        self.fingerprint = self.engine.fingerprint
        self.store, self.settings = store, settings

    def workspace(self, run_id):
        return self.engine.workspace(run_id)

    async def execute(self, run_id, phase, parent):
        from security_auditor.domain import Fact, Finding, Risk, Threat
        from security_auditor.native_skills import NativeSkillLoader
        tid = self.store.add_task(run_id, "mantis_" + phase, {}, parent, "engine")
        self.store.start_task(tid)
        loader = NativeSkillLoader(self.settings.skills_dir)
        language = self.store.run(run_id)['language']
        stages = ['architect', 'threat_modeler'] if phase == 'model' else ['researcher', 'calibrator']
        for stage in stages:
            bundle, instruction, fingerprint = loader.compile(stage, language)
            assert bundle.instruction in instruction
            self.store.save_model_output(tid, '{"transport_fixture":true,"instruction_hash":"' + fingerprint + '"}')
        refs = [e['id'] for e in self.store.evidence(run_id, 'code')]
        for eid in refs:
            self.store.note_read(tid, eid)
        common = {'module': 'orders', 'evidence_ids': refs, 'rationale': 'Scripted transport fixture, not analysis acceptance.', 'engine': 'mantis'}
        if phase == 'model':
            records = [Fact(title='Order architecture', fact_type='BOUNDARY', basis='OBSERVED', **common),
                Threat(title='Cross-tenant export', attacker='Tenant user', entrypoint='/orders/export',
                    trust_boundary='Tenant data', preconditions=['User can export'], impact='Cross-tenant disclosure', **common)]
        else:
            finding = Finding(title='Cross-tenant export', finding_type='STATIC_VULNERABILITY',
                status='STATIC_SUPPORTED', native_status='static_confirmed',
                impact='Cross-tenant disclosure', attack_preconditions=['User can export'],
                recommendation='Scope the shared query by tenant.', **common)
            risk = Risk(title='Cross-tenant risk', subject_id=finding.id, severity='HIGH',
                evidence_strength='STATIC_SUPPORTED', impact_score=4, likelihood_score=4,
                native_score=6.4, **{k: v for k, v in common.items() if k != 'module'})
            records = [finding, risk]
        self.store.commit_output(tid, 'mantis_engine', StageOutput(records=records, summary='Scripted application transport fixture'))
