# PAthena

[中文](README.md) | **English**

PAthena is a group of security analysis agents for design reviews and code delivery. Given project design documents and source code, it extracts security requirements from the design, derives relevant compliance requirements using a structured standards library, builds a threat model from the code, checks each requirement against its implementation, and then audits for vulnerabilities and consolidates the implementation comparison.

The workflow produces four deliverables: **security requirements, a threat model, requirement implementation assessments, and Findings**. Requirements identify their design or standard sources. Implementation checks correspond to individual requirements. Findings identify the relevant code, explain the impact, and provide recommendations.

## What it is useful for

- **Design reviews**: Organize assets, roles, data flows, and security controls from Word, PDF, and PowerPoint design documents. Extract explicit requirements and additional requirements inferred from design risks, with acceptance criteria for each requirement.
- **Compliance requirement analysis**: Match design security requirements to structured PCI DSS v4.0.1 clauses already stored in the database. Use the design context to derive relevant or potentially relevant compliance controls while preserving clause sources and applicability conditions.
- **Delivery acceptance**: Check each security requirement against the actual source code. Identify implementation support, partial implementation, requirement violations, and controls that need configuration or other material before a conclusion can be reached.
- **Security audits**: Investigate candidate vulnerabilities using the code architecture, trust boundaries, and attack paths. Present independent code audit results alongside requirement implementation gaps to help plan remediation and further verification.

An analysis can therefore answer both “What candidate security problems exist in the code?” and “How much of the security design is implemented, and what gaps remain?” Even when a rule scan does not flag a vulnerability, an implementation check can still record a design requirement as unmet or not yet established.

## How the agents work together

Roles run through a shared Google ADK framework. The workflow determines stage order, task scope, and conditional branches. Each role uses its own versioned skills, prompts, tools, and result structure. Independent requirement tasks within a stage can run up to the configured concurrency limit. Upstream artifacts are persisted for later roles to read; the application validates responses and stores them in the database.

| Responsibility | Actual roles | Tasks |
| --- | --- | --- |
| Design analysis | `design_analyst` | Extract assets, modules, roles, data flows, and controls; distinguish document statements from analytical inferences |
| Security requirement generation | `requirement_generator` | Turn design facts into security requirements, source explanations, and individual acceptance criteria |
| Standard matching and compliance requirement generation | `pci_mapper`, `pci_requirement_generator` | Retrieve structured clauses, assess technical relevance and applicability conditions, and generate compliance requirements with explicit conditions |
| Requirement review | `requirement_reviewer` | Automatically review wording, duplicates, sources, and checkability; preserve original requirements and review results |
| Code understanding | Mantis `history`, `structural_index`, `architect` | Organize code context and structural indexes; establish the actual architecture and module relationships |
| Threat modeling | Mantis `threat_modeler` | Analyze code entry points, assets, trust boundaries, and possible attack paths |
| Requirement implementation checks | `requirement_checker` | Investigate source code independently for each requirement and return results and code locations for every acceptance criterion |
| Vulnerability planning and research | Mantis `planner`, `researcher` | Plan investigations using the threat model and requirement gaps; research candidate security problems along code paths |
| Findings deduplication and review | Mantis `deduplicator`, `reviewer`, `critic` | Merge duplicates, review static conclusions, seek counterexamples, and retain excluded findings |
| Attack chains and risk analysis | Mantis `chainer`, `calibrator` | Analyze possible relationships between findings when static review conditions are met, and assess risk |
| Reflection and reporting | Mantis `reflector`, `reporter` | Organize investigation results and native audit reports; the product report module consolidates the four deliverables |

Document analysis, standard matching, and requirement checks use this project's domain logic. Code understanding, threat modeling, and Findings audits directly run a pinned version of the Mantis core with the corresponding full skills. Mantis selects subsequent branches based on investigation results, so individual Findings do not necessarily pass through every role. Requirement gaps are passed to vulnerability planning as investigation leads; further analysis determines whether they represent exploitable problems.

The current workflow generates and reviews requirements automatically, with no mandatory human approval of the requirement baseline. All analysis is static and does not run the target program. Dynamic reproduction and automatic patching stages have been removed from the execution workflow.

## Four deliverables

