# Company-PC Agent Handoff

This is the zero-context execution contract for the Agent that will install
and localize Project Copilot Workbench on a restricted company Windows PC. Do
not rely on the development conversation. Read and execute this document in
order, preserve evidence at every checkpoint, and stop at the stated safety
gates.

The public release contains generic code and fully synthetic HVAC data only.
The company PC is the only place where approved company documents, endpoints,
model names, certificates, credentials, runtime indexes and local evaluation
sets may exist.

## Current UI architecture trial (2026-07-18)

Do not rebuild the old `/workbench` dashboard. It redirects to the ordinary
single Chat. The current branch exposes four comparison routes under
`/versions`; they all use the same private workspace, model and governed tools.
Use `/versions/evidence` as the leading direction, but do not replace `/` until
the Chairman has compared all four pages.

Do not treat the architecture trial as intelligence acceptance. The final
14-case shared-backend run completed all requests but passed only 1/14
automatic hard gates; independent HVAC review scored pass 0, partial 6 and
fail 8 (21.4/100). Preserve that result and repeat the same questions after
company localization. A prettier V2/V3 page does not convert a failed answer
into a pass.

Runtime installation must use the pinned Python lock, including
`tzdata==2026.3`. It supplies `ZoneInfo` data on Windows while snapshot queries
receive the manifest timezone explicitly. Browser Markdown is already vendored as Marked 18.0.6 and
DOMPurify 3.4.12, so a company deployment does not need Node.js or internet
access to render answers. Never replace the local scripts with CDN URLs.
The same lock and installation path passed all jobs in GitHub Actions run
`29617892095`, including Windows tests and offline document parsing.

Acceptance must include:

1. Submit two questions rapidly and confirm only one backend request runs at a
   time and the second request receives the first turn as history.
2. Render nested Markdown links, bare `docs/...` paths and Windows absolute
   paths and confirm only human labels remain visible.
3. Verify V2 shows at most two original filenames plus the total count, while
   its evidence panel shows every exact filename and excerpt.
4. Verify V3 does not force-open the deliverable on mobile.
5. Run MX09 from `evaluation/four_version_complex_questions.json`; confirm it
   never asks for today's date when the historical range is explicit, and
   record answer grounding separately instead of assuming it passed.

## Current single-Chat deployment contract (2026-07-17)

This section supersedes older instructions that separated `/` and
`/workbench`:

- Open `http://127.0.0.1:8788/`. This is the only ordinary engineer-facing
  page; `/workbench` redirects to it.
- Select the active workspace, then upload one or more files beside the Chat
  composer. The server stores them in the external runtime, indexes them
  automatically, and reports per-file success or failure.
- Ask knowledge and data questions in the same Chat. Citations show the
  original filename first; internal storage names and absolute paths must not
  be shown.
- Keep the runtime outside the source checkout and release directory. A safe
  Windows layout is `D:\ProjectCopilot\app\0.2.0` for the immutable app and
  `D:\ProjectCopilot\runtime` for private sources, indexes and databases.
- Optional PDF/DOCX/PPTX/XLSX ingestion requires the `documents` extra and
  locally prefetched Docling artifacts. Run
  `scripts/prefetch_docling_assets.py` on the connected builder, transfer its
  verified output, then set `PROJECT_COPILOT_DOCLING_TOKENIZER_PATH` and
  `PROJECT_COPILOT_DOCLING_ARTIFACTS_PATH` to those local directories. Set
  `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` on the restricted PC.
- To index an approved local project tree without copying private data into
  Git, use `--catalog-root`, `--public-worktree`, and
  `--catalog-project-id`. The catalog is read-only and excludes Git metadata,
  runtimes, inbox/outbox state, task leases, execution state, secrets, keys,
  caches and build outputs.

Example launch after installing the reviewed wheel:

