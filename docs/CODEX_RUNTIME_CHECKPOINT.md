# Codex runtime comparison checkpoint

Last updated: 2026-07-21 (Asia/Shanghai)

The authoritative scope and status are in `docs/CODEX_RUNTIME_TASK_LEDGER.json`.

## Outcome

The current product direction is one engineer-facing Chat page backed by the
official Codex Python SDK and Codex agent runtime. OpenCode remains a
replaceable alternative, not the default merely because it uses the MIT
license. Formal deployment must use a company API, Azure OpenAI, or an approved
Business/Enterprise identity; shared personal logins and shared API keys are
outside the design.

The reference site supplied by the Chairman is an Obsidian Publish site. Its
right-side miniature is a local graph and its expanded view is the full vault
graph. Project Copilot now reuses its already-vendored Cytoscape 3.34.0 instead
of adding a second graph renderer: the graph stays hidden during ordinary Chat,
appears only after evidence is used, shows the current evidence path in compact
mode, and expands to the complete project graph on demand.

## Current architecture

- `openai-codex==0.144.4` is the primary transport. This official Python SDK is
  explicitly marked **beta**, so the runtime seam remains replaceable. The
  earlier CLI JSONL adapter is still available only through the explicit
  `PROJECT_COPILOT_CODEX_TRANSPORT=cli-jsonl` switch; there is no silent
  fallback.
- The SDK worker starts the pinned bundled Codex App Server, denies approvals,
  uses an ephemeral thread, constrains final output with a JSON schema, and
  receives credentials only through a controlled process environment.
- Each request creates a fresh evidence session. The command sandbox receives
  copied documents but not the private DuckDB directory.
- The required official MCP server exposes nine bounded read-only tools:
  `schema`, `data_quality`, `cop_ranking`, `search_project_knowledge`, `query_hvac_database`,
  `inspect_hvac_snapshot`, `inspect_configuration_history`,
  `inspect_configuration_change_effect`, and `inspect_metric_extreme`.
- `query_hvac_database` uses the existing SQL guard. It allows one bounded flat
  `SELECT` only and rejects writes, file access, star projection, CTEs,
  subqueries and unbounded result sets. The typed tools remain preferred.
- Final output supports readable Markdown, bounded tables, bounded line/bar
  charts, exact human-readable filenames and validated excerpts. Governed tool
  results override unsupported model-authored table/chart values.
- The browser never receives an API key, database path, SQL text, Codex thread
  ID, private chain-of-thought or writable project path.
- Codex mode does not initialize the legacy embedding/reranking stack. The old
  Haystack/DuckDB site and the synthetic HVAC corpus remain comparison assets.

## OpenCode continuation (2026-07-21)

- Official OpenCode SDK/provider/MCP/permission documentation and GitHub API
  evidence were refreshed. OpenCode v1.18.3 is a replaceable MIT alternative;
  no quality parity is inferred from popularity or license.
- Existing private simple and first-complex payloads replayed through the
  current strict adapter before new model calls. Sanitized evidence is in
  `evaluation/results/opencode-existing-payload-replay-20260721.json`; raw
  `.opencode-live` payloads remain local and unpublished.
- TDD fixed the OpenCode 1.18.3 v1 SDK path/query/body mismatch, preserved
  numeric grounding from non-displayable wide tables, bounded event tables to
  12 columns and aggregate output to four tables/charts, and removed the
  duplicate pytz dependency.
- Frozen CC01 and CC02 xhigh OpenCode cases completed on the synthetic corpus.
  CC01 measured 194.939 seconds with three tools and six citations; CC02
  measured 151.046 seconds with three tools and six citations and correctly
  refused write/control. CC02's stale gold assumptions are recorded in the
  adjudication rather than applied as hindsight penalties.
- A live Codex control remains blocked by the documented native-Windows
  isolation gate. No OpenCode-versus-Codex quality winner or parity claim is
  made. Both blocked controls now have machine-readable artifacts recording
  that no Codex model call started.

## Verification evidence

- `tests/test_codex_runtime.py`: **44 passed**. This includes an actual SDK
  process starting the bundled Codex App Server against a local mock Responses
  stream, plus an official MCP STDIO lifecycle that discovers all nine tools
  and executes a configuration-change query.
- Runtime + direction + Web focused suite: **80 passed**; the frozen
  complex-question/evaluator contract adds **10 passed**, for **90 focused
  tests** in the final combined run.
- Browser acceptance: **10 passed, 1 skipped**. The skipped case is the
  intentionally gated real-model browser journey; it is not counted as model
  quality evidence.
- Frozen complex-question/evaluator contract tests: **9 passed**.
- Browser checks prove the composer remains fixed, the graph is initially
  hidden, evidence activates the compact local graph, full-screen expand and
  restore work, and human filenames remain visible.
- Ruff check and format check on changed Python: passed.
- The hash-locked bootstrap completed and reported `codex-cli 0.144.4`; `pip
  check` found no broken requirements. A clean wheel build completed and
  contained the SDK worker, MCP server, graph JavaScript and Chat template.
- Frozen old-backend nine-case subset: hard gate **1/9**, human review
  **0 pass / 3 partial / 6 fail**. This remains the comparison baseline, not a
  score for the SDK path.

Successive independent reviews found no Critical issue and drove all Important
findings to closure; the final rereview returned **Ready: Yes**. The accepted
fixes now bind document and virtual/data
citations to the exact MCP result, never treat numbers from the user's question
as proof, reject unsupported numeric
claims and presentations, reject every Shell/file-change/Web-search event,
force App Server `--strict-config`, redact internal paths before the API
response (including citation metadata), share canonical graph/citation
locations, highlight only the exactly cited dataset, publish constrained MCP
schemas with optional presentation parameters, disable legacy remote APIs in
fixed Agent modes, exclude discarded citation metadata from numeric grounding,
and fail closed if Windows descendant process-tree cleanup cannot be verified.

The final release gate passed Ruff check/format, **449 tests passed, 14
intentional tests skipped**, and the release guard passed. The shared runtime,
development and document locks retain MCP's `pywin32` dependency behind an
explicit Windows platform marker, so Ubuntu CI does not attempt to install a
Windows-only wheel.

## Honest benchmark boundary

The SDK/App Server/MCP contract is executable and verified locally, but a full
nine-case real-model SDK benchmark is not claimed on native Windows. The
authorized elevated Windows sandbox still inherits ordinary read permission
to unrelated files on the E: drive. Running the model despite that result
would violate the product boundary. Contract tests and the earlier weaker-
sandbox architecture probe must not be presented as production isolation or
as a real-model quality score.

An Ubuntu WSL2 Landlock probe denied both the private file and mounted root
repository while allowing the copied workspace. That validates the next
production-isolation architecture, but the Windows Web/MCP wrapper has not yet
been migrated to it.

## Remaining bounded work

The focused/full verification and independent rereview are complete. Commit and
push only the explicitly audited public-safe files without force, then keep the
loopback trial server available.

The live Codex quality control remains a separate fail-closed boundary: the
validated WSL2/Landlock integration must precede any real-model Codex parity
campaign. It must not be represented as completed by this synthetic SDK or
OpenCode evidence.

Do not restart the old workspace-write 8790 proof as a company-data service. It
is architecture evidence only.