| Stage | Inputs and processing | Outputs |
| --- | --- | --- |
| 1. Security requirements | Parse design documents; extract design facts, explicit requirements, and inferred requirements; match requirements to structured PCI DSS clauses; generate relevant compliance requirements and review them automatically | Requirements, acceptance criteria, design sources, related clauses, and applicability conditions |
| 2. Threat modeling | Mantis reads a fixed source snapshot and uses upstream design and requirement context to establish actual architecture, entry points, trust boundaries, and attack paths | A threat model grounded in code and specific threats |
| 3. Requirement implementation assessments | Create an independent task for each requirement; use the code model and source navigation to investigate entry points, shared controls, and error paths; check each acceptance criterion | Static assessment results corresponding to each requirement, reasoning, and source locations |
| 4. Findings | Mantis uses the threat model and implementation gaps for planning, research, deduplication, static review, criticism, attack chain analysis, and risk assessment; the final report consolidates the vulnerability audit and implementation comparison | Candidate vulnerabilities, implementation or design gaps, impact, causes, recommendations, and code locations; excluded candidates are retained separately |

```mermaid
flowchart LR
    D["Design documents: Word / PDF / PPT"] --> R["1. Security requirements"]
    P["Structured PCI DSS clause library"] --> R
    R --> T["2. Threat modeling"]
    C["Fixed source snapshot"] --> T
    T --> A["3. Implementation assessments"]
    R --> A
    C --> A
    A --> F["4. Findings static audit"]
    T --> F
    F --> O["Report: four deliverables and implementation comparison"]
    R --> O
    A --> O
    T --> O
```

Security requirements and implementation checks share a stable identifier such as `SR-001` within each run. Each requirement has an independent check task and may contain multiple acceptance criteria. Every criterion must be submitted exactly once. Results distinguish static support, partial implementation, requirement violations, unknowns, and external evidence requirements. Tasks that have not been checked remain incomplete. Failure to locate a local code fragment does not establish that a control is absent: the check must also investigate shared middleware, other modules, or external responsibilities.

For example, suppose the design requires that “a disabled account must no longer access protected resources.” The system first records the requirement and acceptance criteria. It then uses the code model to locate login entry points, token validation, and shared authorization paths, and checks whether account disabling covers those paths. If the source behavior conflicts with the requirement, the report retains the implementation gap and its code location. Vulnerability research then investigates attack preconditions and impact. If the conclusion depends on runtime configuration, the result explicitly requests external material.

## How it differs from SAST

SAST (Static Application Security Testing) and PAthena can both analyze source code without executing the target program. Common rule- or query-driven SAST tools primarily check dangerous patterns, data flows, and vulnerability rules in code. PAthena adds design and standard requirement analysis before the static code audit, checks implementation against those project-specific requirements, and links the threat model, implementation gaps, and Findings.

| Dimension | Common rule- or query-driven SAST | PAthena |
| --- | --- | --- |
| Starting point | Source code, rules, and queries; some tools also read build or configuration material | Design documents, a fixed source snapshot, and a structured standards library |
| Source of security requirements | Built-in or custom vulnerability rules, security patterns, and queries | Explicit design requirements, requirements inferred from design risks, and compliance requirements derived from matched standard clauses |
| Architecture and threats | Usually analyzes program structure, data flows, and rules; capabilities vary by tool | Explicitly establishes architecture, trust boundaries, entry points, and attack paths from code for subsequent investigation |
| Implementation comparison | Checks code for issues described by rules; custom rules can express business controls | Creates an independent task for each security requirement, checks each acceptance criterion, and retains requirement identifiers, reasoning, and source locations |
| Vulnerability investigation | Rules or queries locate candidate issues and provide code locations or data flow paths | Agents continue investigating using the threat model, requirement gaps, and actual source code, followed by deduplication, static review, and criticism |
| Compliance results | Some tools map rules to standards or provide compliance classifications | Matches design requirements to structured standard clauses, preserves technical relevance and applicability conditions, and checks the implementation of related controls |
| Main outputs | Candidate issues, classifications, severity, and code locations, among other results | Security requirements, a threat model, requirement implementation assessments, Findings, and their relationships |

This comparison describes common workflows. Individual SAST products can also support custom business rules, model-based analysis, or requirement management integrations. The tools can be used together: rule scans check the code patterns they cover, while PAthena further organizes the project's design requirements, threats, and implementation comparison. The current version does not provide a dedicated import workflow for external SAST results.

PAthena's implementation verification is a **static assessment**. Code support does not establish that a deployment is correctly configured, and a requirement gap does not automatically imply an exploitable vulnerability. Organizational processes, personnel responsibilities, and actual deployment controls require external material. A related PCI DSS clause does not establish that the project formally falls within its scope, and the report does not provide compliance certification.

## Technology stack

