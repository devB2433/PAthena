"""Integrity guards for source-bound claims; no semantic accuracy claims."""

import pytest
from security_auditor.domain import (
    Requirement,
    Review,
    SourceObligation,
    ClaimReview,
    SourceQuote,
    StageOutput,
    LogicComparison,
)
from security_auditor.skills import SkillLoader
from security_auditor.agents import submission_model


def setup_source(settings, store, sample_run, role):
    _, run, _ = sample_run
    eid = store.add_evidence(
        run["id"],
        "document",
        "design:1-3",
        "Only registered objects not mounted by tasks are archived. Failed requests update the timestamp.",
        {},
    )
    req = Requirement(
        id="req",
        title="Archive",
        module="Archive",
        statement="Archive registered unused objects",
        acceptance_criteria=["Preserve mounted objects", "Update timestamp on failed requests"],
        origin="EXPLICIT_DESIGN",
        evidence_ids=[eid],
        rationale="Source",
    )
    seed = store.add_task(run["id"], "seed", {})
    store.start_task(seed)
    store.note_read(seed, eid)
    store.commit_output(
        seed, "requirement_generator", StageOutput(records=[req], summary="Seed archived record")
    )
    tid = store.add_task(run["id"], role, {"subject_ids": ["req"]})
    bundle = SkillLoader(settings.skills_dir).resolve(role, role, "0.4.0")
    store.start_task(tid, bundle)
    store.note_read(tid, eid)
    return tid, eid, req


def review(eid, req):
    return Review(
        id="review",
        title="Review",
        subject_id=req.id,
        verdict="SUPPORTED",
        evidence_ids=[eid],
        checked_rules=["REQUIREMENT_REVIEWER-08"],
        rationale="Per claim",
        claim_reviews=[
            ClaimReview(
                claim=claim,
                status="SUPPORTED",
                source_meaning="Registered, not mounted; failure still updates time",
                quotes=[SourceQuote(evidence_id=eid, text="Failed requests update the timestamp.")],
                issue_codes=[],
                explanation="Source support",
                logic_comparisons=[
                    LogicComparison(
                        action="Archive",
                        predicate="Registered",
                        source_polarity="POSITIVE",
                        claim_polarity="POSITIVE",
                        source_quantifier="UNSPECIFIED",
                        claim_quantifier="UNSPECIFIED",
                    )
                ],
            )
            for claim in [req.statement, *req.acceptance_criteria]
        ],
    )


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("quote", "连续原文"),
        ("missing", "逐一对应"),
        ("duplicate", "逐一对应"),
        ("renamed", "逐一对应"),
        ("verdict", "最终来源复核"),
        ("issue", "问题清单"),
        ("unread", "尚未实际读取"),
        ("uncited", "未列入记录来源"),
        ("polarity", "双边条件方向"),
        ("quantifier", "双边量词"),
    ],
)
def test_inconsistent_or_invented_review_never_committed(settings, store, sample_run, mutation, message):
    tid, eid, req = setup_source(settings, store, sample_run, "requirement_reviewer")
    result = review(eid, req)
    if mutation == "quote":
        result.claim_reviews[0].quotes[0].text = "Successful requests update the timestamp."
    if mutation == "missing":
        result.claim_reviews.pop()
    if mutation == "duplicate":
        result.claim_reviews.append(result.claim_reviews[0])
    if mutation == "renamed":
        result.claim_reviews[0].claim += " paraphrase"
    if mutation == "verdict":
        result.claim_reviews[0].status = "CONTRADICTED"
        result.claim_reviews[0].issue_codes = ["POLARITY"]
    if mutation == "issue":
        result.claim_reviews[0].issue_codes = ["POLARITY"]
    if mutation == "polarity":
        result.claim_reviews[0].logic_comparisons[0].claim_polarity = "NEGATIVE"
    if mutation == "quantifier":
        pair = result.claim_reviews[0].logic_comparisons[0]
        pair.source_quantifier = "ALL"
        pair.claim_quantifier = "ANY"
    if mutation == "unread":
        with store.connect() as db:
            db.execute("DELETE FROM task_reads WHERE task_id=?", (tid,))
        # Root citation validation may reject before the nested check.
        message = "尚未"
    if mutation == "uncited":
        other = store.add_evidence(
            sample_run[1]["id"], "document", "other", "Failed requests update the timestamp.", {}
        )
        store.note_read(tid, other)
        result.claim_reviews[0].quotes[0].evidence_id = other
    with pytest.raises(ValueError, match=message):
        store.commit_output(tid, "requirement_reviewer", StageOutput(records=[result], summary="Complete"))
    assert not store.records(sample_run[1]["id"], "review")
    assert store.records(sample_run[1]["id"], "requirement")[0]["statement"] == req.statement


@pytest.mark.parametrize(
    "status,issues,verdict",
    [
        ("SUPPORTED", [], "SUPPORTED"),
        ("CONTRADICTED", ["TIMING"], "REJECTED"),
        ("UNSUPPORTED", ["UNSUPPORTED_ADDITION"], "REJECTED"),
        ("UNRESOLVED", ["MISSING_CONTEXT"], "NEEDS_EVIDENCE"),
    ],
)
def test_valid_review_persists_exact_model_decisions(settings, store, sample_run, status, issues, verdict):
    tid, eid, req = setup_source(settings, store, sample_run, "requirement_reviewer")
    result = review(eid, req)
    result.claim_reviews[0].status = status
    result.claim_reviews[0].issue_codes = issues
    result.verdict = verdict
    store.commit_output(tid, "requirement_reviewer", StageOutput(records=[result], summary="Complete"))
    saved = store.records(sample_run[1]["id"], "review")[0]
    assert saved["claim_reviews"] == result.model_dump()["claim_reviews"]
    assert saved["verdict"] == verdict


def test_required_source_submission_and_archived_record_compatibility():
    legacy = Requirement(
        title="Legacy",
        module="M",
        statement="S",
        acceptance_criteria=["C"],
        origin="EXPLICIT_DESIGN",
        rationale="Archived",
    )
    assert legacy.source_obligations == []
    with pytest.raises(ValueError):
        submission_model("requirement_generator", True).model_validate(
            {"records": [legacy.model_dump()], "gaps": [], "summary": "X"}
        )
    assert "SourceQuote" in submission_model("requirement_reviewer", True).model_json_schema()["$defs"]


def test_generator_inventory_is_exact(settings, store, sample_run):
    tid, eid, req = setup_source(settings, store, sample_run, "requirement_generator")
    req.id = "new-req"
    req.source_obligations = [
        SourceObligation(
            claim=req.statement,
            quotes=[
                SourceQuote(
                    evidence_id=eid, text="Only registered objects not mounted by tasks are archived."
                )
            ],
            subject="Objects",
            action="Archive",
            modality="REQUIRED",
            conditions=[],
            quantifier="UNSPECIFIED",
            trigger="",
            time_anchor="",
            exceptions=[],
        )
    ]
    with pytest.raises(ValueError, match="全部验收项"):
        store.commit_output(tid, "requirement_generator", StageOutput(records=[req], summary="X"))