```powershell
$env:PROJECT_COPILOT_MODEL_MODE = "company"
$env:PROJECT_COPILOT_OPENAI_BASE_URL = "https://approved-host.example/v1"
$env:PROJECT_COPILOT_MODEL = "approved-model-id"
$env:PROJECT_COPILOT_ALLOWED_HOSTS = "approved-host.example"
$env:PROJECT_COPILOT_DOCLING_TOKENIZER_PATH = "D:\ProjectCopilot\models\all-MiniLM-L6-v2"
$env:PROJECT_COPILOT_DOCLING_ARTIFACTS_PATH = "D:\ProjectCopilot\models\docling"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
project-copilot --runtime D:\ProjectCopilot\runtime --host 127.0.0.1 --port 8788
```

Do not copy the development machine's model credentials. The company launcher
must inject its own approved secret. Verify `/api/health`, upload a synthetic
PDF, ask an exact-filename question, inspect the citation, restart the process,
and repeat the query before opening real company material.

## 1. Role and completion definition

Your role is deployment operator and acceptance recorder, not architecture
designer. Do not replace frameworks, weaken security settings, expose the app
to the LAN, add Web/MCP/code tools, enable model-generated SQL, or change the
embedding model during installation.

The company deployment is complete only when all of these are true:

1. The exact reviewed Git commit and release artifacts are identified.
2. Every transferred artifact matches the approved SHA-256 manifest.
3. Python 3.12 and all runtime packages install from the offline wheelhouse
   with no public index access.
4. The synthetic smoke test passes before any real company source is opened.
5. The company model host is HTTPS, appears in the exact allowlist, and passes
   internal-CA validation without disabling TLS verification.
6. Embeddings remain disabled unless a separate data-owner approval exists.
7. A real Project Package is stored outside the Git/release directories and is
   imported into an isolated runtime.
8. Source inventory, indexing errors, cited answers, governed analytics,
   refusal behavior and browser workflow pass the acceptance checklist.
9. Firewall/proxy evidence shows no unapproved outbound destination.
10. Backup, restore and rollback are rehearsed and recorded.

## 2. Read order

Read these files before running commands:

1. `README.md`
2. `docs/architecture.md`
3. `docs/company-deployment-v2.md`
4. `docs/admin-user-guide.md`
5. `docs/acceptance-checklist.md`
6. `docs/evaluation.md`
7. `config/company-v2.example.ps1`
8. `THIRD_PARTY_NOTICES.md`

Read `docs/light-rag-direct-deploy.md` only if the release owner explicitly
asks for a separate LightRAG A/B trial. LightRAG is not connected to the V2
application and is not required for deployment.

## 3. Required inputs from the release owner

Do not start without all required values. Store the answers in the company
release ticket, never in the public repository.

| Input | Required value |
|---|---|
| Approved Git commit | Full 40-character SHA |
| Application version | `0.2.0` for this handoff |
| Release directory | Immutable transferred directory |
| SHA256 manifest hash | Received through a second trusted channel |
| Python target | Company-approved CPython 3.12, architecture recorded |
| Install root | Example: `D:\ProjectCopilot\app\0.2.0` |
| Runtime root | Example: `D:\ProjectCopilot\runtime` |
| Project root | Outside Git/release tree; access-controlled |
| Model base URL | Full approved OpenAI-compatible `/v1` URL |
| Model identifier | Approved deployment/model name |
| Exact allowed hostnames | No wildcard and no URL path |
| Internal CA bundle | File path and independently recorded hash, if used |
| Secret source | Approved vault/service launcher; never a script or `.env` |
| Embedding approval | Explicit yes/no, model, endpoint and approval ticket |
| Data owner | Named approver for imported documents and telemetry |
| Network owner | Named approver for firewall and egress evidence |
| Backup owner | Destination, encryption and retention policy |

If any required value is missing, write a blocking checklist entry and stop
before importing real data. You may still perform the isolated synthetic smoke
test if the release hashes and Python inputs are complete.

## 4. Non-negotiable stop conditions

Stop and escalate without improvising if any condition occurs:

