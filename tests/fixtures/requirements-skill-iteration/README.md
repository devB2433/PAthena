# Source-fidelity iteration fixtures

The public KEP-4193 is pinned by commit and SHA-256. The eight repair cases
include five exact previous model drafts, two counterfactual probes and one
valid inferred control. This is not independent expert acceptance.

The final runtime uses paired generated criteria, exact task-local paragraph
references and fixed reviewer statement/criterion slots. It performs only
deterministic metadata lookup and schema/inventory/declared-verdict checks.
Model conclusions, text, issue codes and logical conditions are not repaired.

Copy either fixture directory into an ignored experiment folder. Download the
public source specified in source.json as design.md and verify its SHA-256.
Use tools/evaluate_requirement_skills.py prepare, gateway and run with that
directory. The candidate uses the working tree but must match the expected
full packet hashes; the baseline is exported from its pinned Git commit.
The model, language, temperature, output capacity and task selection are in
plan.json. generated-reviews processes all generated drafts without editing
them. Use a distinct port and isolated state directory per experiment.

Original documents, SQLite databases, raw provider outputs, failed development
prototypes and the HTML report remain local. Gold criteria and developer
adjudication are outside the actual model prompts and product database.
No production project is created.
