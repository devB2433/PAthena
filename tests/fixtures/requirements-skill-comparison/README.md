# KEP-2799 requirement-skill comparison inputs

This is a public-document test fixture, not product demonstration data. The
original document, model outputs, databases and scoring annotations are kept
outside the tracked tree. `source.json` pins the public document URL, commit and
SHA. `plan.json` fixes 13 source-backed behaviors and eight error traps before
any model responses. Its checksum is `plan.sha256`.

The development agent authored the reference expectations and scored the model
results; this was not blind, independent expert adjudication. The shared fixed
facts intentionally cover the reference behaviors and isolate generation from
document reading. The six review challenges are synthetic mutations grounded
in the same real source. They are not sufficient for real-draft acceptance.

Run with `tools/evaluate_requirement_skills.py`. Copy these three JSON/checksum
files into a new ignored experiment directory and download the exact document
from the raw GitHub URL assembled from source.json's repository, commit and
path; save it as design.md. The prepare command verifies all bytes before use.
Start the gateway on a free loopback port, then run the comparison in a second
terminal with the same directory and port. Only the gateway reads .env.

```
.venv/bin/python tools/evaluate_requirement_skills.py prepare --directory reports/my-skill-test
.venv/bin/python tools/evaluate_requirement_skills.py gateway --directory reports/my-skill-test --port 18881
.venv/bin/python tools/evaluate_requirement_skills.py run --directory reports/my-skill-test --port 18881
```

These commands make paid real-model calls. Stop the gateway after completion.
The optional generated-reviews command reviews every draft of the first pair
as a separately reported exploratory extension. It never regenerates a draft.
All runs use an isolated database and the production executor and loaders.
The historical baseline is exported verbatim from Git; the candidate must match
its pinned Git files too. No abbreviated testing-only skills are supplied.
