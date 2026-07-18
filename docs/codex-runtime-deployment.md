# Codex runtime evaluation profile

This profile proves the product direction **thin Web UI + official Codex agent +
governed data tools**. It is intentionally limited to the committed synthetic
HVAC bakeoff. It is not yet an active-workspace or company-data deployment.

> **Current Windows result (2026-07-18): blocked.** The authorized elevated
> setup completed, but the sandbox account could still read application/root
> repository files on an ordinarily readable host drive. The startup preflight
> now tests that path and refuses to start. Do not bypass it. WSL2 Landlock
> passed the equivalent no-model negative read probe and is the pending
> production-backend replacement.

## What this profile does

```text
single Chat / named workflow button
        -> FastAPI adapter
        -> fresh per-request evidence session
        -> official Codex CLI 0.144.5
        -> elevated Windows permission profile
        -> exact project documents (read only)
        -> required official MCP server
        -> three fixed DuckDB operations
```

The browser never receives the company API key, a Codex thread ID, a database
path, SQL text, or a writable project path. Every request gets a new workspace.
The DuckDB file remains outside the command sandbox and is accessible only to
the MCP server process.

The only data tools are:

- `schema`
- `data_quality`
- `cop_ranking`

The MCP schema accepts no SQL and no filesystem path from the model. All three
operations open DuckDB read-only with external access, extension installation,
temporary spill, and community extensions disabled.

## Current acceptance boundary

- The profile name shown in the page is `Agentic HVAC Bakeoff`.
- It contains ten human-readable source files plus the virtual
  `telemetry.csv` evidence label.
- Upload is hidden and the upload API returns HTTP 409 in Codex mode.
- The legacy Haystack mode remains the default and retains ordinary workspace
  upload behavior.
- Do not use this Codex profile with company files until active-workspace
  snapshot isolation, per-user identity, retention, and company security review
  are completed.

## Prerequisites

1. Windows 11, fully updated, Python 3.12, and Node.js 18 or newer.
2. A company-approved Codex configuration using an HTTPS Responses endpoint.
3. Administrator approval for the **elevated native Windows sandbox**.
4. A runtime directory outside the source repository, for example
   `D:\ProjectCopilotCodex`.

Restricted read roots require Codex's elevated sandbox. The unelevated backend
must not be used as a silent fallback. If the elevated helper is unavailable or
blocked by enterprise policy, Codex analysis must fail closed; use WSL2 with
the official Linux sandbox only after a separate company review.

## Install

From the repository root on an online Windows preparation machine:

```powershell
scripts\bootstrap.ps1
scripts\bootstrap-codex-runtime.ps1 `
  -RuntimeRoot D:\ProjectCopilotCodex\official-runtime
```

The second script installs `@openai/codex@0.144.5`, finds its native
`codex.exe`, and runs a version smoke test. It does not create, copy, or print an
API credential. DuckDB is supplied by the hash-locked Python application; no
DuckDB CLI is exposed to the agent sandbox.

The Python runtime is pinned by `requirements.runtime.lock`, including the
official Model Context Protocol Python SDK `mcp==1.28.1`.

## Configure the approved provider

Use the company-managed Codex config, or a dedicated equivalent outside Git.
The application reads its selected provider only after
`PROJECT_COPILOT_ACK_CODEX_SWITCH=true`.

Minimum non-secret shape:

```toml
model = "COMPANY_APPROVED_MODEL"
model_provider = "company"

[model_providers.company]
name = "Company"
base_url = "https://approved.example/v1"
wire_api = "responses"
supports_websockets = false
```

Keep the bearer credential in the approved Codex auth store or company secret
manager. Never commit `config.toml`, `auth.json`, `.env`, copied tokens, or
runtime logs.

## Enable and verify the elevated sandbox

On the target PC, use the pinned official Codex executable and run:

```powershell
$codexExe = Get-ChildItem `
  -LiteralPath D:\ProjectCopilotCodex\official-runtime\node_modules `
  -Filter codex.exe -File -Recurse |
  Sort-Object FullName |
  Select-Object -First 1
