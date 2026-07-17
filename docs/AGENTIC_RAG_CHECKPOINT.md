# Project Copilot local-private Chat checkpoint

Last updated: 2026-07-18 05:45 (Asia/Shanghai)

The authoritative status and acceptance contract are in
`docs/AGENTIC_RAG_TASK_LEDGER.json`; append-only changes are in
`docs/AGENTIC_RAG_TASK_EVENTS.jsonl`.

## Current product

- The ordinary user journey is one general project Chat. `/workbench` redirects
  to `/`; the workspace APIs remain available for automation and administration.
- Messages scroll inside the viewport and the composer remains fixed at the
  bottom on desktop and mobile.
- Users upload one or more files beside the composer. Existing
  Haystack/Docling ingestion performs parsing and indexing; the main Chat reads
  the active workspace immediately without a manual reindex step.
- `original_filename` and repository-relative source location are retained
  separately from collision-safe internal storage names. Citations lead with
  the exact original filename and hide internal IDs.
- Exact-filename questions use a deterministic RAG route: retrieve relevant
  passages inside the named file, perform one bounded model synthesis, return
  one compact citation, and keep numeric grounding checks.
- Conclusions, rankings and key numbers remain primary. Statistical scope,
  provenance and limitations render as smaller secondary text. Citations use
  progressive disclosure.
- The persistent full-project graph is superseded in the ordinary UI. Each
  answer now shows exact original filenames in a compact `参考资料` summary;
  expansion reveals original excerpts and an optional level-by-level relative
  retrieval path. Markdown links in answer paragraphs and headings display
  only the human label, never the internal path.
- The baseline remains at `/` and `/versions/baseline`. Three architecture
  trials share the exact same backend: `/versions/conversation` adds a visible
  pending-question queue, `/versions/evidence` opens evidence on demand, and
  `/versions/canvas` keeps long engineering deliverables stable beside Chat.
- All four routes use one serial request scheduler and one Marked 18.0.6 plus
  DOMPurify 3.4.12 rendering boundary. Bare internal relative paths, Windows
  paths and POSIX absolute paths are removed from visible answer text while
  original filenames remain visible in citation summaries and evidence cards.

## Private catalog proof

- A read-only catalog importer reuses ProjectIndexer for Markdown, text, JSON,
  Python, TOML/YAML/INI, CSV and supported Office formats.
- It rejects runtime under the source/public worktree and excludes `.git`,
  environments, caches, builds, secrets/keys, hidden benchmark truth, and all
  OnionQuant runtime/context/inbox/outbox/task-claim paths.
- Current private runtime uses `<private-runtime-root>` outside the public
  repository and is ignored by Git.
- Current D-drive proof indexed all 198 discovered repository files with zero
  failures. The query path returns original project filenames and the private
  runtime is active at `http://127.0.0.1:8788`.
- The full documents/dev environment is installed at `<documents-venv>` on a
  data volume with sufficient free space. Local tokenizer and Docling layout
  artifacts live under `<documents-model-root>`.
- With `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`, the real integration
  test passed PDF and DOCX parsing, chunking, indexing, page citation and
  restart retrieval. No runtime model download was used.

## Evidence completed in this slice

- Current GitHub/official research selected Open WebUI/AnythingLLM progressive
  citation patterns and evaluated GitNexus/Cytoscape/Sigma graph approaches.
  Chairman review selected exact filename references plus an optional evidence
  path instead of a persistent full-project graph.
- TDD covers fixed layout, compact context, Chat upload, Chinese original
  filename preservation, immediate main-Chat search, long-file relevant
  passage retrieval, private-catalog exclusions, repository-external runtime,
  evidence-path privacy, exact-file citation collapse, internal Markdown path
  hiding, relevant long-file excerpts, and sample-count versus seconds.
- Real browser acceptance at 1280x800 confirmed: body height equals viewport,
  composer remains visible, the persistent graph is absent, exact original
  filenames lead the reference summary, excerpts and retrieval paths expand on
  demand, and internal Markdown paths do not appear.
- The focused direction/Agent/Web regression suite reached 100% with one
  environment skip; Ruff passed. Independent rereview reported Critical 0 and
  Important 0.
- Independent architecture review scored baseline 2/5, conversation 3/5,
  evidence 4/5 and canvas 4/5. No version cleared the predeclared 4.2/5
  replacement gate. The recommended future shape is evidence as the default,
  the queue shared by all versions, and canvas opened only for complex results.
- The final 14-case real-model shared-backend benchmark completed every request
  with zero execution failures, but only 1/14 passed the automatic hard gate.
  Independent HVAC-engineer review scored pass 0, partial 6, fail 8
  (21.4/100). This is a truthful product failure: the UI variants improve
  reading and interaction, but the shared backend does not yet reliably answer
  harder multi-part engineering questions.
