"""Offline PCI DSS pack import using pdfplumber and an independent PDFium check.

This is a version-specific standards adapter, not a general PDF parser. The
original PDF, separate columns, cross-page notes and page references are kept.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


CLAUSE = re.compile(r"^((?:[1-9]|1[0-2]|A[123])\.\d+\.\d+(?:\.\d+)?)(?=\s)")
SECTION = re.compile(r"^((?:[1-9]|1[0-2]|A[123])\.\d+)(?=\s)")
LABELS = {"Defined Approach Requirements": "defined_approach",
          "Customized Approach Objective": "customized_approach_objective",
          "Applicability Notes": "applicability_notes"}
FIELDS = (*LABELS.values(), "testing_procedures", "guidance")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def parse_columns(pages: list[dict]) -> list[dict]:
    """Normalize parser-produced columns without synthesizing standard text."""
    records, current, field, sections, awaiting_clause = {}, None, None, {}, False
    for page in pages:
        # Section headings span the whole table; the narrow left column cuts them.
        for line in page["whole"].splitlines():
            match = SECTION.match(line)
            if match:
                sections[match.group(1)] = line.strip()
        lines = page["left"].splitlines()
        continuation = next((CLAUSE.match(line.strip()) for line in lines
                             if re.fullmatch(r"\S+\s+\(continued\)", line.strip()) and CLAUSE.match(line.strip())), None)
        if continuation and continuation.group(1) != current:
            printed = continuation.group(1)
            if printed not in records or current not in records:
                raise ValueError(f"Unmatched continuation {printed} on page {page['pdf_page']}")
            # Do not silently correct or assign ambiguous guidance. Preserve the
            # whole original page as context linked to both possible owners.
            page["ambiguity"] = {"printed_owner": printed, "adjacent_owner": current}
            for owner in (printed, current):
                records[owner]["context_ids"].append(f"PCI_DSS_CONTEXT_{page['pdf_page']:03d}")
                records[owner]["source_notes"].append(
                    f"PDF page {page['pdf_page']} has a continuation marker for {printed} after the "
                    f"{current} table. Its content is retained as separate context; association is uncertain.")
            continue
        # Capture the normative stream first; each header can introduce a new
        # requirement on pages containing several vertically stacked controls.
        owners = []
        for line in lines:
            line = line.strip()
            if line in LABELS:
                field = LABELS[line]
                awaiting_clause = field == "defined_approach"
                continue
            match = CLAUSE.match(line)
            if match and (awaiting_clause or "(continued)" in line):
                identifier = match.group(1)
                awaiting_clause = False
                if "(continued)" in line:
                    if identifier != current:
                        raise ValueError(f"Unmatched continuation {identifier} on page {page['pdf_page']}")
                    if not owners or owners[-1] != current:
                        owners.append(current)
                    continue
                current = identifier
                if current not in records:
                    records[current] = {"id": current, "parts": {f: [] for f in FIELDS}, "pdf_pages": [],
                                        "context_ids": [], "source_notes": []}
                if not owners or owners[-1] != current:
                    owners.append(current)
            elif SECTION.match(line):
                field = None
                continue
            if not current or not field or not line or line in {"(continued)", "(continued on next page)"}:
                continue
            record = records[current]
            if page["pdf_page"] not in record["pdf_pages"]:
                record["pdf_pages"].append(page["pdf_page"])
            record["parts"][field].append(line)
        if not owners and current:
            owners = [current]
        if not owners:
            raise ValueError(f"Requirement table has no owner on page {page['pdf_page']}")
        chunks = page["chunks"]
        if len(owners) != len(chunks):
            raise ValueError(f"Ambiguous vertical table ownership on page {page['pdf_page']}")
        for identifier, chunk in zip(owners, chunks):
            record = records[identifier]
            if page["pdf_page"] not in record["pdf_pages"]:
                record["pdf_pages"].append(page["pdf_page"])
            for key in ("testing_procedures", "guidance"):
                text = chunk[key].strip()
                if text:
                    record["parts"][key].append(text)
    result = []
    for record in records.values():
        identifier = record["id"]
        fields = {key: "\n".join(value).strip() for key, value in record.pop("parts").items()}
        if not fields["defined_approach"].startswith(identifier + " ") or not fields["testing_procedures"]:
            raise ValueError(f"Incomplete original requirement or testing procedure: {identifier}")
        parent = ".".join(identifier.split(".")[:2])
        result.append({**record, **fields, "parent_id": parent, "parent_context": sections.get(parent, "")})
    return result


def extract_pdf(path: Path) -> tuple[list[dict], list[dict], dict]:
    import pdfplumber
    import pypdfium2

    tables, contexts, bounds, independent_ids = [], [], None, set()
    pdfium = pypdfium2.PdfDocument(str(path))
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) != len(pdfium):
            raise ValueError("PDF engines disagree on page count")
        cover = pdf.pages[0].extract_text() or ""
        if "Version 4.0.1" not in cover or "Data Security Standard" not in cover:
            raise ValueError("Expected PCI DSS v4.0.1 Requirements and Testing Procedures")
        for index, page in enumerate(pdf.pages):
            whole = page.extract_text() or ""
            page_number = index + 1
            req = page.search("Defined Approach Requirements") or []
            tests = page.search("Defined Approach Testing Procedures") or []
            headers = page.search("Requirements and Testing Procedures") or []
            guidance = [g for g in (page.search(r"\bGuidance\b") or [])
                        if g["x0"] > page.width / 2 and any(abs(h["top"] - g["top"]) < 5 for h in headers)]
            if req and tests and guidance:
                left, middle = req[0]["x0"] - 5, tests[0]["x0"] - 5
                dividers = sorted({e["x0"] for e in page.vertical_edges
                                   if e["height"] > 15 and middle + 100 < e["x0"] < guidance[0]["x0"]})
                if not dividers:
                    raise ValueError(f"Missing guidance column separator on page {page_number}")
                bounds = left, middle, dividers[0]
            if not guidance:
                if whole.strip():
                    contexts.append({"id": f"PCI_DSS_CONTEXT_{page_number:03d}", "text": whole,
                                     "pdf_pages": [page_number]})
                page.close()
                continue
            if not bounds:
                raise ValueError(f"Continuation table lacks column boundaries on page {page_number}")
            top = guidance[0]["bottom"] + 3
            footer = [h for h in headers if h["top"] > page.height * .8]
            bottom = footer[0]["top"] - 5 if footer else page.height - 60
            left, middle, right = bounds
            starts = [top] + [r["top"] - 1 for r in req[1:]]
            if req and req[0]["top"] - 1 > top:
                prefix_text = page.crop((left, top, middle, req[0]["top"] - 1)).extract_text() or ""
                if any(CLAUSE.match(line.strip()) and "(continued)" in line for line in prefix_text.splitlines()):
                    starts.insert(1, req[0]["top"] - 1)
            chunks = []
            for start, stop in zip(starts, starts[1:] + [bottom]):
                chunks.append({"testing_procedures": page.crop((middle, start, right, stop)).extract_text() or "",
                               "guidance": page.crop((right, start, page.width - 50, stop)).extract_text() or ""})
            column = page.crop((left, top, middle, bottom)).extract_text() or ""
            tables.append({"pdf_page": page_number, "whole": whole, "left": column, "chunks": chunks})
            check_page = pdfium[index]
            text_page = check_page.get_textpage()
            other_text = text_page.get_text_bounded(left, page.height - bottom, middle, page.height - top)
            for line in other_text.replace("\r", "").splitlines():
                match = CLAUSE.match(line.strip())
                if match:
                    independent_ids.add(match.group(1))
            text_page.close()
            check_page.close()
            page.close()
    page_count = len(pdfium)
    pdfium.close()
    clauses = parse_columns(tables)
    ambiguous = []
    for table in tables:
        if table.get("ambiguity"):
            contexts.append({"id": f"PCI_DSS_CONTEXT_{table['pdf_page']:03d}", "text": table["whole"],
                             "pdf_pages": [table["pdf_page"]], "association": table["ambiguity"]})
            ambiguous.append({"pdf_page": table["pdf_page"], **table["ambiguity"]})
    ids = {c["id"] for c in clauses}
    if ids != independent_ids:
        raise ValueError(f"Independent PDF inventory mismatch: missing={sorted(independent_ids - ids)}, "
                         f"extra={sorted(ids - independent_ids)}")
    chapters = {c.split(".")[0] for c in ids}
    if chapters != {str(i) for i in range(1, 13)} | {"A1", "A2", "A3"}:
        raise ValueError("Main requirements or Appendix A requirements missing")
    return clauses, contexts, {"page_count": page_count, "table_pages": len(tables),
                              "parser": f"pdfplumber {pdfplumber.__version__}",
                              "source_continuity_notes": ambiguous,
                              "independent_inventory": "PDFium column extraction agrees on all requirement IDs"}


def import_pack(pdf_path: Path, destination: Path, provenance_path: Path | None = None) -> dict:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Destination is not empty; preserve the existing standard pack")
    raw = pdf_path.read_bytes()
    source_hash = sha256(raw)
    provenance = json.loads(provenance_path.read_text()) if provenance_path else {}
    if provenance.get("sha256") and provenance["sha256"] != source_hash:
        raise ValueError("Source provenance hash does not match the local PDF")
    clauses, contexts, extraction = extract_pdf(pdf_path)
    source = {"file": pdf_path.name, "sha256": source_hash, "publisher": "PCI Security Standards Council",
              "local_import": str(pdf_path.resolve()), **provenance}
    # Cover, introduction and scope pages precede the detailed controls. The
    # mapper can read them without treating them as additional requirements.
    context_ids = [c["id"] for c in contexts if 5 <= c["pdf_pages"][0] <= 41]
    for clause in clauses:
        clause["source"] = {**source, "pdf_pages": clause["pdf_pages"],
                            "context_ids": context_ids + clause.pop("context_ids")}
        fields = [("Defined Approach Requirement (normative)", clause["defined_approach"]),
                  ("Customized Approach Objective", clause["customized_approach_objective"]),
                  ("Applicability Notes (integral to requirement)", clause["applicability_notes"]),
                  ("Defined Approach Testing Procedures (assessment instructions)", clause["testing_procedures"]),
                  ("Guidance (informative; does not extend requirement)", clause["guidance"])]
        clause["text"] = f"PCI DSS v4.0.1 — {clause['id']}\n{clause['parent_context']}\n\n" + "\n\n".join(
            f"{label}\n{text}" for label, text in fields if text)
        if clause["source_notes"]:
            clause["text"] += "\n\nSource association notes (not normative)\n" + "\n".join(clause["source_notes"])
    for context in contexts:
        context["source"] = {**source, "pdf_pages": context["pdf_pages"]}
    clause_bytes = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in clauses).encode()
    context_bytes = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in contexts).encode()
    manifest = {"id": "PCI_DSS", "version": "4.0.1", "schema_version": 1, "fixture": False,
                "requirement_ids": [c["id"] for c in clauses], "clauses_sha256": sha256(clause_bytes),
                "context_ids": [c["id"] for c in contexts], "contexts_sha256": sha256(context_bytes),
                "source": source, "extraction": extraction}
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf_path, destination / pdf_path.name)
    (destination / "clauses.jsonl").write_bytes(clause_bytes)
    (destination / "contexts.jsonl").write_bytes(context_bytes)
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Import a local PCI DSS v4.0.1 PDF without a model API")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--provenance", type=Path)
    args = parser.parse_args()
    manifest = import_pack(args.pdf, args.destination, args.provenance)
    print(json.dumps({"version": manifest["version"], "requirements": len(manifest["requirement_ids"]),
                      "contexts": len(manifest["context_ids"]), **manifest["extraction"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
