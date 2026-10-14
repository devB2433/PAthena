import pytest
from security_auditor.agents import submission_model
from security_auditor.output_projection import project_empty_extras


def test_empty_decorations_are_removed_without_changing_analytical_values():
    schema = submission_model("design_analyst", True).model_json_schema()
    value = {
        "records": [
            {
                "id": "f",
                "title": "F",
                "rationale": "Reason",
                "evidence_ids": ["S0001"],
                "kind": "fact",
                "module": "M",
                "fact_type": "CONTROL",
                "basis": "DECLARED",
                "source_obligations": [
                    {
                        "claim": "Keep",
                        "quotes": [{"quote_id": "Q00001"}],
                        "subject": "S",
                        "action": "A",
                        "modality": "REQUIRED",
                        "conditions": [{"predicate": "Registered", "polarity": "POSITIVE"}],
                        "quantifier": "ALL",
                        "trigger": "Failed request",
                        "time_anchor": "From invalidation",
                        "exceptions": [],
                        "time_anchor_note": "",
                    }
                ],
            }
        ],
        "gaps": [],
        "summary": "Complete",
        "unused": None,
    }
    projected, receipt = project_empty_extras(value, schema)
    assert "unused" not in projected
    assert (
        projected["records"][0]["source_obligations"][0]["conditions"]
        == value["records"][0]["source_obligations"][0]["conditions"]
    )
    assert projected["records"][0]["source_obligations"][0]["time_anchor"] == "From invalidation"
    assert receipt == [
        {"path": ["records", 0, "source_obligations", 0, "time_anchor_note"], "value": ""},
        {"path": ["unused"], "value": None},
    ]
    assert "time_anchor_note" in value["records"][0]["source_obligations"][0]
    assert submission_model("design_analyst", True).model_validate(projected)


@pytest.mark.parametrize("extra", ["meaningful unregistered claim", 0, False, ["claim"], {"claim": "value"}])
def test_nonempty_unknown_values_and_missing_fields_still_fail(extra):
    schema = submission_model("design_analyst", True).model_json_schema()
    projected, _ = project_empty_extras({"records": [], "gaps": [], "summary": "S", "extra": extra}, schema)
    with pytest.raises(ValueError):
        submission_model("design_analyst", True).model_validate(projected)
    projected, _ = project_empty_extras({"records": [], "gaps": []}, schema)
    with pytest.raises(ValueError):
        submission_model("design_analyst", True).model_validate(projected)


def test_numeric_claim_binding_preserves_normative_text_and_checks_inventory():
    from security_auditor.output_projection import resolve_claim_indices
    from security_auditor.provider import OutputContractError

    target = {"statement": "Original statement.", "acceptance_criteria": ["Criterion A.", "Criterion B."]}
    raw = {
        "records": [
            {
                "subject_id": "req",
                "claim_reviews": [
                    {"claim_index": i, "status": "SUPPORTED", "explanation": "Model reasoning"}
                    for i in [2, 0, 1]
                ],
            }
        ]
    }
    out = resolve_claim_indices(raw, "requirement_reviewer", {"req": target})
    assert [x["claim"] for x in out["records"][0]["claim_reviews"]] == [
        "Criterion B.",
        "Original statement.",
        "Criterion A.",
    ]
    assert [x["status"] for x in out["records"][0]["claim_reviews"]] == ["SUPPORTED"] * 3
    assert "claim" not in raw["records"][0]["claim_reviews"][0]
    for indices in [[0, 1], [0, 1, 1], [0, 1, 3], [False, 1, 2]]:
        wrong = {"records": [{"subject_id": "req", "claim_reviews": [{"claim_index": i} for i in indices]}]}
        with pytest.raises(OutputContractError):
            resolve_claim_indices(wrong, "requirement_reviewer", {"req": target})
    raw["records"][0]["claim_reviews"][0]["claim"] = "Altered criterion"
    with pytest.raises(OutputContractError):
        resolve_claim_indices(raw, "requirement_reviewer", {"req": target})


def test_paired_criteria_are_bound_without_repeating_the_normative_text():
    from security_auditor.output_projection import resolve_claim_indices

    source = {
        "quotes": [{"quote_id": "Q00001"}],
        "subject": "API",
        "action": "Authenticate",
        "modality": "REQUIRED",
        "conditions": [],
        "quantifier": "UNSPECIFIED",
        "trigger": "",
        "time_anchor": "",
        "exceptions": [],
    }
    raw = {
        "records": [
            {
                "id": "req",
                "title": "Auth",
                "module": "API",
                "rationale": "Source",
                "origin": "EXPLICIT_DESIGN",
                "statement": "Authenticate",
                "statement_source": source,
                "acceptance_criteria": [{"criterion": "Reject missing credentials", "source": source}],
            }
        ],
        "gaps": [],
        "summary": "Complete",
    }
    assert submission_model("requirement_generator", True).model_validate(raw)
    out = resolve_claim_indices(raw, "requirement_generator", {})
    assert out["records"][0]["acceptance_criteria"] == ["Reject missing credentials"]
    assert [x["claim"] for x in out["records"][0]["source_obligations"]] == [
        "Authenticate",
        "Reject missing credentials",
    ]
    assert "statement_source" in raw["records"][0]
    assert out["records"][0]["origin"] == raw["records"][0]["origin"]


def test_fixed_review_slots_cover_every_criterion_without_model_echo():
    from security_auditor.agents import scoped_review_submission_model
    from security_auditor.output_projection import resolve_claim_indices
    from security_auditor.provider import OutputContractError

    target = {"statement": "S", "acceptance_criteria": ["A", "B"]}
    entry = {
        "status": "SUPPORTED",
        "source_meaning": "Original",
        "quotes": [{"quote_id": "Q00001"}],
        "issue_codes": [],
        "explanation": "Model reason",
        "logic_comparisons": [
            {
                "action": "Act",
                "predicate": "P",
                "source_polarity": "POSITIVE",
                "claim_polarity": "POSITIVE",
                "source_quantifier": "ALL",
                "claim_quantifier": "ALL",
            }
        ],
    }
    raw = {
        "records": [
            {
                "id": "review",
                "kind": "review",
                "title": "Review",
                "subject_id": "req",
                "verdict": "SUPPORTED",
                "rationale": "Model summary",
                "checked_rules": ["METHOD"],
                "claim_reviews": {"statement": entry, "criterion_1": entry, "criterion_2": entry},
            }
        ],
        "gaps": [],
        "summary": "Complete",
    }
    assert scoped_review_submission_model("req", 2).model_validate(raw)
    result = resolve_claim_indices(raw, "requirement_reviewer", {"req": target})
    assert [x["claim"] for x in result["records"][0]["claim_reviews"]] == ["S", "A", "B"]
    assert [x["explanation"] for x in result["records"][0]["claim_reviews"]] == ["Model reason"] * 3
    del raw["records"][0]["claim_reviews"]["criterion_2"]
    with pytest.raises(ValueError):
        scoped_review_submission_model("req", 2).model_validate(raw)
    with pytest.raises(OutputContractError):
        resolve_claim_indices(raw, "requirement_reviewer", {"req": target})