- a transferred hash does not match;
- the Git commit differs from the approved commit;
- `pip` attempts to use a public package index during offline installation;
- a runtime dependency is available only as an unapproved source archive;
- the model or embedding hostname is not on the exact allowlist;
- a non-loopback endpoint uses HTTP;
- TLS validation fails or someone proposes `verify=false`;
- a secret appears in a script, log, screenshot, Git diff or command output;
- the app is asked to bind to `0.0.0.0` or a LAN address;
- the real Project Package is under the Git clone or release directory;
- an Office/PDF document requires Docling but the separate offline parser/model
  bundle has not passed no-egress acceptance;
- any runtime/parser wheelhouse contains a source archive, an offline lock is
  missing, an offline/source lock name-version set differs, or production is
  asked to install the CI/test offline lock;
- the requested workflow requires Shell, arbitrary Python, Web, MCP, free-form
  SQL, physical equipment control or a new unreviewed Agent tool;
- source deletion, re-index, migration or rollback lacks a current backup;
- the release guard, dependency audit, license report, SBOM, Gitleaks, tests or
  GitHub Actions are not green for the exact commit.

## 5. Verify the received release

Use the exact PowerShell commands in section 3 of
`docs/company-deployment-v2.md`. Confirm:

```text
SHA256SUMS.json: all files present, all hashes match
Git bundle: valid
Git commit: exact approved SHA
Wheel: project_copilot_workbench-0.2.0-*.whl
Source locks: requirements.build.lock and requirements.*.lock present
Deployment lock: requirements.runtime.offline.lock present
Optional parser production lock: requirements.documents.offline.lock present
Optional parser test lock: requirements.documents-ci.offline.lock present
SBOM: sbom.cdx.json present
License evidence: license-report.json present
```

Save command output in the release evidence directory. Do not copy secrets or
real document content into that directory.

Checkpoint `CP-01` is complete when the independent manifest hash and every
file hash match. Record operator, machine, date/time and full commit SHA.

## 6. Install into a new virtual environment

Follow section 5 of `docs/company-deployment-v2.md`. Never upgrade an in-use
environment in place. The required shape is:

```powershell
$Release = "D:\ProjectCopilot\releases\APPROVED"
if (Test-Path -LiteralPath "D:\ProjectCopilot\app\0.2.0\venv") {
  throw "Application venv already exists; create a fresh release path"
}
py -3.12 -m venv D:\ProjectCopilot\app\0.2.0\venv
& D:\ProjectCopilot\app\0.2.0\venv\Scripts\python.exe -m pip --isolated install `
  --no-index --no-cache-dir --only-binary=:all: `
  --find-links "$Release\wheelhouse-build" pip==26.1.2
& D:\ProjectCopilot\app\0.2.0\venv\Scripts\python.exe -m pip --isolated install `
  --no-index --no-cache-dir --only-binary=:all: `
  --find-links "$Release\wheelhouse" --require-hashes `
  -r "$Release\requirements.runtime.offline.lock"
& D:\ProjectCopilot\app\0.2.0\venv\Scripts\python.exe -m pip install `
  --no-index --no-deps "$Release\wheel\project_copilot_workbench-0.2.0-py3-none-any.whl"
& D:\ProjectCopilot\app\0.2.0\venv\Scripts\python.exe -m pip check
```

Replace `APPROVED` and the wheel filename with the verified received values.
Do not copy a developer `.venv`.

The command above is the base profile. If Office/PDF parsing was explicitly
approved and the isolated parser test in the deployment runbook passed, replace
the runtime offline-lock dependency command with this complete
production-parser profile; do not install both profiles and do not install the
CI/test offline lock in the production venv:

```powershell
& D:\ProjectCopilot\app\0.2.0\venv\Scripts\python.exe -m pip --isolated install `
  --no-index --no-cache-dir --only-binary=:all: `
  --find-links "$Release\wheelhouse-documents" --require-hashes `
  -r "$Release\requirements.documents.offline.lock"
