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


class Fact(RecordBase):
    kind: Literal["fact"] = "fact"
    module: str
    fact_type: Literal["MODULE", "ASSET", "DATA_FLOW", "CONTROL", "BOUNDARY", "ENTRYPOINT"]
    basis: Literal["DECLARED", "OBSERVED", "INFERRED"]


class Requirement(RecordBase):
    kind: Literal["requirement"] = "requirement"
    module: str
    statement: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    origin: Literal["EXPLICIT_DESIGN", "INFERRED_SECURITY", "PCI_DSS"]
    clause_ids: list[str] = Field(default_factory=list)

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
    relevance: Literal["RELEVANT", "POTENTIALLY_RELEVANT", "UNRELATED", "UNKNOWN"] = "UNKNOWN"
    applicability_conditions: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)


def compliance_candidate(record: dict) -> bool:
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
        "STATIC_SUPPORTED", "PARTIAL", "VIOLATED", "UNKNOWN", "EXTERNAL_EVIDENCE_REQUIRED"
    ]
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
    "code_architect": {"fact"},
    "threat_modeler": {"threat"},
    "requirement_checker": {"assessment", "finding"},
    "vulnerability_planner": {"fact"},
    "vulnerability_researcher": {"finding"},
    "finding_reviewer": {"review"},
    "finding_critic": {"review"},
    "risk_calibrator": {"risk"},
}

ALLOWED_SOURCES = {
    "design_analyst": {"document"},
    "requirement_generator": {"document"},
    "pci_mapper": {"document", "standard"},
    "pci_requirement_generator": {"document", "standard"},
    "requirement_reviewer": {"document", "standard"},
    "code_architect": {"code"},
    "threat_modeler": {"document", "code", "standard"},
    "requirement_checker": {"document", "code", "standard"},
    "vulnerability_planner": {"document", "code", "standard"},
    "vulnerability_researcher": {"document", "code"},
    "finding_reviewer": {"document", "code", "standard"},
    "finding_critic": {"document", "code", "standard"},
    "risk_calibrator": {"document", "code", "standard"},
}
