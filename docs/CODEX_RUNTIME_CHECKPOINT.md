# Codex runtime comparison checkpoint

Last updated: 2026-07-18 18:30 (Asia/Shanghai)

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
- Windows sessions add an explicit deny-read ACL for `CodexSandboxUsers` to
  each private DuckDB directory. The startup probe also requires application
  source outside the copied workspace to be unreadable.
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

- `tests/test_codex_runtime.py`: 23 passed.
- Runtime + direction + Web focused regressions: 55 passed.
- Targeted browser acceptance: passed.
- Ruff on changed Python: passed.
- Official MCP STDIO smoke: tools were exactly `schema`, `data_quality`, and
  `cop_ranking`; `cop_ranking` returned HP-03 at 4.001643.
- Codex `mcp list` recognized the generated required server and redacted its
  environment values.
- A later post-UAC rereview invalidated the earlier clear verdict: legacy
  two-probe readiness markers could bypass the new outside-source check. Marker
  schema version 2 now rejects every older marker. No merge-ready claim remains.

## Blocker

The Chairman authorized the official elevated Windows setup and the UAC install
completed successfully. The real negative test then proved that
`CodexSandboxOffline` still inherits ordinary `Users/Everyone` read access to
the E: drive: it could open application/root-repository source outside the
copied workspace. The private DuckDB becomes unreadable only after an explicit
deny ACL. Therefore the Windows backend remains fail-closed and must not run a
real model against company files.

An existing Ubuntu WSL2 environment was probed with the official Codex 0.144.5
Linux binary. Its Landlock profile produced `allowed=0`, `private=1`, and
`root-repository=1`: the copied workspace was readable while both outside paths
were denied. This validates WSL2 as the next isolation architecture, but it is
not yet integrated with the Windows Web/MCP runtime.

Do not restart the old workspace-write 8790 proof as a company-data service.
It is architecture evidence only.

## Next action

1. Obtain Chairman approval to replace the Windows execution backend with the
   validated WSL2/Landlock backend.
2. Keep the Windows Web adapter and governed MCP broker, but launch the official
   Linux Codex binary inside WSL2 with translated read-only paths.
3. Repeat workspace/private/root negative reads through the production wrapper.
4. Only then run one secure real-model MX01 request and browser acceptance.
