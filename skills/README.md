# Skill corpus and execution contract

Production and automated evaluations load the same checked-in corpus. There is
no shortened evaluation-only skill set and no fallback to a user-local skill.

## Application methods

The five active application roles are `design_analyst`, `requirement_generator`,
`pci_mapper`, `requirement_reviewer`, and `requirement_checker`. Document analysis,
generation and review are at 0.4.0; mapping and implementation checks remain at 0.3.0.
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
