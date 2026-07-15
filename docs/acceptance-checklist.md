# V2 Acceptance Checklist

Use this checklist for a specific Git commit and release artifact set. Do not
mark an item complete from memory, an earlier build, or an unrecorded manual
observation. Every mandatory item needs a timestamp, operator/reviewer, result,
and evidence path or CI URL.

## Acceptance record

| Field | Value |
|---|---|
| Release version | `0.2.0` / replace if changed |
| Git commit | `REPLACE` |
| GitHub Actions run | `REPLACE` |
| Windows edition/build | `REPLACE` |
| Python/architecture | `3.12 / REPLACE` |
| Wheel SHA-256 | `REPLACE` |
| Dependency manifest SHA-256 | `REPLACE` |
| SBOM SHA-256 | `REPLACE` |
| Company test endpoint/test-double | `REPLACE` |
| Synthetic corpus version/hash | `REPLACE` |
| Acceptance operator | `REPLACE` |
| Independent reviewer | `REPLACE` |
| Decision/date | `REPLACE` |

Statuses: `[ ]` not run, `[x]` pass, `[!]` failed/blocker, `[-]` not applicable
with written justification.

## A. Scope, custody, and public-data review

- [ ] The exact commit is recorded and `git status --short` was clean before
  artifact creation.
- [ ] The repository contains only generic code and synthetic data.
- [ ] No company document, endpoint, model ID, hostname, schema, field name,
  threshold, certificate, secret, log, screenshot, query, answer, index, or
  database is tracked or present in release artifacts.
- [ ] `examples/synthetic_hvac/SYNTHETIC_DATA_PROVENANCE.md` covers every public
  synthetic dataset/artifact.
- [ ] Real Project Packages remain outside the Git checkout and release bundle.
- [ ] The release scanner reports zero findings:

  ```powershell
  & ".venv\Scripts\python.exe" -m project_copilot.release_guard .
  ```

- [ ] Gitleaks passes on full history for the exact commit.
- [ ] License files, notices, dependency licenses, and public icons/assets were
  reviewed.
- [ ] No new license condition conflicts with Apache-2.0 distribution or the
  intended company use.

Evidence: `REPLACE`

## B. Research and architecture decision

- [ ] Current Web/GitHub research is recorded in
  `docs/research/2026-07-15-v2-framework-selection.md` and
  `docs/research/2026-07-15-recent-rag-shortlist.md` (or newer dated evidence).
- [ ] Community health, license, Windows/offline support, company
  OpenAI-compatible support, attack surface, and implementation cost were
  compared.
- [ ] ADR 002 remains accepted or a newer ADR supersedes it.
- [ ] ADR 003's deterministic defrost-engine/AI-explanation boundary remains
  accepted; the current public corpus is visibly `synthetic_demo`, not OEM
  compliance evidence.
- [ ] V2 rejects uploaded `event_reconstruction` and `oem_exact` scopes until an
  external approval manifest and immutable evidence-hash binding exist.
- [ ] The default is clearly the embedded Workbench; optional LightRAG is not
  presented as integrated.
- [ ] Container runtime remains unapproved while the app is loopback-only, or a
  newer reviewed ADR and tests explicitly approve it.
- [ ] No framework/tool was selected from popularity alone.

Evidence: `REPLACE`

## C. Fresh local verification

- [ ] Locked dependencies install successfully on Python 3.12.
- [ ] Ruff check passes with zero errors.
- [ ] Ruff format check passes.
- [ ] All unit and integration tests pass with zero failures/errors.
- [ ] SQL mutation/security tests pass.
- [ ] Workspace isolation, archive safety, import lifecycle, Agent budget,
  refusal, and no-egress tests pass.
- [ ] Public release scanner passes.

Run the full gate and attach the complete output:

```powershell
scripts\verify.cmd
```

Record exact summary: `REPLACE tests passed, REPLACE skipped, 0 failed`.

Evidence: `REPLACE`

## D. Wheel, offline bundle, dependency, and SBOM

- [ ] Wheel builds from the exact commit.
- [ ] Wheel metadata reports the expected name/version/license and includes the
  synthetic demo plus static/templates/license files.
- [ ] A clean venv installs runtime dependencies from the offline wheelhouse
  using `--no-index` and the hash-locked runtime file.
- [ ] The application wheel installs with `--no-deps` from the received
  artifact.
- [ ] `pip check` passes.
- [ ] Wheel smoke `/api/health` returns HTTP 200 and the synthetic project ID.
- [ ] `pip-audit -r requirements.runtime.lock --strict` passes or every finding
  has a written risk acceptance.
- [ ] CycloneDX SBOM is generated from the smoke environment and retained.
- [ ] Artifact SHA-256 manifest verifies on the company PC.
- [ ] Any Office/PDF Docling bundle is separately hashed and proves parsing in
  a blocked-network VM; otherwise those formats are explicitly disabled.
