# Skill corpus and execution contract

Production and automated evaluations load the same checked-in corpus. There is
no shortened evaluation-only skill set and no fallback to a user-local skill.

## Application methods

The five active application roles are `design_analyst`, `requirement_generator`,
`pci_mapper`, `requirement_reviewer`, and `requirement_checker`. Their 0.3.0
skills define investigation steps, decision rubrics, inventory obligations,
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