& D:\ProjectCopilot\app\0.2.0\venv\Scripts\python.exe -m pip check
```

Checkpoint `CP-02` is complete when `pip check` succeeds and
`project-copilot.exe --help` exits zero without network access.

## 7. Synthetic first-start gate

Start in deterministic mode with a disposable runtime and the synthetic
Project Package extracted from the approved source archive:

```powershell
$env:PROJECT_COPILOT_MODEL_MODE = "deterministic"
$env:PROJECT_COPILOT_KNOWLEDGE_PROVIDER = "local"
$env:HAYSTACK_TELEMETRY_ENABLED = "False"

& D:\ProjectCopilot\app\0.2.0\venv\Scripts\project-copilot.exe `
  --project D:\ProjectCopilot\smoke-source\examples\synthetic_hvac `
  --runtime D:\ProjectCopilot\runtime-smoke --port 8788
```

From a second PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8788/api/health
```

Expected properties include `status=ok`,
`project_id=synthetic-hvac-demo`, and `egress_mode=loopback-only`.

Complete the human journey in `docs/admin-user-guide.md`: create a workspace,
upload one synthetic meeting note, confirm indexed inventory, ask a cited
question, inspect the tool activity, re-index, and verify an unsafe control
request is refused. Do not proceed if labels, errors, citations or inventory
are unclear to the operator.

Checkpoint `CP-03` is complete when the synthetic UI, API health, cited answer,
governed analytics and refusal behavior pass with no browser console error.

## 8. Configure the company endpoint

Inject the API key from the approved secret source, then load
`config/company-v2.example.ps1` as shown in section 6 of the deployment
runbook. The script must print the model URL, model identifier and allowed
hosts, but never the key value.

If company PowerShell policy blocks unsigned scripts, do not set the machine or
user policy to `Unrestricted` and do not copy a developer's one-off
`-ExecutionPolicy Bypass` practice into production. Have the company endpoint
owner review/sign the script or translate the same explicit variables into the
approved service launcher. Record the policy/signature evidence.

Start with embeddings disabled. Expected startup variables are:

```text
PROJECT_COPILOT_MODEL_MODE=company
PROJECT_COPILOT_OPENAI_BASE_URL=<approved HTTPS /v1 URL>
PROJECT_COPILOT_OPENAI_MODEL=<approved identifier>
PROJECT_COPILOT_OPENAI_WIRE_API=responses
PROJECT_COPILOT_ALLOWED_HOSTS=<exact host list>
PROJECT_COPILOT_OPENAI_API_KEY=<injected secret>
PROJECT_COPILOT_CA_BUNDLE=<optional reviewed PEM path>
HAYSTACK_TELEMETRY_ENABLED=False
```

If and only if embedding approval exists, invoke the configuration script with
`-EmbeddingModel`, optional `-EmbeddingBaseUrl`, and
`-ApproveEmbeddings`. A separate embedding key may be injected through
`PROJECT_COPILOT_EMBEDDING_API_KEY`; otherwise the chat key is reused. Record
that document chunks will leave the app for the approved embedding endpoint.

Office/PDF parsing and local reranking have separate artifact gates. Configure
`-DoclingTokenizerPath -DoclingArtifactsPath` only after the `documents`
wheelhouse, Docling artifact cache and local tokenizer have passed an offline
PDF/DOCX test. Both `PROJECT_COPILOT_DOCLING_ARTIFACTS_PATH` and the standard
`DOCLING_ARTIFACTS_PATH` must resolve to the same immutable reviewed directory.
Configure
`-RerankerModelPath -ApproveReranker` only after the `reranking` wheelhouse,
model license/hash review and frozen-gold Recall/MRR/NDCG A/B pass. Both paths
must be immutable local directories; runtime model download is forbidden.

Checkpoint `CP-04` is complete when TLS, exact-host validation, model smoke
query, proxy-disabled behavior and firewall destinations match the approval.

## 9. Prepare and import the real Project Package

Create an access-controlled project directory outside the Git and release
trees. Keep original source files immutable. Use the category and format rules
in `docs/project-package.md`.

Recommended initial import order:

1. project background and equipment schedule;
2. current configuration and controller export;
3. dated meetings, decisions and action register;
4. commissioning/change records and field technician notes;
5. SOPs, alarm response and safety procedures;
6. approved telemetry CSV.