- Backend: Python 3.12, FastAPI, Google ADK, LiteLLM, Pydantic.
- Frontend: TypeScript, React, Vite, Ant Design.
- Database: SQLite, WAL, FTS5; persistence for the structured standards library, tasks, raw model responses, and results.
- Document parsing: Docling. Source navigation: Mantis and local tree-sitter grammars.
- Model: DeepSeek `deepseek-flash` by default, accessed through a separate model gateway.
- Deployment: Two containers for the analysis service and model gateway. The analysis service serves the frontend from the same origin.

English is the default output language. Selecting Chinese in system settings directly instructs subsequent analysis through skills and prompts to generate Chinese content. Existing results are not translated afterward. Each run freezes its own output language.

## Container deployment

Docker Compose is required. Before the first startup, create local configuration and directories for input material:

```sh
cp .env.example .env
mkdir -p inputs models standards
```

Edit `.env` and set `DEEPSEEK_API_KEY`. The key is provided only to the model gateway. `.env`, input material, original standards, models, databases, and reports are excluded from version control.

```sh
docker compose --env-file .env -f deploy/compose.yaml build
docker compose --env-file .env -f deploy/compose.yaml up -d
```

Open <http://127.0.0.1:8088>. The project list is empty on first startup. Create a project, upload a design document, and select a source repository to start analysis.

Target source code can be placed in `inputs/project`. Configure it in `.env`:

```dotenv
AUDITOR_REPOSITORIES={"project":"/inputs/project"}
```

You can also use the template for mounting an external repository read-only:

```sh
mkdir -p deploy/local
cp deploy/compose.repositories.example.yaml deploy/local/repositories.yaml
```

Set `AUDITOR_REPOSITORY_DIR` in `.env` to the repository's absolute host path, and edit the registered name in the template as needed. Start with the additional configuration:

```sh
docker compose --env-file .env -f deploy/compose.yaml -f deploy/local/repositories.yaml up -d
```

The gateway forwards requests only to `api.deepseek.com` and rejects redirects to other hosts. The analysis service has only an internal network. Compose does not currently provide an operating-system-level domain firewall; the deployment environment must enforce the corresponding outbound restrictions. DeepSeek receives the material excerpts needed for analysis.

State is stored in the `auditor-state` volume. Regular service rebuilds preserve the data. Keep this volume when analysis results need to be retained.

## Document models and standards library