- [ ] The parser dependency wheelhouse is built from
  `requirements.documents.lock` with `--require-hashes` for the target Windows
  Python/architecture.
- [ ] PDF parsing is supplied both an immutable local tokenizer and Docling
  artifacts directory, runs with remote services/model lookup disabled, and
  preserves page/section metadata after application restart.
- [ ] No runtime dependency falls back to source compilation on the restricted
  Windows PC.

Reference commands are in
[company deployment](company-deployment-v2.md#2-build-the-release-on-the-personal-pc).

Evidence: `REPLACE`

## E. Company endpoint, secret, TLS, and allowlist

- [ ] `PROJECT_COPILOT_MODEL_MODE=company` is used only for the approved
  production/test endpoint; deterministic mode is visibly labeled.
- [ ] The base URL uses HTTPS unless it is loopback.
- [ ] The exact base hostname is present in `PROJECT_COPILOT_ALLOWED_HOSTS`; no
  wildcard or broad parent domain is used.
- [ ] The model/deployment identifier is approved and recorded outside the
  public repository.
- [ ] The API key is injected by an approved vault/service launcher and is not
  printed, logged, committed, or stored in command arguments.
- [ ] Internal CA bundle exists, its hash is recorded, and certificate chain,
  hostname, expiry, and system clock checks pass.
- [ ] TLS verification has not been disabled.
- [ ] Environment proxy inheritance is understood to be disabled. If a proxy
  is mandatory, an approved internal API gateway is configured as the base
  endpoint.
- [ ] A failed/unauthorized endpoint, missing key, missing model, HTTP
  non-loopback URL, and non-allowlisted host all fail closed.

Evidence: `REPLACE`

## F. Firewall, telemetry, proxy, and no-egress

- [ ] `HAYSTACK_TELEMETRY_ENABLED=False` is present in the service environment.
- [ ] `PROJECT_COPILOT_KNOWLEDGE_PROVIDER=local` unless the separate downstream
  provider approval gate is intentionally accepted.
- [ ] Public model APIs, package indexes, code hosting, Web search, MCP,
  telemetry collectors, and unapproved connectors are denied to the runtime
  process/account.
- [ ] Deterministic synthetic startup/import/query/analytics/refusal produces no
  application egress.
- [ ] Company mode produces only the approved model destination and documented
  name-resolution traffic.
- [ ] Firewall/proxy logs and packet capture are correlated to the app PID in
  an isolated VM; unrelated machine traffic is excluded or identified.
- [ ] `tests/test_zero_egress.py` passes.
- [ ] Network evidence is retained with the release record.

Evidence: `REPLACE`

## G. Workspace creation and Project Package import

- [ ] A new user can start the app with the documented commands.
- [ ] The user can create a workspace in the UI with a valid project ID and
  display name.
- [ ] The user can open/switch between at least two workspaces.
- [ ] The synthetic Project Package imports through the UI without using a
  developer-only API.
- [ ] Individual uploads cover all six categories: `background`,
  `configuration`, `meeting`, `SOP`, `decision`, and `dataset`.
- [ ] A safe ZIP Project Package/source archive imports successfully.
- [ ] Traversal, symlink, duplicate basename, unsupported extension, file-count,
  file-size, and archive-size violations are rejected.
- [ ] The inventory shows source ID, filename, category, status, SHA-256,
  parser, size, and error.
- [ ] Required sources have `status=indexed`; parser/index errors remain visible
  and auditable.
- [ ] Re-index returns a recorded chunk count and preserves the expected source
  inventory.
- [ ] Delete removes one source, rebuilds the index, and prevents later citation
  of the deleted source.
- [ ] The CLI create/import/list/re-index entry points work with a runtime path
  outside the repository.
- [ ] Real Project Packages are not copied to the repository or public CI.

Evidence: `REPLACE`

## H. Primary Agent/model workflow

- [ ] The primary `/api/workspaces/{project_id}/copilot/query` workflow uses the configured company
  OpenAI-compatible test endpoint or deterministic test double through the same
  Haystack Agent/tool boundary.
- [ ] The company model path is exercised by an integration test; the API key
  and endpoint are not exposed to the browser.
- [ ] Tool schemas are strict and limited to project search, configuration,
  meeting/decision lookup, governed analytics, source inspection, and
  clarification.
- [ ] Agent limits are enforced: maximum steps, maximum tool invocations, and
  wall-time budget.
- [ ] The visible trace contains tool/status/summary only and does not expose
  hidden chain-of-thought.
- [ ] Shell, PowerShell, Python/code, unrestricted files, Web, MCP, unrestricted
  SQL, and physical equipment control are absent/refused.
- [ ] Tool failure is visible and does not silently become an unsupported
  answer.

Evidence: `REPLACE`

## I. Retrieval, grounding, and answer evaluation

Run the frozen synthetic HVAC evaluation set and retain raw per-case JSON. Do
not fill this section with an unmeasured percentage.

- [ ] Exact configuration lookup returns the correct value and citation.
- [ ] Cross-document synthesis uses all required sources.
- [ ] A temporal meeting/decision question selects the correct effective event
  and cites it.
- [ ] A configuration conflict exposes both values/sources and asks for or uses
  the approved effective-date rule.
- [ ] A combined knowledge+telemetry question uses both retrieval and governed
  analytics.
- [ ] Missing evidence produces a refusal or clarification, not a guess.
- [ ] Hostile/prompt-injection content cannot obtain a prohibited tool or
  override source/SQL/network policy.
- [ ] Citations map to exact imported source IDs and useful excerpts; page/
  section is shown when the parser provides it.
- [ ] Deleted and cross-workspace sources never appear in results.
- [ ] Retrieval hit, citation coverage/correctness, answer correctness,
  tool-selection success, refusal success, and latency are recorded per case.
- [ ] p50/p95 latency, failures, model calls, and index time are measured on the
  stated Windows/model configuration.

Evidence/results path: `REPLACE`

## J. Governed analytics

- [ ] Approved CSV telemetry passes Pandera/Polars schema validation.
- [ ] Invalid schema/types/units are rejected.
- [ ] DuckDB snapshot replacement is atomic and locked.
- [ ] Query connection is read-only.
- [ ] SQLGlot policy accepts one bounded SELECT only and enforces table/row
  limits.
- [ ] Mutation, attach, extension, copy/export, external file readers, multiple
  statements, and disallowed tables/functions are rejected.
- [ ] The Agent selects only a typed allowlisted analytics operation; it does
  not execute model-generated SQL.
- [ ] Latest reading, peak load, efficiency/COP, power, and temperature delta
  operations return expected synthetic results.
- [ ] No-dataset workspace returns a clear `Dataset required` result.

Evidence: `REPLACE`

### Defrost temporal diagnostics

- [ ] The imported rule pack records asset, controller model, firmware,
  rule/version, source file/section, timezone, sample interval, tolerances, and
  `compliance_scope`.
- [ ] A compliant synthetic window returns zero violations and the expected
  candidate/defrost/recovery transitions.
- [ ] The deliberately non-compliant window reports the expected first
  deviation, violation codes, and observed values.
- [ ] Duplicate timestamps, data-quality failures, and out-of-tolerance gaps
  return `insufficient_data` rather than a compliance verdict.
- [ ] A rule that depends on timing finer than the available sample interval is
  recorded as unobservable or blocked; it is never inferred from a ten-second
  sample.
- [ ] The answer cites the exact imported control-sequence/rule source and does
  not promote a confirmed rule violation into an unsupported physical root
  cause.
- [ ] The Agent accepts only an asset and bounded start/end window and cannot
  use the workflow to control equipment or execute arbitrary code/SQL.

Evidence: `REPLACE`

## K. Browser acceptance and screenshots

- [ ] Chromium is installed for the test environment.
- [ ] A fresh local server is started on `127.0.0.1:8788` with a disposable
  runtime.
- [ ] Desktop flow creates a workspace, uploads a decision source, inventories
  it, asks a cited question, and shows `meeting_decision_lookup` activity.
- [ ] Desktop screenshot is refreshed at
  `docs/assets/workbench-desktop.png` or retained as the CI artifact selected
  for release.
- [ ] Mobile viewport is 390×844, has no horizontal overflow, and primary
  navigation/Ask project flow remain usable.
- [ ] Mobile screenshot is refreshed at
  `docs/assets/workbench-mobile.png` or retained as the CI artifact selected
  for release.
- [ ] Browser console has zero errors.
- [ ] State-changing API requests without `X-Project-Copilot: 1` are rejected.
- [ ] Security headers include CSP, no-sniff, frame deny, no-referrer, and
  no-store.

Example local run:

```powershell
& ".venv\Scripts\python.exe" -m playwright install chromium
$env:PROJECT_COPILOT_BROWSER_URL = "http://127.0.0.1:8788"
$env:PROJECT_COPILOT_SCREENSHOT_DIR = "$PWD\artifacts\browser-acceptance"
& ".venv\Scripts\python.exe" -m pytest `
  tests\test_browser_acceptance.py -q
Copy-Item "$env:PROJECT_COPILOT_SCREENSHOT_DIR\workbench-desktop.png" `
  "docs\assets\workbench-desktop.png"
Copy-Item "$env:PROJECT_COPILOT_SCREENSHOT_DIR\workbench-mobile.png" `
  "docs\assets\workbench-mobile.png"
```

Evidence: `REPLACE`

## L. Operations, backup, restore, migration, and rollback

- [ ] Application, runtime, Project Package, logs, backups, and secrets use
  separate directories/ACLs.
- [ ] Process/access logs are captured and rotated without request bodies,
  imported documents, authorization headers, or secrets.
- [ ] The limitation that `0.2.0` has no durable multi-user query audit ledger
  is accepted or tracked as a blocker.
- [ ] Backup is taken with the app stopped and includes Project Package,
  runtime, configuration-without-secrets, release/hash evidence, and required
  audit records.
- [ ] Backup archives have verified hashes and approved encryption/retention.
- [ ] Restore is performed into a new directory and passes health, inventory,
  re-index, cited query, analytics, and refusal tests.
- [ ] Migration to another Windows PC is rehearsed without copying plaintext
  secrets.
- [ ] Upgrade uses a new venv and copied runtime, not in-place mutation.
- [ ] Rollback restores the previous binary plus matching pre-upgrade runtime
  snapshot and passes the smoke gate.
- [ ] Disk-full, expired certificate, unavailable model endpoint, parser error,
  and interrupted import/delete troubleshooting paths are rehearsed or owned.

Evidence: `REPLACE`

## M. Optional LightRAG A/B gate

Mark this section `[-]` when LightRAG is not being evaluated. It is never a
mandatory dependency for V2.

- [ ] The run uses only the public CC0 synthetic corpus on loopback; no company
  document, telemetry, endpoint, credential or model identifier enters v1.5.4.
- [ ] Version is pinned to `v1.5.4`; source commit, PyPI wheel hash, and/or GHCR
  digest match [the direct-deploy profile](light-rag-direct-deploy.md).
- [ ] The release owner acknowledges that v1.5.5rc1 first contains the reviewed
  authentication/disclosure/path/container fixes, and no company-data adoption
  can proceed until those fixes reach a stable version and pass a fresh review.
- [ ] One process/storage/input/port/account/API key is used for one workspace.
- [ ] Both account authentication and API key are enabled;
  `WHITELIST_PATHS=` is empty.
- [ ] Reverse proxy/adapter exposes only approved upload/status/inventory/query/
  delete routes.
- [ ] `mode=bypass`, WebUI, graph mutation, Web, MCP, code/shell, optional
  external evaluation, and Langfuse are unavailable.
- [ ] Project Package documents upload one by one; telemetry stays in governed
  DuckDB.
- [ ] Upload track/status, paginated inventory, `mix`/`hybrid` query with chunk
  references, async delete, and delete+re-upload re-index mapping pass.
- [ ] Windows offline install/container transfer, internal CA, firewall,
  no-egress, backup/restore, and rollback pass.
- [ ] Same corpus/model/machine/limits are used for embedded and LightRAG runs.
- [ ] No regression occurs on exact lookup, conflict, clarification, refusal, or
  hostile input.
- [ ] At least one additional cross-document/temporal case is fully correct
  with correct citations; a tie keeps embedded mode.
- [ ] Citation coverage/correctness does not decline; latency/index/storage/model
  call evidence stays within approved budgets.
- [ ] A separate adapter ADR and independent security review are accepted before
  any product integration.

Evidence: `REPLACE`

## N. Independent review

- [ ] Reviewer is independent of the implementation work.
- [ ] Reviewer receives the handoff, ADR/research, diff, threat model, test
  output, evaluation results, release artifacts, and deployment docs.
- [ ] Reviewer checks correctness, security, public-data boundary, deployment
  executability, documentation accuracy, and acceptance evidence.
- [ ] No release-blocking Critical or Important finding remains.
- [ ] Every accepted lower-severity finding has owner/rationale/follow-up.
- [ ] After fixes, the complete verification and affected acceptance cases are
  rerun from scratch.

Reviewer report: `REPLACE`

## O. Commit, push, CI, and trial server

- [ ] All intended changes are committed in a new commit; published history is
  not amended.
- [ ] Push succeeds without force-push.
- [ ] GitHub CI is green for Windows/Linux tests, package/wheel/SBOM, browser,
  dependency audit, release scanner, and Gitleaks.
- [ ] Remote commit equals the locally accepted commit.
- [ ] The final local trial server runs the accepted commit with synthetic data
  and a disposable runtime for Chairman review.
- [ ] Trial URL is loopback unless a separately approved authenticated tunnel/
  proxy is explicitly in scope.
- [ ] Final handoff records commit, CI URLs, trial health URL, known limitations,
  evidence paths, and next research directions.

Evidence: `REPLACE`

## Final release decision

- [ ] **ACCEPT** — all mandatory sections pass; no Critical/Important finding;
  production conditions and limitations are signed.
- [ ] **REJECT/BLOCK** — failed item(s): `REPLACE`.

Approver/date/signature reference: `REPLACE`