- The benchmark exposed and drove bounded fixes without weakening policy:
  SQLGlot now admits only the additional read-only `ABS` and `EXTRACT`
  functions; the project timezone is explicit and the CPython-maintained
  `tzdata==2026.3` package is hash-locked for Windows/Linux portability;
  percentage-point claims no longer double-parse as event counts; short
  documents retain passage-level evidence while long files use word chunks;
  mixed analysis-plus-write requests answer only the safe part and refuse the
  write clause.

## Fresh release verification

- Ruff check and Ruff format check passed; Node syntax validation passed for
  the vendored-browser integration.
- The final Python suite collected 391 tests and exited successfully; the
  environment-gated Docling/real-model cases remained explicit skips rather
  than silent fallbacks.
- The current single-Chat desktop/mobile browser suite passed 9 tests with one
  opt-in real-model test skipped. It covered all four routes, upload beside the
  composer, fixed layout, serialized questions, V2 evidence, V3 canvas,
  Markdown/path privacy and XSS sanitization.
- The opt-in approved-provider browser journey was then run separately and
  failed honestly: the complex HP-02 change-effect answer was blocked by the
  numeric grounding gate and produced no table. This is consistent with the
  1/14 complex benchmark and is not recorded as a UI pass or downgraded test.
- `project_copilot.release_guard` and distribution-content verification passed.
  The fresh wheel is 4,800,612 bytes and the sdist is 6,454,469 bytes; a wheel
  target install and `/api/health` smoke passed with the synthetic project.
- `pip-audit --strict` found no known vulnerabilities in
  `requirements.runtime.lock`; LicenseCheck reported all runtime packages
  compatible with the Apache-2.0 project policy. Independent final code review
  reported Critical 0 and Important 0.
- Implementation and detailed handoff commit `3151a02` was pushed without
  force to `origin/codex/agentic-rag-bakeoff`. This delivery ledger is closed:
  the four-version architecture experiment is complete, while the failed
  intelligence gate becomes a separate future backend-quality objective.

## Cross-platform release remediation

- GitHub Actions run `29615856293` preserved an important release failure:
  Ubuntu formatted standalone snapshot timestamps in UTC because the inspector
  inherited the runner timezone, while Windows could not construct
  `ZoneInfo("Asia/Shanghai")` without an IANA database.
- TDD now requires every snapshot inspector to receive the project timezone
  explicitly. DuckDB sessions therefore return the same `+08:00` historical
  windows on developer machines and CI instead of depending on host locale.
- Current Python documentation recommends the first-party `tzdata` package for
  systems without system IANA data. `tzdata==2026.3` replaces the earlier
  direct `pytz` dependency in every runtime/dev/document lock chain; an empty
  system TZ path still resolves `Asia/Shanghai` from the packaged database.
- Focused snapshot/direction/release-lock tests, the full Python suite, Ruff,
  browser acceptance, release guard and the deterministic 23/23 offline
  evaluation pass locally. Independent portability review reports Critical 0,
  Important 0 and Minor 0. AR-10 remains open until the replacement GitHub
  Actions run is green; no failed CI result is being relabeled as complete.

## Next bounded steps

Do not create a fifth UI or polish the current variants. Keep the four routes
available for Chairman comparison, then improve the shared backend against the
same frozen 14 questions:

1. Generate each answer directly from structured tool evidence and bind every
   claim to a field, time window or event contract instead of freely rewriting
   verified numbers.
2. Deliver verified subquestions even when one requested value fails grounding;
   refuse only the unsafe or unsupported part.
3. Whitelist engineer-facing project sources and exclude `evaluation/`,
   `tests/`, `scripts/` and internal handoff material from user citations.
4. Validate event semantics, especially missing flow proof, command/feedback
   duration and the difference between one observation and a continuous state.
5. Re-run the unchanged 14-case benchmark. Do not resume UI selection until
   there are zero critical engineering errors, zero raw paths, zero internal
   citations, all unsafe writes are refused, all safe subquestions receive an
   answer, and at least 12/14 cases pass independent review.
6. Separately optimize catalog updates so one uploaded file does not rebuild the
   whole 198-file private catalog.

The Goal tool could not replace the older unfinished Goal, so
`docs/AGENTIC_RAG_TASK_LEDGER.json` revision 13 is authoritative for this
four-version continuation.

## Durable constraints

- Never commit or package real source files, indexes, DuckDB/SQLite/Parquet,
  embeddings, graph data, logs, credentials or private runtime state.
- Never touch OnionQuant Cron, context_state, Inbox or Outbox.
- No silent fallback: unsupported file parsing and model/tool failures remain
  visible and fail closed.