Docling uses local parsing models. Download the resources during installation preparation:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/docling-tools models download layout tableformer --output-dir models/docling
```

To enable the parsing models inside the container, set this in `.env`:

```dotenv
AUDITOR_DOCLING_MODELS=/models/docling
```

Resource preparation is separate from analysis. The parsing process does not download resources online. Word, PowerPoint, and PDFs with text are currently supported. OCR for scanned pages and interpretation of diagram semantics need further work.

The PCI DSS source document and structured standards package are not distributed with the repository. Import your own PCI DSS v4.0.1 PDF offline:

```sh
.venv/bin/python -m pip install -e '.[standards]'
.venv/bin/python -m security_auditor.standards /path/PCI-DSS-v4_0_1.pdf standards/pci-dss-4.0.1
```

The import directory must be empty. Use `--provenance /path/source.json` to include a source record. The importer preserves the original PDF, clause text, applicability notes, testing procedures, guidance, and source digests. It does not call a model to rewrite the standard.

Set `AUDITOR_STANDARD_PACK=/standards/pci-dss-4.0.1` in `.env`. Each structured standard version is stored once and reused across projects; each run freezes its standard version. Requirement matching uses local retrieval and complete clause reads, recording technical relevance separately from formal applicability. Relevant or potentially relevant controls produce requirements that retain their applicability conditions. Candidate retrieval does not establish complete semantic coverage of the standard or provide compliance certification. When no standards package is configured, compliance steps record the gap and are skipped.

## Analysis modes

- `full`: Design requirements, standard matching, code modeling, implementation checks, and Findings.
- `requirements_only`: Analyze only design documents and standard requirements.
- `code_only`: Run only Mantis static modeling and Findings audits, without reading design documents.
- `implementation_only`: Import successfully submitted requirements and sources from an existing run in the same project, and check only their implementation.

Selecting source code without uploading a document uses code mode. Existing requirements can be checked through the “Check existing requirements” action. The API's `implementation_only` mode accepts `baseline_run_id` and `repository_id`, preserves the original requirement language, records relationships between runs, and retains historical results.

## Budgets, result submission, and recovery

API request counts, cumulative tokens, and per-task model call limits are temporarily disabled, with `AUDITOR_ENFORCE_BUDGETS=false` by default. This switch applies to the analysis service, outbound gateway, ADK, and Mantis adapter. Saved budgets on historical runs no longer block execution, while original budgets and cumulative usage are retained. Budget settings are hidden in the UI, new analyses have no limits, and legacy limits submitted through the API have no effect.

To restore limits, set `AUDITOR_ENFORCE_BUDGETS=true` in both the analysis service and gateway, then configure `AUDITOR_MAX_REQUESTS`, `AUDITOR_MAX_TOKENS`, and `AUDITOR_AGENT_MAX_CALLS`. All default to `0`, meaning unlimited. Budget controls then become available in the UI. In the API, `null` for `budget.max_requests` or `budget.max_tokens` means unlimited. Per-response output length, context capacity, concurrency, timeouts, and retry limits remain in place; these are separate from cumulative consumption limits.

Each actual outbound request is reserved and accounted for atomically. Known provider usage, in-flight reservations, and estimates for unknown usage are displayed separately; estimates are not invoices. Connection failures, rate limits, and temporary service errors use bounded retries and recovery waits. Authentication, balance, parameter, and result contract issues retain explicit errors. A user pause stops automatic wake-ups.

Project-defined roles use strict function argument contracts. Responses are archived first, then validated for schema, task ownership, sources, and acceptance-criterion inventory before transactional database submission. The application does not fabricate analytical fields, infer results from prose, or call another model to interpret or repair the final response. The application supplies fixed task relationships and rejects conflicting relationships returned by a model.

Security judgments can cite only material actually read in the current session. Search results, summaries, and historical reads do not replace current source reads. Conclusions of implementation support, partial implementation, or requirement violations must have actual source references. Native Mantis analysis retains its investigation loops and structured conclusions, with no dynamic reproduction or target execution permissions.

Results can be exported as HTML, CSV, and JSON. Reports retain requirement relationships, sources, code locations, and excluded candidates. Deployment does not guarantee that model output will always be valid. When final structure or source validation fails, the task remains incomplete and requires the blocking issue to be addressed before continuing.

## Local development and validation

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m pip install -e '.[dev]'
cd apps/web
pnpm install --frozen-lockfile
pnpm run build
cd ../..
.venv/bin/pytest -q
.venv/bin/ruff check src tests --exclude vendor
.venv/bin/auditor doctor
```

Local startup does not automatically load `.env`; provide configuration to the corresponding processes. Set `DEEPSEEK_API_KEY` only for the gateway process and point `AUDITOR_GATEWAY_DATABASE` to the same database used by the analysis service:

```sh
AUDITOR_GATEWAY_DATABASE=./state/auditor.sqlite3 .venv/bin/auditor gateway
```

In another terminal, register the repository, parsing models, and standards package, then start the analysis service:

```sh
AUDITOR_REPOSITORIES='{"project":"/absolute/path/to/project"}' \
AUDITOR_DOCLING_MODELS=./models/docling \
.venv/bin/auditor serve --port 8088
```

PDF tests require models prepared in advance and explicitly skip when resources are missing. Protocol tests use controlled model responses and do not establish real-world analysis accuracy. The first complete run with a real model required debugging and resumptions. Fully unattended stability and the validity of Findings have not yet passed acceptance testing. See [Current implementation status](docs/development-status.md) for other limitations.

## Project structure and provenance

```text
apps/web/                  Frontend
src/security_auditor/      Backend, database, model gateway, and adapters
src/security_auditor/vendor/mantis/  Pinned Mantis core and skills
skills/                    Project-defined versioned analysis skills
design/                    Workflow and configuration examples
deploy/                    Containers and general deployment templates
tests/                     Automated tests and minimal test fixtures
docs/                      Architecture, parsing, static reuse, and status notes
licenses/                  Third-party licenses
```

Mantis comes from [google/mantis](https://github.com/google/mantis). The pinned commit and file digests are recorded in the vendored `SOURCE.json`. Its Apache-2.0 license and [third-party notices](THIRD_PARTY_NOTICES) are retained. The internal name `security-design-auditor` is used for the package and Compose project.

Detailed design documents are available in [Implementation plan](docs/implementation-plan.md), [Frontend, backend, and deployment](docs/frontend-backend-deployment.md), [Document parser selection](docs/document-parsing-selection.md), [Mantis skills review](docs/mantis-skills-review.md), and [Static audit reuse](docs/mantis-static-parity.md). Capabilities described as targets in design documents should be read alongside the current implementation and status. These linked design and status documents are currently in Chinese.
