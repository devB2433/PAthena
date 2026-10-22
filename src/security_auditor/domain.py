from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def new_id() -> str:
    return str(uuid.uuid4())


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecordBase(StrictModel):
    id: str = Field(default_factory=new_id, min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=10000)
    engine: Literal["mantis"] | None = None
    native_id: str | None = None
    native_status: str | None = None
    native_data: dict | None = None


class SourceQuote(StrictModel):
    evidence_id: str = ''
    text: str = Field(default='', min_length=1, max_length=5000, description="根据本任务选择的引文编号取得的原始文本")
    quote_id: str | None = None


class SourceCondition(StrictModel):
    predicate: str = Field(min_length=1)
    polarity: Literal['POSITIVE', 'NEGATIVE']


class SourceObligation(StrictModel):
    claim: str = Field(default='', min_length=1, description="根据 claim_index 取得的准确规范性原文；事实记录中为事实命题")
    claim_index: int | None = Field(default=None, ge=0)
    quotes: list[SourceQuote] = Field(min_length=1)
    subject: str
    action: str
    modality: Literal['REQUIRED', 'FORBIDDEN', 'OPTIONAL', 'DECLARED', 'INFERRED']
    conditions: list[SourceCondition]
    quantifier: Literal['ALL', 'ANY', 'UNSPECIFIED']
    trigger: str = Field(description="触发动作的事件；仅在原文未规定时留空")
    time_anchor: str = Field(description="计时起点、经过时长及重置事件；不适用时留空")
    exceptions: list[str]


class LogicComparison(StrictModel):
    action: str = Field(min_length=1, description="两侧比较可执行条件时所指的同一个动作")
    predicate: str = Field(min_length=1, description="分别从原文和主张独立解析出的同一个肯定式谓词")
    source_polarity: Literal['POSITIVE', 'NEGATIVE', 'UNSPECIFIED']
    claim_polarity: Literal['POSITIVE', 'NEGATIVE', 'UNSPECIFIED']
    source_quantifier: Literal['ALL', 'ANY', 'UNSPECIFIED']
    claim_quantifier: Literal['ALL', 'ANY', 'UNSPECIFIED']


class ClaimReview(StrictModel):
    claim: str = Field(default='', min_length=1, description="根据提交的 claim_index 取得的准确目标文本")
    claim_index: int | None = Field(default=None, ge=0)
    status: Literal['SUPPORTED', 'CONTRADICTED', 'UNSUPPORTED', 'UNRESOLVED']
    source_meaning: str = Field(min_length=1, description="先独立还原原文含义，再与主张比较")
    quotes: list[SourceQuote] = Field(min_length=1)
    issue_codes: list[Literal['POLARITY', 'QUANTIFIER', 'TIMING', 'EXCEPTION', 'STRENGTH',
                             'UNSUPPORTED_ADDITION', 'INTERNAL_CONTRADICTION', 'CLASSIFICATION',
                             'SCOPE', 'UNTESTABLE', 'MISSING_CONTEXT']]
    explanation: str = Field(min_length=1)
    logic_comparisons: list[LogicComparison] = Field(default_factory=list)


class Fact(RecordBase):
    kind: Literal["fact"] = "fact"
    module: str
    fact_type: Literal["MODULE", "ASSET", "DATA_FLOW", "CONTROL", "BOUNDARY", "ENTRYPOINT"]
    basis: Literal["DECLARED", "OBSERVED", "INFERRED"]
    source_obligations: list[SourceObligation] = Field(default_factory=list)


class Requirement(RecordBase):
    kind: Literal["requirement"] = "requirement"
    module: str
    statement: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    origin: Literal["EXPLICIT_DESIGN", "INFERRED_SECURITY", "PCI_DSS"]
    clause_ids: list[str] = Field(default_factory=list)
    standard_control_id: str | None = None
    standard_catalog_id: str | None = None
    verification_method: Literal['CODE', 'DEPLOYMENT', 'NON_CODE'] | None = None
    matched_requirement_ids: list[str] = Field(default_factory=list)
    applicability_conditions: list[str] = Field(default_factory=list)
    source_obligations: list[SourceObligation] = Field(default_factory=list)

    @field_validator("acceptance_criteria")
    @classmethod
    def distinct_criteria(cls, value):
        if any(not c.strip() for c in value) or len(value) != len(set(value)):
            raise ValueError("需求验收项不能为空或重复")
        return value


