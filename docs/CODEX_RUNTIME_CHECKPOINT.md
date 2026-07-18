# Codex runtime comparison checkpoint

Last updated: 2026-07-18 16:10 (Asia/Shanghai)

The authoritative scope and status are in `docs/CODEX_RUNTIME_TASK_LEDGER.json`.

## Outcome

The direction is validated: the thin Web can use the full official Codex agent
instead of recreating a weaker RAG loop. In the synthetic complex MX01-style
case, Codex combined data-quality and COP operations with configuration,
meeting, change and service evidence and returned the expected ranking and
caveats. The previous shared backend refused its equivalent result.

The first implementation was not safe enough to merge. Independent review
correctly found unrestricted reads, arbitrary DuckDB access, a shared writable
workspace, raw thread IDs, false upload/workspace claims, weak JSONL grounding,
stderr leakage and false health telemetry.

## Current architecture

- Each request creates a fresh session and runs Codex with `--ephemeral`.
- A custom Permission Profile grants minimal runtime reads, read-only access to
  that request's copied documents, temporary writes, and no command network.
- The native Windows profile requires the official `elevated` sandbox.
- DuckDB is outside the command sandbox. The official MCP Python SDK 1.28.1
  exposes only `schema`, `data_quality`, and `cop_ranking`.
- Final output is schema-constrained JSON. Citations are accepted only when the
  filename exists and the excerpt is an exact substring of that source; the
  virtual telemetry citation requires a non-empty result from `data_quality`
  or `cop_ranking`. A schema lookup alone is not telemetry evidence.
- Codex mode does not initialize the legacy embedding or reranking stack.
- The Web service refuses to start until an operator preflight proves both that
  the copied workspace is readable and the private DuckDB file is denied by the
  elevated sandbox. The pass marker is bound to the selected Codex executable.
- No Codex thread ID is returned to the browser. Conversation continuity uses
  the compact last six UI turns.
- Codex mode is explicitly the fixed `Agentic HVAC Bakeoff`, reports 11 evidence
  files, hides upload, and returns HTTP 409 if upload is attempted.
- Haystack remains the default runtime and is unchanged.

## Verification evidence

- `tests/test_codex_runtime.py`: 19 passed.
- Runtime + direction + Web focused regressions: 55 passed.
- Targeted browser acceptance: passed.
- Ruff on changed Python: passed.
- Official MCP STDIO smoke: tools were exactly `schema`, `data_quality`, and
  `cop_ranking`; `cop_ranking` returned HP-03 at 4.001643.
- Codex `mcp list` recognized the generated required server and redacted its
  environment values.
- Final independent rereview found no Critical or Important code findings; it
  remains not merge-ready only because real elevated-sandbox acceptance is an
  external approval blocker.

## Blocker

Secure real-model acceptance is blocked because this PC has not completed the
official elevated Windows sandbox setup. Restricted-read Permission Profiles
are rejected by the unelevated backend. The elevated setup helper requires an
administrator prompt and changes dedicated sandbox users, ACLs, firewall rules,
private-desktop behavior and local policy. It must not be enabled without
explicit Chairman/company-IT approval.

Do not restart the old workspace-write 8790 proof as a company-data service.
It is architecture evidence only.

## Next action after approval

1. Run the official elevated sandbox setup after approval and accept the
   one-time UAC change.
2. Start with `scripts/run-codex.ps1`; its automatic allow/deny preflight must
   pass before the Web process is launched.
3. Run one secure real-model MX01 request.
4. Obtain an independent final rereview.
5. Commit and push only if the elevated profile fails closed on outside reads,
   all tests remain green, and no Critical/Important review item remains.