For the first import, use a new workspace ID. Confirm every expected file
appears in the inventory with category, parser, size, hash and status. Parser
errors are acceptance failures, not files to ignore. Do not enable the Docling
extra merely to make an error disappear; first pass its separate offline bundle
and no-egress gate.

Checkpoint `CP-05` is complete when the source inventory count and hashes match
the data-owner manifest, every required source is indexed, and errors have an
owner and resolution record.

## 10. Run company-local evaluation

Do not copy company questions or answers into the public repository. Create a
private local evaluation set patterned after `evaluation/gold_cases.json` and
cover at least:

- exact equipment/configuration lookup;
- current versus superseded configuration;
- meeting timeline and effective decision date;
- cross-document synthesis;
- field change plus follow-up verification;
- knowledge plus telemetry analysis;
- missing evidence and conflicting evidence;
- hostile prompt/tool escalation;
- wrong-workspace and deleted-source isolation;
- citation usefulness for a human HVAC engineer.
- exact asset/controller/firmware rule binding and a bounded defrost replay;
- insufficient/unobservable behavior for gaps, missing points, or timing finer
  than the sample interval.

Before authoring private cases, run the public role-isolation regression from
the verified source tree:

```powershell
& .venv\Scripts\python.exe -m pytest `
  evaluation/test_hvac_role_benchmark.py -q
& .venv\Scripts\python.exe -m evaluation.run_hvac_role_benchmark `
  --runtime D:\ProjectCopilot\evaluation\synthetic-role-runtime `
  --output D:\ProjectCopilot\evaluation\synthetic-role-result.json
```

Also run the public 52-case Agentic RAG contract against an approved company
Responses endpoint or a separately approved test endpoint. Keep results outside
Git when they contain any company-specific model or operational metadata:

This endpoint exercises the bundled synthetic direction corpus. For company
Project Package acceptance, activate the imported workspace in the root Chat
and also retain workspace-scoped API evidence for auditability.

```powershell
& .venv\Scripts\python.exe -m evaluation.run_agentic_rag_candidate `
  --candidate-id company-approved-agentic-rag `
  --endpoint http://127.0.0.1:8788/api/direction/query `
  --model-label approved-company-model `
  --output D:\ProjectCopilot\evaluation\agentic-rag-live.json `
  --resume
