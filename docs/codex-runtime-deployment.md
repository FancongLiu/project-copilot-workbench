# Codex SDK runtime: company deployment and acceptance handoff

This profile implements the selected product shape:

```text
engineer browser
  -> one thin Project Copilot Chat page
  -> local/company FastAPI adapter
  -> official openai-codex Python SDK 0.144.4 (beta)
  -> pinned bundled Codex App Server
  -> company-approved Responses endpoint
  -> copied read-only project documents
  -> required official MCP server
  -> typed/guarded read-only DuckDB operations
```

It is deliberately not a new weak RAG chatbot. Codex remains the planning and
reasoning runtime; Project Copilot supplies the company boundary, approved
tools, presentation contract and engineer-facing shell.

> **Native Windows company-data status (2026-07-19): fail-closed.** The
> authorized elevated sandbox setup completed, but the sandbox account could
> still read unrelated application/root-repository files on an ordinarily
> readable E: drive. `scripts/run-codex.ps1` therefore refuses to start. Do not
> bypass the preflight. WSL2 Landlock passed the equivalent workspace/private/
> root negative-read probe and is the pending production isolation backend.

## 1. What is already runnable

The committed synthetic HVAC profile has automated vertical coverage for:

- the actual Python SDK starting its bundled Codex App Server;
- a controlled OpenAI-compatible Responses stream;
- official MCP initialization, tool discovery and tool calls over STDIO;
- knowledge citations with exact original filenames and excerpts;
- structured data quality, COP, snapshot, configuration history/change and
  metric-extreme queries;
- one guarded flat read-only SQL escape hatch;
- Markdown, bounded tables, bounded line/bar charts and evidence activities;
- a compact Obsidian-style evidence graph with optional full-project expand;
- browser layout, fixed composer and mobile/desktop behavior.

The local mock Responses integration proves protocol wiring, not model answer
quality. The earlier real endpoint probe used a weaker sandbox and is retained
only as architecture evidence. No production-quality SDK benchmark score is
claimed until the isolation gate passes.

## 2. User-facing behavior

The engineer sees one page:

- free natural-language input;
- a few high-frequency workflow buttons;
- readable Markdown rather than raw technical output;
- optional governed tables/charts;
- citations headed by familiar original filenames;
- an initially hidden evidence map.

After an answer uses documents or data tools, the evidence map appears as a
small local graph. It shows only the current query path, cited files and data
nodes. The expand control opens the full project graph; restore returns to the
small view. Closing it does not affect the answer. This mirrors the useful part
of Obsidian Publish without making the graph the main workflow.

## 3. Read-only knowledge and data tools

The required MCP server exposes nine tools:

| Tool | Purpose |
|---|---|
| `schema` | Discover approved telemetry fields. |
| `data_quality` | Find missing, frozen, duplicate or out-of-order readings. |
| `cop_ranking` | Rank units by load-weighted COP for a bounded time window. |
| `search_project_knowledge` | Search approved documents and return exact original filenames/excerpts. |
| `inspect_hvac_snapshot` | Inspect a bounded telemetry snapshot. |
| `inspect_configuration_history` | Read effective/superseded configuration history. |
| `inspect_configuration_change_effect` | Compare approved before/after settings and evidence. |
| `inspect_metric_extreme` | Find bounded extrema for an approved metric. |
| `query_hvac_database` | Execute one guarded flat `SELECT` when typed tools are insufficient. |

Typed tools are preferred. `query_hvac_database` rejects writes, file/system
access, star projection, CTEs, subqueries, multiple statements and unbounded
results. DuckDB is opened read-only with external access, extension install,
temporary spill and community extensions disabled.

The Agent is not allowed to execute Shell, PowerShell, Python, file-change or
Web-search events. Project documents are inspected through
`search_project_knowledge`. App Server is launched with `--strict-config`, so
unknown security/MCP/network fields abort startup. Virtual data citations and
model-authored numeric claims/tables/charts are accepted only when they match
the exact governed tool result or cited excerpt. Public answer/table/chart
strings are path-redacted on the server before the JSON response.

