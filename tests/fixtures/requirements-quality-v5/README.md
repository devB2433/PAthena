# Requirement quality v5 development benchmark

`design.md` is an original synthetic design, intentionally different from the
public Kubernetes source used in earlier comparisons. This is a developer-owned,
non-blind development benchmark, not an independent accuracy estimate.

Freeze source, semantic rubric, versions, parameters and task order before calls.
Use the production executor and full checked-in skill packets; no gold facts or
rubric are given to extraction/generation. Judge the first outputs unchanged.
Score normative statements/criteria, not reassuring rationale. Store every
failure and all physical-request usage. An absent or failed output earns no
coverage and must not be silently retried away.

The rubric requires both valid new inference and fidelity to explicit constraints.
Count each justified invariant once; more requirements or more source quotations
are not better scores. Rate each requirement separately for unnecessary scope,
unsupported mandates, feasibility, testability and provenance. Report full/partial
coverage, valid inference, overconstraint and unresolved cases separately. Do not
collapse these into a single unsupported accuracy percentage.

The prior public KEP review challenge is a regression set, not a held-out test.
Mapper/checker/legacy control methods are revised in the same corpus but are not
validated merely by this requirements-only experiment.

## Reproduce the final known development case

`plan.json` and `refinement-plan.json` are historical pre-call receipts. The
intermediate 0.5.0 complete packet snapshots remain in the local experiment, not
as a replacement production skill tree. `reproduce-plan.json` pins the final
0.5.1 full packet hashes and the Git 0.4.0 baseline. Use a fresh local folder:

```sh
mkdir -p reports/requirements-quality-reproduction
cp tests/fixtures/requirements-quality-v5/design.md reports/requirements-quality-reproduction/
cp tests/fixtures/requirements-quality-v5/source.json reports/requirements-quality-reproduction/
cp tests/fixtures/requirements-quality-v5/reproduce-plan.json reports/requirements-quality-reproduction/plan.json
cp tests/fixtures/requirements-quality-v5/reproduce-plan.sha256 reports/requirements-quality-reproduction/plan.sha256
.venv/bin/python tools/evaluate_requirement_skills.py prepare --directory reports/requirements-quality-reproduction
```

Run `gateway` for that directory with the already configured `.env`, then `run`
in another terminal with the same directory and port. `generated-reviews` is a
separate extension over every first-draft final requirement. These are real paid
model calls; the existing unrestricted experimental budget configuration applies.
`PATHENA_GIT` can select a working Git executable if the default one is unavailable.

`result-summary.json` contains developer judgments, both improvements and
remaining failures, plus known/unknown consumption. Frozen-goal coverage is not
precision or exhaustive security recall. The final all-supported reviews missed
known problems; semantic acceptance is explicitly not passed.
