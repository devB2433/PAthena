# Skill corpus and execution contract

Production and automated evaluations load the same checked-in corpus. There is
no shortened evaluation-only skill set and no fallback to a user-local skill.

## Application methods

The five active application roles are `design_analyst`, `requirement_generator`,
`pci_mapper`, `requirement_reviewer`, and `requirement_checker`. All six application methods, including the historical compatibility role,
are at 0.6.0.
The skills define investigation steps, decision rubrics, inventory obligations,
source requirements, and completion conditions. Each role includes a casebook
of investigation trajectories, counterexamples, and decision boundaries.
The manifests pin both the main instructions and every reference document.
References are included in full, not merely linked or summarized. `SkillLoader` and
`compile_instruction` supply both production and test instructions.

`pci_requirement_generator` is retained only for the historical structured-standard
project interface. Its submission is Requirement, not StandardControl. The current
project workflow binds controls already prepared in the catalog; it does not
regenerate them during matching. This compatibility role does not generate or
vectorize a new standard catalog.

## Code methods

`code/` contains 21 verbatim upstream skills and five reference documents.
`code/SOURCE.json` records the source commit, file paths, hashes, and active
stage mapping. The 13 registered stages include the deterministic structural
index; the remaining stages use the original analysis methods. Setup and dynamic
skills are retained for source completeness, but are not enabled in the static
analysis graph. The original source and licenses are also retained in the
vendored engine and `THIRD_PARTY_NOTICES`.

`NativeSkillLoader` validates source identity and file digests, includes full
stage references, and adds `code/static-adapter.md` and output-language rules.
Missing or changed original skills stop execution. The adapter documents the
static boundary, sequential investigation fallback, read-only tool mappings,
and structured submission protocol. It does not replace the upstream research,
review, or calibration methods. Original shell-helper examples are carried out
with registered navigation tools and literal source search; no arbitrary shell
or target execution is granted.

## Verification and changes

`tools/sync_code_skills.py` copies the pinned original corpus without rewriting
it. `tools/check_skill_corpus.py` validates all manifests and prints a corpus
receipt. Tests check original file parity, actual ADK request instructions,
task skill/version/instruction receipts, both languages, tool availability,
static execution boundaries, and removal of the seven obsolete summary roles.
Application source search paginates through all registered literal matches;
search hits do not count as source reads. Design analysis and requirement
generation cannot complete while designated design material remains unread.
Scripted model responses test integration and contracts; they do not establish
the accuracy of model-generated security conclusions.

Engine and run fingerprints include the corpus and adapter. Changes require a
new analysis run; existing reports are historical results and are not silently
relabeled as produced by the new skills.

## Source fidelity contract, v0.4

Facts carry exact source quotes and explicit conditions, polarity, quantifiers,
events, timer anchors and exceptions. Each generated requirement has one
source-obligation entry for its statement and each acceptance criterion. Each
review stores an independent source judgment for those exact claims, including
issue codes and a two-sided comparison of source and claim logic.

Persistence checks validate original quote bytes, actual task reads, exact claim
inventory, declared logic mismatches and verdict consistency. They do not prove
semantic entailment. A model can still misparse both sides; a passing contract is
not an accuracy certificate. Failed outputs stay failed, with raw provider data;
no additional model inference repairs them. Old records remain readable with
empty optional fields, and older skill snapshots retain their original protocol.
New fields use existing JSON record storage without rewriting historical rows.

The application output allowance is now 32768 tokens, after the v4 pilot hit
16384. This is response capacity, not a restored cumulative token/call budget.

The model selects task-local paragraph IDs and numbered normative claims.
Their original text and source IDs are resolved by fixed lookup, including the
record's source-provenance closure. The original provider arguments and lookup
catalogs remain archived. Undeclared empty decorations may be omitted with a
receipt; exact target-owned origin/clause annotation echoes are checked and
omitted. Nonempty unknown analytical values, conflicting echoes, missing
claims or contradictory supported verdicts still fail. These are declared
transport projections, not extra model inference or semantic result correction.

Generation's submission pairs each criterion and source constraint in one
object, plus a separate statement source. Review freezes named statement and
criterion slots per task. Storage retains the common Requirement and Review
models with numbered original claims. The final 2026-10-14 requirements workflow
completed, but semantic acceptance did not: both versions judged only 5/8
repair cases correctly. Complete reference binding is not entailment proof.

## Requirement quality investigation, v0.5.1

