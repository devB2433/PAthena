import json

import pytest

from security_auditor.ingestion import ingest_run, standard_manifest
from security_auditor.runtime import Scheduler
from security_auditor.skills import digest
from security_auditor.standards import parse_columns


def table(page, left, chunks=None):
    return {"pdf_page": page, "left": left, "whole": "1.1 Controls are established.\n" + left,
            "chunks": chunks or [{"testing_procedures": "1.1.1 Examine the implementation.",
                                   "guidance": "Purpose\nGuidance is informative."}]}


def test_columns_preserve_normative_notes_and_references_without_creating_requirements():
    rows = parse_columns([
        table(44, "Defined Approach Requirements\n1.1.1 Protect records.\n"
              "3.5.1 and 3.5.1.2 are cross-references within the requirement.\n"
              "Customized Approach Objective\nRecords remain protected.\n"
              "Applicability Notes\nApplies only to service providers."),
        table(45, "Applicability Notes\n(continued)\nIncludes shared responsibilities.",
              [{"testing_procedures": "", "guidance": "Example\nUse a shared control."}]),
    ])
    assert [r["id"] for r in rows] == ["1.1.1"]
    row = rows[0]
    assert "3.5.1.2" in row["defined_approach"]
    assert "shared responsibilities" in row["applicability_notes"]
    assert "Guidance is informative" not in row["defined_approach"]
    assert row["pdf_pages"] == [44, 45]
    assert row["parent_context"] == "1.1 Controls are established."


def test_continuation_and_new_requirement_on_same_page_have_separate_owners():
    rows = parse_columns([
        table(44, "Defined Approach Requirements\n1.1.1 First control.\nCustomized Approach Objective\nFirst goal."),
        table(45, "1.1.1 (continued)\nDefined Approach Requirements\n1.1.2 Second control.\n"
              "Customized Approach Objective\nSecond goal.",
              [{"testing_procedures": "", "guidance": "First continued guidance."},
               {"testing_procedures": "1.1.2 Examine second control.", "guidance": "Second guidance."}]),
    ])
    assert len(rows) == 2
    assert "First continued guidance" in rows[0]["guidance"]
    assert "Second guidance" not in rows[0]["guidance"]
    assert rows[1]["testing_procedures"] == "1.1.2 Examine second control."


def test_ambiguous_source_continuation_is_preserved_without_silently_reassigning_it():
    pages = [table(44, "Defined Approach Requirements\n1.1.1 First control."),
             table(45, "Defined Approach Requirements\n1.1.2 Second control."),
             table(46, "1.1.1 (continued)", [{"testing_procedures": "", "guidance": "Ambiguous guidance."}])]
    rows = parse_columns(pages)
    assert pages[-1]["ambiguity"] == {"printed_owner": "1.1.1", "adjacent_owner": "1.1.2"}
    assert all(r["context_ids"] == ["PCI_DSS_CONTEXT_046"] for r in rows)
    assert all(r["source_notes"] for r in rows)
    assert all("Ambiguous guidance" not in r["guidance"] for r in rows)


def sample_pack(tmp_path):
    """Contract-only temporary files; never installed as a product standard."""
    pack = tmp_path / "contract-pack"
    pack.mkdir()
    pdf = b"contract source bytes"
    (pack / "source.pdf").write_bytes(pdf)
    source = {"file": "source.pdf", "sha256": digest(pdf), "pdf_pages": [44], "context_ids": ["scope"]}
    clauses = [{"id": "1.1.1", "text": "Original requirement.", "source": source}]
    contexts = [{"id": "scope", "text": "Original applicability and scope.", "source": source}]
    raw = (json.dumps(clauses[0]) + "\n").encode()
    context_raw = (json.dumps(contexts[0]) + "\n").encode()
    (pack / "clauses.jsonl").write_bytes(raw)
    (pack / "contexts.jsonl").write_bytes(context_raw)
    manifest = {"id": "PCI_DSS", "version": "4.0.1", "fixture": False, "source": source,
                "requirement_ids": ["1.1.1"], "clauses_sha256": digest(raw),
                "context_ids": ["scope"], "contexts_sha256": digest(context_raw)}
    (pack / "manifest.json").write_text(json.dumps(manifest))
    return pack


@pytest.mark.parametrize("file", ["source.pdf", "clauses.jsonl", "contexts.jsonl"])
def test_standard_snapshot_checks_source_requirements_and_context_integrity(tmp_path, file):
    pack = sample_pack(tmp_path)
    before = standard_manifest(pack)
    assert before["requirement_ids"] == ["1.1.1"]
    assert before["context_ids"] == ["scope"]
    (pack / file).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="摘要"):
        standard_manifest(pack)


def test_context_is_readable_but_is_not_dispatched_as_a_requirement(settings, store, tmp_path):
    pack = sample_pack(tmp_path)
    doc = tmp_path / "design.md"
    doc.write_text("A service processes cardholder records.")
    project = store.create_project("contract-only")
    run = store.create_run(project["id"], "requirements_only",
                           {"assets": [{"path": str(doc), "name": doc.name, "id": "document",
                                        "sha256": digest(doc.read_bytes())}], "standard": standard_manifest(pack)},
                           False, 100, 200000)
    ingest_run(store, run["id"], settings)
    evidence = store.evidence(run["id"], "standard")
    assert len(evidence) == 2
    scopes = Scheduler(settings, store).scopes(run["id"], "pci_mapper")
    assert len(scopes) == 1
    assert scopes[0]["clause_ids"] == ["1.1.1"]
    assert len(scopes[0]["context_evidence_ids"]) == 1
    context = store.read_evidence(run["id"], scopes[0]["context_evidence_ids"][0])
    assert context["content"] == "Original applicability and scope."