```

The final public synthetic v35 reference completed 52/52 requests without
execution failure. Its automatic checks passed 52/52 behavior, 52/52 tool
contract and 44/44 exact evidence contract. These are structural/grounding
checks, not answer correctness. Preserve the raw result and the independently
bound 52-case HVAC review separately; final v35 accepted 52/52 and is bound to
raw-result SHA
`f17bf6a25f333570ebb73daeb3c43bed13069438c19c32b5bf27a1208285fbca`.
Never edit raw JSON to match a human decision.

The public benchmark is a deterministic smoke test only. Its four role areas
show the required private layout: design engineer, controls/commissioning
engineer, field-service engineer, and project-delivery engineer. Build the
company set outside Git with one access-controlled data area and gold set per
role. Import the approved shared Project Package plus only that role's overlay
into a fresh runtime root. Never reuse one role's workspace or index for
another role's score.

Company-private coverage must not be defrost-only. Include equipment schedules,
point lists, effective configuration, meeting/change history, SOPs, alarm and
work-order evidence, energy/temperature/pressure/flow trends, data quality,
time-window analysis, and safety refusal. Defrost or another complex control
sequence is one temporal case. Bind every model-specific case to asset,
controller, firmware, document revision, units, time zone, point mapping,
sample interval, and immutable source hashes. A vendor table or threshold must
remain private and version-bound; do not generalize it across product families.

For a company-model campaign, preserve the frozen deterministic expectations
and add an optional reviewed DeepEval adapter for faithfulness, context
precision/recall, task completion, tool correctness, argument correctness, and
step efficiency. The judge must use the approved company endpoint, be calibrated
against independent HVAC-engineer ratings, and retain disagreements. Do not
convert an uncalibrated judge score into an acceptance percentage.

Record per-case expected sources, required answer facts, expected tool names,
refusal/clarification outcome and measured latency. Classify failures as
corpus, parser, retrieval, ranking, tool selection, grounding, analytics, UI,
deployment or policy. Before changing architecture or code, search current
official/GitHub solutions for the failure class and compare mature options.

Checkpoint `CP-06` is complete only when the data owner accepts the measured
results. Never invent or round a success percentage without the underlying
case counts.

For company telemetry, keep typed read-only operations for recurring sequence
work. The current public reference implements snapshot-wide data-quality,
control-event, alarm-event and allowlisted metric-extreme tools. Extend these
operations under tests instead of allowing the model to execute CTEs, window
functions, file access or unrestricted SQL.

The root model-backed Chat reads the active imported workspace; the
workspace-scoped API remains available for automated acceptance. The synthetic
direction Agent is bounded to 11 steps, 10 tools and 180 seconds. Event
windows use `[start_time, end_time)`, compressor mismatch windows under 60
seconds are observations, and A311 is an alarm code rather than an asset ID.
The live benchmark aborts after two consecutive provider failures and resumes
only after provenance validation.

## 11. Human HVAC-engineer UX acceptance

Use a representative engineer who did not build the application. Ask them to
complete the journey without coaching. Observe rather than explain.

Record whether the engineer can:

1. identify the active project immediately;
2. understand the difference between importing files and importing a package;
3. select a category without knowing software terminology;
4. see indexing progress and know what to do after an error;
5. ask a normal work question rather than an artificial prompt;
6. distinguish the answer from its evidence;
7. open and understand citations, sections/pages and tool activity;
8. recognize conflicting or superseded configurations;
9. understand why an unsafe request was refused;
10. find re-index and delete, and recognize that delete is destructive;
11. use the same essential flow at the approved mobile viewport;
12. recover from a typo, empty upload, unsupported format or endpoint outage.

Any label that requires the developer to explain it is a UX defect. Do not
replace a clear work term with an Agent/RAG/vector/database acronym in the main
journey. Keep technical diagnostics available to administrators, not as the
primary user language.

Checkpoint `CP-07` is complete when all Critical/Important UX findings are
fixed or explicitly accepted by the product owner and the browser test is
rerun.

## 12. Network, audit, backup, restore and rollback

Execute the exact evidence procedures in sections 10-14 of
`docs/company-deployment-v2.md` and complete sections H, L and M of
`docs/acceptance-checklist.md`.

Minimum evidence:

- allowed process and destination list;
- DNS/TCP/TLS/firewall observations during idle, import, re-index and query;
- proof that environment proxy inheritance is not used by model clients;
- application/reverse-proxy logs without request bodies or secrets;
- stopped-application backup with hash and approved encryption;
- restore into a new directory followed by health, inventory, cited query,
  analytics and refusal checks;
- previous wheel, runtime snapshot and configuration-without-secrets available
  for rollback;
- disk-full, expired-certificate, unavailable-endpoint and parser-error owners.

Checkpoint `CP-08` is complete when restore and rollback work without changing
the original backup or exposing data.

## 13. Final handback record

Return a compact evidence package to the release owner. Include:

```text
deployment_status: pass | blocked
application_version:
git_commit:
machine_and_python:
release_manifest_hash:
install_root:
runtime_root:
project_id:
source_inventory_count:
model_mode_and_host_without_secret:
embeddings_enabled: yes | no
company_evaluation_case_count_and_pass_counts:
browser_acceptance:
security_and_egress_evidence_paths:
backup_restore_evidence_paths:
rollback_evidence_paths:
open_critical_findings:
open_important_findings:
accepted_limitations:
next_action_and_owner:
```

Do not paste raw documents, prompts, answers, credentials, full logs or worker
transcripts into the handback. Reference access-controlled artifact paths.

The deployment is `blocked` if any Critical/Important finding remains without
explicit owner acceptance. A blocked result is a valid safe outcome; silently
weakening a control is not.