class Applicability(RecordBase):
    kind: Literal["applicability"] = "applicability"
    clause_id: str
    status: Literal["APPLICABLE", "NOT_APPLICABLE", "UNDETERMINED"]
    requirement_ids: list[str] = Field(default_factory=list)
    control_ids: list[str] = Field(default_factory=list)
    relevance: Literal["RELEVANT", "POTENTIALLY_RELEVANT", "UNRELATED", "UNKNOWN"] = "UNKNOWN"
    control_scope: Literal["CODE_RELATED", "NON_CODE", "UNKNOWN"] | None = Field(
        default=None, description="与被评估代码子系统的关系，独立于正式 PCI 范围。"
        "相关的部署控制归为 CODE_RELATED；无关的组织控制归为 NON_CODE。")
    applicability_conditions: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)


def compliance_candidate(record: dict) -> bool:
    # None supports archived v1 decisions only. An unknown scope stays in the
    # mapping inventory and does not create requirements or code-check tasks.
    if record.get('control_scope') in {'NON_CODE', 'UNKNOWN'} or record.get('status') == 'NOT_APPLICABLE':
        return False
    relevance = record.get('relevance', 'UNKNOWN')
    return relevance in {'RELEVANT', 'POTENTIALLY_RELEVANT'} or (
        relevance == 'UNKNOWN' and record.get('status') == 'APPLICABLE')


class Threat(RecordBase):
    kind: Literal["threat"] = "threat"
    module: str
    attacker: str
    entrypoint: str
    trust_boundary: str
    preconditions: list[str]
    impact: str


class Assessment(RecordBase):
    kind: Literal["assessment"] = "assessment"
    requirement_id: str
    acceptance_criterion: str
    module: str
    entrypoint: str
    design_status: Literal["SUPPORTED", "PARTIAL", "MISSING", "UNKNOWN"]
    implementation_status: Literal[
        "STATIC_SUPPORTED", "PARTIAL", "VIOLATED", "UNKNOWN", "NOT_CODE_VERIFIABLE", "EXTERNAL_EVIDENCE_REQUIRED"
    ] = Field(description="UNKNOWN 表示静态调查不足。NOT_CODE_VERIFIABLE 表示实际存在的"
              "需求依赖部署、运维或其他无法从代码取得的事实。"
              "EXTERNAL_EVIDENCE_REQUIRED 仅为历史归档值，禁止用于新提交。")
    counter_evidence_ids: list[str] = Field(default_factory=list)


class Finding(RecordBase):
    kind: Literal["finding"] = "finding"
    module: str
    finding_type: Literal["DESIGN_GAP", "IMPLEMENTATION_GAP", "STATIC_VULNERABILITY"]
    status: Literal["CANDIDATE", "STATIC_SUPPORTED", "NEEDS_EVIDENCE", "FALSE_POSITIVE"] = "CANDIDATE"
    requirement_ids: list[str] = Field(default_factory=list)
    attack_preconditions: list[str] = Field(default_factory=list)
    impact: str
    recommendation: str


class Review(RecordBase):
    kind: Literal["review"] = "review"
    subject_id: str
    verdict: Literal["SUPPORTED", "NEEDS_EVIDENCE", "REJECTED"]
    checked_rules: list[str] = Field(min_length=1)
    counter_evidence_ids: list[str] = Field(default_factory=list)
    claim_reviews: list[ClaimReview] = Field(default_factory=list)


class Risk(RecordBase):
    kind: Literal["risk"] = "risk"
    subject_id: str
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    evidence_strength: Literal["STATIC_SUPPORTED", "PARTIAL", "UNKNOWN"]
    impact_score: int = Field(ge=1, le=5)
    likelihood_score: int = Field(ge=1, le=5)
    native_score: float | None = Field(default=None, ge=0, le=100)


Record = Annotated[
    Fact | Requirement | Applicability | Threat | Assessment | Finding | Review | Risk,
    Field(discriminator="kind"),
]


class StageOutput(StrictModel):
    records: list[Record] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)


ALLOWED_KINDS = {
    "mantis_engine": {"fact", "threat", "finding", "risk"},
    "design_analyst": {"fact"},
    "requirement_generator": {"requirement"},
    "pci_mapper": {"applicability"},
    "pci_requirement_generator": {"requirement"},
    "requirement_reviewer": {"review"},
    "requirement_checker": {"assessment", "finding"},
}

ALLOWED_SOURCES = {
    "design_analyst": {"document"},
    "requirement_generator": {"document"},
    "pci_mapper": {"document", "standard", "code"},
    "pci_requirement_generator": {"document", "standard"},
    "requirement_reviewer": {"document", "standard"},
    "requirement_checker": {"document", "code", "standard"},
}
