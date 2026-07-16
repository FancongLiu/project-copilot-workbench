# Agentic RAG direction-build checkpoint

Last updated: 2026-07-16 23:10 (Asia/Shanghai)

## Objective

Rapidly produce a commercial-HVAC single-chat direction build that can search
project knowledge, query structured telemetry in natural language, combine both
evidence types, and render engineer-readable answers. The Chairman will trial
the direction build before deeper production hardening.

The authoritative state is `docs/AGENTIC_RAG_TASK_LEDGER.json`. The completed
V2 ledger remains historical evidence and is not overwritten.

## Current corrections

- Agentic RAG is the governing architecture. RAGFlow, WrenAI and DB-GPT are
  possible implementations or tools, not replacements for the architecture.
- RAGFlow plus WrenAI is only the leading quality hypothesis. DB-GPT is the
  simpler all-in-one challenger. Neither is selected as final until the same
  corpus, model and gold questions are run.
- The target is sufficient business capability, not theoretical optimality.
- Build the reproducible HVAC corpus and hidden truth before the next product
  implementation so failures can be classified rather than argued from demos.
- Defrost remains one bounded test case and must disappear from the primary UI.
- No silent downgrade is allowed. The scripted offline page is only a UI
  direction aid and does not satisfy the real-intelligence gate.
- The current Codex Switch configuration exposes an authorized
  OpenAI-compatible Responses API and `gpt-5.6-sol`. Runtime access must read
  the credential in memory and must never copy it into this repository or logs.

## Completed data and evaluation foundation

1. Two bounded read-only Agents independently designed the telemetry/event truth
   and the document/configuration/gold-question corpus.
2. The TDD-built generator now produces four synthetic heat-pump assets, 72
   hours of ten-second telemetry, 103,650 raw rows, 103,620 unique rows, CSV,
   Parquet, DuckDB, configuration history, point aliases, documents and hidden
   event truth.
3. Fifteen event/data-quality types include gap, duplicate, out-of-order,
   sensor drift, flow loss, command/feedback mismatch, high discharge
   temperature, short cycling, low efficiency, configuration change, one
   defrost sequence, stuck valve, low-suction features, telemetry freeze and
   fan-feedback loss.
4. The candidate-neutral manifest freezes 52 knowledge, data, combined,
   clarification, safety and presentation cases.
5. The gold evaluator recomputes all numerical truth from DuckDB. Focused tests
   pass 6/6 and the measured result is retained at
   `evaluation/results/agentic-hvac-gold.json`.
6. The working branch is `codex/agentic-rag-bakeoff`. OnionQuant runtime, Cron,
   context state, Inbox and Outbox remain untouched.

## Current work

Slice AR-04 is complete. The root page is now a single, non-defrost-centric
Chat backed by the authorized Responses API. The model chooses bounded project
search, read-only DuckDB analysis, or both; results render as Chinese Markdown,
tables, charts, source-status cards and human-readable filenames. The original
workspace/import interface remains available at `/workbench`.

The live path fails closed when SQL is rejected, evidence tools are skipped,
the model stops without a final answer, eight total tool calls are exceeded, or
the 120-second request deadline expires. A real-model desktop journey and a
follow-up question on the same conversation passed; the completed conversation
also passed at 320px width. Final independent rereview after the async-loop,
tool-budget and browser refinements reported Critical 0 and Important 0.

Fresh release verification passed Ruff, formatting, 196 tests with five
documented optional skips, and the public release guard. A wheel was built in
an isolated pinned Hatchling environment; it contains the 7.1 MB synthetic
DuckDB snapshot and source documents, but not the 30.9 MB raw telemetry CSV or
5.1 MB Parquet file. Those two evaluation files remain reproducible from the
generator and are intentionally ignored by Git.

Slice AR-05 is active. The lightweight Haystack plus DuckDB direction build has
cleared the browser gate, but the full 52-case model-backed candidate bake-off
against RAGFlow/WrenAI and DB-GPT is deliberately not claimed complete. The
Chairman can first trial the direction at `http://127.0.0.1:8788`.

## Next acceptance gate

The next meaningful checkpoint requires:

- the full 52-case model-backed result artifact for the lightweight candidate;
- a measured decision on whether installing a heavier platform is justified;
- Chairman feedback on the current single-chat direction;
- final release verification, detailed company-PC handoff, commit and push.

No heavy platform is installed merely because the synthetic corpus exists: the
corpus is about 43 MB, and storage pressure is not a valid reason to select or
reject the current lightweight direction.
