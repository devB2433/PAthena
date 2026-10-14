# Application-method revision: 0.2.0 to 0.3.0

The 0.2.0 checks established loading consistency and protocol behavior. They did
not demonstrate that the project-defined methods matched the depth of the
upstream code-audit methods. This revision addresses that separate problem.

## Comparison basis

The pinned architecture, threat-model, planner, researcher, and reviewer methods
in `code/` provide concrete input/output contracts, branching investigation
steps, scope and freshness handling, exhaustive discovery, explicit negative
constraints, decision thresholds, and completion conditions. Their original
text is unchanged. Application roles need equivalent operational detail adapted
to documents, requirements, fixed controls, and per-criterion checks; code-audit
shell, workspace, and dynamic instructions are not copied into these roles.

| Role | Gap in 0.2.0 | Concrete 0.3.0 method |
| --- | --- | --- |
| Design analyst | General extraction instructions lacked multi-pass relationship investigation | Material inventory, per-flow identity/object/state tracing, statement vs inference, conflict/exception pass, fact field mapping, five worked cases |
| Requirement generator | General categories lacked a test for useful security behavior | Explicit/inferred inventories, security invariant derivation, threat-to-control matrix, weakest-satisfying-implementation challenge, path expansion, atomic criteria, six cases |
| PCI mapper | Broad matching rules lacked a candidate investigation and challenge loop | Behavior/synonym queries, control/parent reading, three independent decision axes, mixed-responsibility selection, exclusions challenged with counterevidence, six cases |
| Requirement reviewer | Short rubric lacked a reproducible adversarial review method | Independent support reconstruction, thirteen separately identified challenge checks, weakest-implementation and legal-use checks, duplicate/conflict investigation, decision precedence, seven cases |
| Requirement checker | Broad static rules lacked proof-chain and exhaustion guidance | Criterion invariants, ingress/control/sink paths, lexical/index union, shared-control and bypass searches, continue/stop gates, five verdict thresholds, seven cases |
| Compatibility PCI generator | Mixed standard preparation and project submission contracts | Explicit legacy-only entry point, normative sentence decomposition, responsibility split, conditional requirements, source recheck and inventory settlement, five cases |

## Runtime changes required by these methods

- Every role's reference documents are pinned in its manifest and included in
  the compiled production instructions. Missing/tampered references fail
  closed. The packet hash changes if the casebook changes.
- Application `search_evidence` reports total, has_more and next_offset instead
  of silently limiting investigation to the first 50 hits. Hits remain unread
  until the original source is actually read.
- Design and requirement-generation completion gates check designated design
  reads on both the final tool and final-JSON paths. Incomplete output is
  retained as failed, without another model turn to repair it.
- Application tasks record the first actual ADK system-instruction hash,
  including runtime transport additions, as well as the full skill packet hash.

## What validation establishes

Tests execute the actual loaders, ADK runner, source-search pagination and
material-completion gates. They verify casebook inclusion and source integrity.
Scripted model responses establish contract behavior, not semantic accuracy.
These methods have not yet received an independent real-model accuracy study;
length or number of checks is not evidence of equal accuracy to the original
code-audit system. Existing reports remain tied to their original skill versions.

## Real-model pilot, 2026-10-14

The pinned 505-line KEP-2799 document was tested with the same production
executor, deepseek-flash, Chinese output, temperature 0 and disabled thinking.
The reference behaviors were fixed before responses. Two paired rounds fully
retained 12 / 9 of 13 behaviors with 0.2.0 and 12 / 12 with 0.3.0. Mean tokens
for design plus generation rose 16.46%. With identical fixed design facts,
full behavior coverage was 12 / 13 versus 13 / 13; those facts deliberately
contain the expected behaviors and do not test document extraction.

Both reviewers got six fixed source-backed challenge cases right. Reviewing
every first-round generated draft was a separate exploratory extension:
the old reviewer accepted two requirements with reversed association conditions;
the new reviewer accepted an additional recovery policy unsupported by the
document. Each version also had one round with the association condition
reversed in an upstream Fact. Longer instructions and rationales did not
eliminate semantic errors or ensure effective independent review.

The development agent authored the gold criteria and adjudicated outputs. This
was not blind, independent human-expert acceptance. All raw outputs remain
unchanged in an isolated local experiment, and no production projects were
created. See `tools/evaluate_requirement_skills.py` and
`tests/fixtures/requirements-skill-comparison/README.md`. The actual skill
files were verified against their pinned Git revisions, not substituted by
evaluation summaries. These results call for additional source-condition
fidelity and review tests on fresh documents, not prompt tuning on KEP-2799
followed by a claim of independent validation.