& $codexExe.FullName sandbox setup --elevated --current-user
```

Approve the one-time administrator prompt only after company IT approves the
dedicated lower-privilege users, ACL boundaries, firewall rules, private
desktop, and local policy changes described by the official Codex Windows
sandbox documentation.

The generated runtime config requires:

```toml
[windows]
sandbox = "elevated"
sandbox_private_desktop = true
```

It also defines a custom permission profile with only:

- minimal operating-system/runtime reads;
- read access to the one request's copied document workspace;
- write access to the sandbox temporary directory;
- no command network access.

`scripts/run-codex.ps1` performs a fresh security preflight before every start.
It launches two commands through the generated `project-copilot` Permission
Profile: one must read the copied `AGENTS.md`, and one must receive an access
denial when attempting to read the private DuckDB file. Only then is
`elevated-sandbox-preflight.json` written under the private runtime root. The
marker is bound to the selected Codex executable. A stale marker is removed
before each probe.

If either probe fails, the Web service is not started. Do not change the
profile to `workspace-write`, `danger-full-access`, or a broad filesystem read
profile.

## Start the loopback evaluation site

```powershell
scripts\run-codex.ps1 `
  -ProjectPath .\examples\synthetic_hvac `
  -RuntimePath D:\ProjectCopilotCodex\app-runtime `
  -CodexRuntimeRoot D:\ProjectCopilotCodex\official-runtime `
  -CodexConfig C:\ApprovedSecrets\codex\config.toml `
  -Port 8790
```

The command first runs `project-copilot-codex-preflight`; startup stops with a
public-safe error if elevated isolation is unavailable or the private database
is readable. Open `http://127.0.0.1:8790` only after the preflight passes. Do
not bind the application to a LAN address or
place it behind a reverse proxy: this evaluation build has no multi-user
authentication.

## Acceptance checks

```powershell
$env:POLARS_SKIP_CPU_CHECK = "1" # only on CPUs that need the existing Polars workaround
.\.venv\Scripts\python.exe -m pytest -q tests\test_codex_runtime.py
.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_codex_runtime.py tests\test_direction_demo.py tests\test_web_v2.py
.\.venv\Scripts\ruff.exe check `
  src\project_copilot\codex_runtime.py `
  src\project_copilot\codex_mcp_server.py `
  src\project_copilot\codex_preflight.py `
  src\project_copilot\web.py `
  tests\test_codex_runtime.py
```

Then verify in the browser:

1. Header says `Agentic HVAC Bakeoff`, `11 个文件`, and `固定合成测试资料`.
2. No upload control is visible.
3. The configuration workflow distinguishes current, superseded, approved, and
   unapproved values.
4. The data-quality workflow invokes only the named MCP tools and returns the
   expected HP-03 / HP-02 / HP-04 / HP-01 COP order.
5. Citations show original filenames and exact source excerpts; unknown or
   fabricated citations fail with HTTP 503.
6. `GET /api/health` reports `agent_runtime=codex`, the approved remote egress,
   and no false loopback claim.

## Private runtime and retention

Each request writes a private session below:

```text
<runtime>\codex-agent\runs\<random>\
  workspace\          # copied synthetic documents only
  private-evidence\   # DuckDB; outside command read permissions
  codex-home\         # generated config and output schema
  events.jsonl        # private tool/event audit
  stderr.log          # private failure diagnostic
```

These files are not application source and must never enter Git or a release
archive. Before company rollout, define an IT-approved ACL, maximum storage,
retention interval, secure deletion procedure, and incident-access policy.

## Rollback

Stop the Codex-mode process and start the existing `scripts\run.ps1` profile.
The default Haystack runtime and its workspaces are unchanged. Do not copy any
Codex session directory into the Haystack runtime or vice versa.

## Evidence and current blocker

On 2026-07-18 the official MCP server was started through the official Python
client over STDIO; it exposed exactly the three tools above and returned
`HP-03 / 4.001643` for `cop_ranking`. Twenty-three Codex-runtime tests, fifty-five focused
Web regressions, the targeted browser acceptance, and Ruff passed.

Earlier synthetic-only probing with the company endpoint proved that the full
Codex agent can answer the complex multi-document plus data-quality/COP case,
where the previous backend refused the equivalent MX01 result. That probe used
the weaker workspace sandbox and is retained only as architecture evidence.

The Chairman authorized the elevated setup and UAC provisioning completed.
Real negative testing then showed that the sandbox account could still open
application/root-repository files on the ordinarily readable E: drive. Marker
schema version 2 now requires that outside-source read to be denied and rejects
all earlier two-probe markers, so the Windows wrapper remains blocked. WSL2
Landlock passed the equivalent no-model workspace/private/root probe and is the
pending replacement backend; secure real-model acceptance has not run.
