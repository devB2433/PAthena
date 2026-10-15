# OWASP-informed concrete requirements: v6 development test

This known synthetic design is the same 40-line LedgerBridge input used in v5.
It is not a held-out project or an accuracy certification. Sources, the 13-goal
rubric, six fixed review challenges, task order and parameters were frozen before
these calls. Ten goals come from the earlier benchmark; cancellation authorization,
browser title output safety and failure-closed protection were added before this
experiment based on explicit investigation, not retroactively scored as earlier
frozen goals. Gold is never supplied to extraction or generation.

Compare the complete v0.5.1 corpus at `a620f24` with the full v0.6.0 worktree
packet, including the single canonical shared knowledge resource. Production and
tests use the same SkillLoader and AdkExecutor. Shared references are hash-checked
and archived with each complete packet, never fetched or shortened at runtime.

Rate the actual statement and criteria for object/action specificity, relevant
negative case and forbidden side effect, legal delegation, failure behavior and
source classification. A positive authorization test must not force success despite
expiry/cancellation/resource limits. Report coverage separately from defects; do
not call the proportion of covered goals overall precision or security recall.
Do not repair or silently retry failed outputs. Scripted tests only verify program
integration, not these semantic judgments.

`plan.json` and its digest are the original pre-call receipt. The final
`reproduce-plan.json` adds full packet hashes and pins the final candidate commit
when available; copy it as `plan.json` with its digest and design/source files
into a new local reports folder. Run `prepare`, then `gateway` and `run` in separate
terminals using tools/evaluate_requirement_skills.py. These invoke real paid model
calls with the user's configured credential and unrestricted experimental budget.
Raw output, complete packets, database, research snapshots and HTML report stay local.

The historical fixed-review seeding helper uses a ServiceAccount module label;
the full target statements, premises and source are LedgerBridge. Both arms receive
the same label. This limits how broadly the six-case review result can be used.
The experiment does not validate mapper retrieval, the checker or a prepared
compliance catalog. OWASP patterns are professional guidance, not imported standard
controls or an OWASP compliance claim.