## 4. Install on a preparation machine

Prerequisites:

1. Windows 11 or an IT-approved WSL2 host, Python 3.12 and PowerShell.
2. A company-approved OpenAI-compatible Responses endpoint.
3. A runtime directory outside the source repository.
4. Company approval for the isolation and credential pattern.

From the repository root:

```powershell
scripts\bootstrap.ps1
scripts\bootstrap-codex-runtime.ps1
```

If local PowerShell execution policy blocks scripts, do not weaken the machine
or domain policy. Have company IT approve/sign the scripts, or for an explicitly
authorized one-process evaluation invoke the file with
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File`. The current machine
passed the bootstrap through that process-scoped route.

The second script installs `requirements.codex.lock` with `--require-hashes`.
That lock pins `openai-codex==0.144.4` and its bundled Windows Codex runtime.
The script locates the bundled executable with `codex_cli_bin`, runs
`codex --version`, and prints its path. It never creates, copies or prints a
credential.

The SDK is currently **beta**, so upgrades are explicit: research the current
official release, update `requirements.codex.in`, regenerate the hash lock,
run all acceptance tests, and only then change the pin.

## 5. Configure the approved provider

Create the Codex configuration outside Git, for example
`C:\ApprovedSecrets\codex\config.toml`:

```toml
model = "COMPANY_APPROVED_MODEL"
model_provider = "company"

[model_providers.company]
name = "Company"
base_url = "https://approved.example/v1"
wire_api = "responses"
supports_websockets = false
```

Keep the bearer credential in the approved Codex auth store, Azure/company
secret manager, or a process environment supplied by the approved launcher.
Never commit `config.toml`, `auth.json`, `.env`, copied tokens or runtime logs.
The browser never receives the credential. One shared personal account/API key
for multiple employees is explicitly unsupported.

Azure OpenAI can be used only when the selected model and API surface support
the Responses behavior required by the current Codex runtime. Treat provider
compatibility as an executable contract test, not a name-only assumption.

## 6. Native Windows isolation gate

The generated runtime configuration requires:

```toml
[windows]
sandbox = "elevated"
sandbox_private_desktop = true
```

After company IT approval, run the one-time official setup using the executable
reported by `bootstrap-codex-runtime.ps1`:

```powershell
& $codexExe sandbox setup --elevated --current-user
```

`scripts/run-codex.ps1` then runs `project-copilot-codex-preflight` before
starting the Web service. The selected permission profile must:

1. read the request's copied `AGENTS.md`;
2. fail to read the private DuckDB file; and
3. fail to read application/root-repository source outside the copied
   workspace.

Only a fresh schema-v2 marker bound to the selected executable is accepted.
Any old marker or any failed negative read stops startup. Never weaken this to
`workspace-write`, `danger-full-access` or broad filesystem reads.

Current native Windows result: check 3 fails on the ordinary E: drive. This is
the documented blocker. The next production step is to integrate the already
validated WSL2/Landlock boundary and rerun all three checks plus a real model
turn before using company files.

## 7. Start after the isolation gate passes

```powershell
scripts\run-codex.ps1 `
  -ProjectPath .\examples\synthetic_hvac `
  -RuntimePath D:\ProjectCopilotCodex\app-runtime `
  -CodexConfig C:\ApprovedSecrets\codex\config.toml `
  -ReasoningEffort high `
  -Port 8790
```

The wrapper discovers the bundled SDK runtime and sets
`PROJECT_COPILOT_CODEX_TRANSPORT=python-sdk`. The legacy adapter is available
only through the explicit value `cli-jsonl`; unknown values fail and there is
no automatic fallback.

The evaluation server binds to `127.0.0.1`. Do not bind it to a LAN address or
reverse proxy until user authentication, authorization, request isolation,
rate limits and retention have passed company review.

## 8. Acceptance commands

