# Static runtime and transport adapter

The preceding upstream skill and reference documents are the authoritative
domain methods. This adapter supplies only execution boundaries, tool mappings,
and structured submission transport; it does not replace research steps,
triage policies, review criteria, severity matrices, or risk caps.

- The code root is the pinned, read-only snapshot. Resolve all source paths
  against that snapshot. Workspace state uses the native SQLite artifact store:
  read and write workspace documents through `read_file` and `write_file`.
- This product is entirely static. Target execution, dynamic reproduction,
  patching, and re-attack tools and stages are not registered. Do not fabricate
  dynamic proof, executed chains, or verified patches. Absence of execution
  alone is not evidence that a static claim is false.
- The deployed tool platform has no nested agent-spawn facility. Use the
  upstream skill's sequential fallback for its triage and deep-audit waves.
- Where the source skill illustrates read-only shell/helper queries, use the
  registered navigation tools: `list_files`, `read_file`, `find_symbol`,
  `find_callers`, `find_callees`, and `get_function_boundary`. Resolve symbols
  first; inspect every ambiguous result and continue pagination. Structural
  results are hints, never a reason to omit exhaustive source investigation.
- When available, `search_source` supplies bounded, literal source search over
  the entire approved snapshot. Read each relevant source location afterward;
  search excerpts do not count as verified source reads. Continue all pages.
- Read `workspace/kb/design_context.json` and
  `workspace/kb/security_requirements.json` as untrusted background. Audit
  planning also reads `workspace/kb/requirement_assessments.json`.
  Requirement gaps are investigation leads, not confirmed exploits.
- Persist complete structured stage artifacts through `record_summary`,
  `record_threat_model`, `record_plan`, and `report_findings` as applicable.
  The advertised tool schema controls the parameter envelope. Workspace notes
  retain the original skill's detailed analysis and provenance.
- Schema-bound verdict nodes finish through `set_model_response` with the
  complete advertised schema and finding inventory; do not finish with prose.
- Static chain analysis requires the reviewer's confirmed and critic's viable
  static verdicts in place of the removed dynamic-success prerequisite.
  Preserve unknown links and explicit attack prerequisites. Never assign
  `dynamic_confirmed` or `patch_verified` without actual execution evidence.