Every application method includes a mandatory, hash-pinned
`references/quality-method.md`, loaded in full in production and evaluations.
The methods distinguish security-relevant obligations from ordinary functional
statements; infer minimal controls from actual ownership, controllable inputs,
trust changes and failure paths; challenge overconstraint using legitimate
operations; and reconcile real flows with controls and explicit gaps.

Design analysis hands off sensitive-flow facts and unknowns. Generation records
flow-to-requirement coverage in the existing summary and checks necessity,
minimal sufficiency, provenance and legal positive/negative cases. Review checks
those arguments independently, distinguishes valid inference from unsupported
addition, and records omissions without rejecting unrelated valid controls.
Mapping checks obligation intersections and query coverage against frozen
controls; implementation checking challenges concrete source paths and shared
controls; the legacy generator reconciles normative obligations with subsystem
responsibility. Native original skills and their adapter remain unchanged.

The source protocol is explicitly compatible with 0.4.0, 0.5.0 and 0.5.1 for
the three document roles. Changing a method version cannot silently disable
source binding, or enable that protocol on mapper/checker roles. Older snapshots
retain their original versions and output shape.

The synthetic development benchmark and rubric are checked in under
`tests/fixtures/requirements-quality-v5/`; raw responses and local reports are
excluded from Git. This is not blind validation. Coverage, unreasonable controls,
provenance errors, review mistakes and output failures are measured separately.
The mapper, checker and legacy generator method revisions are not semantically
validated by a requirements-only experiment.

## OWASP risk-to-requirement methods, v0.6.0

`_shared/security-requirements.md` is the canonical professional reference for
all six application methods, including the legacy role. It contains 18 conditional
patterns, concrete constraints, adversarial acceptance cases, legitimate exceptions
and exclusion boundaries. The risk organization uses OWASP Top 10:2025; ASVS 5.0.0
informs verification granularity, with official Cheat Sheet sources documented in
the resource. Pattern identifiers are project-local and are not OWASP clause IDs.

Each manifest pins `shared_references`; the loader includes the complete shared
resource and incorporates it in the packet digest. Missing/modified content stops
the stage. Older snapshots without shared references preserve their exact original
packets. No runtime web fetch or alternate evaluation-only resource is permitted.
The shared research document is marked CC BY-SA 4.0 with OWASP attribution; code
and native skill licenses remain separate.

Coverage is assessed per real operation, applicable security invariant and boundary.
Cancellation authorization is distinct from cancellation/publish ordering. Browser
text safety is distinct from nonexistent SQL/file sinks. Logs are checked separately
for audit usefulness, private data and bearer credentials. Design premises remain
the required source for inferred requirements; method references cannot substitute
for evidence or regenerate immutable compliance controls.

The v6 known-design experiment has predeclared coverage and concrete-control rubrics
under `tests/fixtures/requirements-owasp-v6/`. Its semantics and output failures are
reported separately from integration tests. Original native skills are unchanged.


## Requirement-check selection

`pci_mapper` 0.6.1 supports both the existing design-requirement matching protocol and `selected_controls_v1`: every enumerated fixed control receives a source-backed relevance/applicability decision, including controls absent from the design. Selection restricts only implementation tasks. Native modeling/audit skills and code scope remain unchanged; independent Findings never require a selected requirement link.

## Chinese runtime assets

Chinese runs load all 13 registered stage skills and their active references from
`code/zh-CN/`, plus the complete localized static adapter (15 resources).
`code/LOCALIZATION.zh-CN.json` pins each original digest, localized digest, source
line count, localized line count, and Markdown block count. English originals
remain byte-identical to `code/SOURCE.json`. The localization preserves the full
method; paragraph wrapping may differ. Code fences and protected technical values
are retained. This inventory is an integrity check, not proof of semantic equivalence.

`tools/localize_native_skills.py` is an offline asset-build utility, not part of
analysis execution. It first verifies inputs against the anonymously public pinned
repository revision, then localizes complete paragraphs with their protected-value
context and rejects lost/repeated protected values. Production loads checked-in
assets only and never translates prompts or results through an extra model call.
Harness-owned supplemental instructions have Chinese request adapters in
`native_localization.py`; untrusted fenced materials remain unchanged.

## Chinese output editor

`chinese_output_editor/` contains the complete pinned Humanizer-zh skill, MIT
license and source manifest. This is a prose postprocessor, separate from the
five analytical roles. It edits only permitted Chinese explanatory fields before
business persistence. It cannot rewrite requirements, criteria, source evidence,
verdicts, scores or native artifacts. Original drafts, raw returns, batch receipts
and final edits are durable; failed editing resumes without repeating analysis.