```powershell
$env:POLARS_SKIP_CPU_CHECK = "1" # only for the known legacy CPU compatibility case

.\.venv\Scripts\python.exe -m pytest -q tests\test_codex_runtime.py
.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_codex_runtime.py `
  tests\test_direction_demo.py `
  tests\test_web_v2.py

$env:PROJECT_COPILOT_BROWSER_URL = "http://127.0.0.1:8790"
.\.venv\Scripts\python.exe -m pytest -q tests\test_browser_acceptance.py

.\.venv\Scripts\python.exe -m ruff check `
  src\project_copilot\codex_runtime.py `
  src\project_copilot\codex_sdk_worker.py `
  src\project_copilot\codex_mcp_server.py `
  src\project_copilot\web.py `
  tests\test_codex_runtime.py `
  tests\test_direction_demo.py `
  tests\test_browser_acceptance.py
```

Current verified counts after independent-review remediation:

Delivery commit: `cfa8dc8` on `codex/agentic-rag-bakeoff`, pushed without force.

- Codex runtime/MCP: 44 passed;
- runtime + direction + Web: 80 passed;
- frozen complex-question/evaluator contract: 10 passed;
- combined focused verification: 90 passed;
- browser: 10 passed, 1 real-model case intentionally skipped;
- Ruff check/format and `pip check`: passed;
- hash-locked bootstrap and clean wheel build: passed (`codex-cli 0.144.4`).

Independent review found no Critical issue. Successive Important findings were
fixed and covered by adversarial tests; the final rereview returned
`Ready: Yes` with no remaining Critical or Important item.
Document citations now require the knowledge tool, question numbers are not
proof, citation metadata is path-sanitized, and the evidence graph highlights
only exact cited locations. One Minor item remains: the outer
timeout terminates the SDK worker, but descendant App Server/MCP cleanup is not
yet proven on every supported operating system. Keep runtime retention and
orphan-process monitoring enabled until the WSL2 production wrapper adds an
OS-native process group/job boundary.

Manual browser acceptance:

1. Only one Chat workspace dominates the page.
2. The composer remains fixed while the answer history scrolls.
3. Answers render headings, lists, tables and charts cleanly.
4. Citations show original filenames, not internal index IDs or raw paths.
5. The evidence graph is hidden before use, appears after grounded activity,
   expands to a full map and restores without moving the composer.
6. Unknown/fabricated citations and unsupported chart/table values fail
   closed; no fake successful answer is shown.
7. Health output identifies the Codex runtime without exposing credentials or
   private paths.

## 9. Runtime storage and audit

Each request writes below the private runtime root:

```text
<runtime>\codex-agent\runs\<random>\
  workspace\          # copied approved documents
  private-evidence\   # DuckDB/manifest; denied to command sandbox
  codex-home\         # generated config and output schema
  events.jsonl        # private bounded tool/event audit
  stderr.log          # private diagnostic, never returned verbatim
```

These files must never enter Git or a release archive. Before multi-user
deployment, company IT must define per-user identity, ACLs, maximum storage,
retention, secure deletion, incident access and audit review.

## 10. Benchmark interpretation

The frozen nine-case old-backend subset is retained as the baseline:

- hard gate: 1/9;
- human review: 0 pass, 3 partial, 6 fail;
- six refusals and two raw-path leaks.

The SDK contract tests are not a replacement score. A fair real-model rerun
requires the same nine cases, same synthetic corpus, same approved endpoint and
three independent runs per case. Recommended acceptance is at least 8/9 hard
gate passes, MX13 mandatory, at least eight human passes plus one partial, and
3/3 consistency for MX04/MX11/MX13. Do not publish a score until the isolation
gate permits the model run.

## 11. Rollback

Stop the Codex-mode process and start the existing `scripts\run.ps1` profile.
The old Haystack runtime, its workspaces and the synthetic corpus are preserved.
Do not copy Codex session directories into the old runtime or vice versa. Do
not delete OnionQuant runtime, Cron, context, Inbox or Outbox state.
