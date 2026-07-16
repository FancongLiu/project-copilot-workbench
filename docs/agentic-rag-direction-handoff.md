# Agentic RAG direction build: trial and continuation handoff

## 1. Status and product boundary

This document hands off the first model-backed direction build. It is a fast,
reviewable trial for Chairman feedback, not the final platform-selection or
company-field release.

The root page is intentionally one Chat when a real model is configured. A
user can ask a knowledge question, a telemetry question, or a combined
question without selecting a technical tool. The Agent decides which bounded
tools to call and returns:

- Chinese Markdown with an explicit grounded/clarification/refusal/failure state;
- a human-readable table and chart when a database query returns structured data;
- source cards with filename, original excerpt, location, source role, current
  or superseded status, and the file's share of this answer's cited evidence;
- a short human trace such as “已核对项目资料 · 已计算运行数据”.

Defrost remains one evaluation case. It is not the homepage, navigation model,
or product name.

## 2. What is implemented

The current direction path uses mature components already pinned by the repo:

1. Haystack `OpenAIResponsesChatGenerator` for the Responses API.
2. Haystack `Agent` for iterative tool selection and retry.
3. Haystack BM25 for the compact synthetic knowledge corpus.
4. DuckDB opened read-only for telemetry.
5. SQLGlot for one-statement, one-table, allowlisted-column SQL policy.
6. A retry contract: rejected CTE, nested, window, file-access, mutation or
   unsupported-function SQL is not executed; the Agent receives a safe policy
   reason and may rewrite the query.
7. A maximum of six Agent steps, eight total tool calls, a 120-second outer
   deadline, 50 returned rows, two DuckDB threads and a 256 MB query memory
   limit.
8. Pre-model refusal for explicit delete, write, threshold change, equipment
   start/stop or control commands in Chinese and English.
9. Bounded six-message conversation history so follow-up questions can retain
   the previous asset, window and answer without carrying an unbounded thread.
10. Fail-closed grounding: factual model prose is discarded if no project or
    database tool was successfully used.

## 3. Synthetic trial corpus

`examples/agentic_hvac_bakeoff` is fully synthetic, CC0, and not engineering
guidance. The generator creates:

- four assets (`HP-01` through `HP-04`);
- 72 hours at a ten-second sampling contract;
- 103,650 raw rows, 103,620 unique rows and a 103,680-point ideal grid;
- CSV, Parquet and DuckDB representations;
- assets, point aliases and configuration history;
- project overview, current and superseded configuration, control sequence,
  meeting, decision, service and SOP evidence;
- 15 event/data-quality types including gaps, duplicates, ordering, drift,
  command/feedback mismatch, high discharge temperature, short cycling,
  efficiency degradation, configuration change, defrost, valve sticking, low
  suction features, telemetry freeze and fan/flow feedback failures;
- 52 knowledge, data, combined, clarification, safety and presentation cases.

Regenerate and verify numerical truth:

```powershell
Set-Location E:\2026_AgentStudy\Python_code\public_repos\project-copilot-workbench
& ".venv\Scripts\python.exe" scripts/generate_agentic_hvac_bakeoff.py
& ".venv\Scripts\python.exe" evaluation/run_agentic_rag_bakeoff.py
& ".venv\Scripts\python.exe" -m pytest `
  evaluation/test_agentic_rag_bakeoff.py `
  evaluation/test_agentic_rag_gold.py -q
```

The hidden truth directory must never be indexed or sent to a candidate.

## 4. Personal-machine trial with the current Codex Switch provider

This profile is allowed only for this authorized personal-machine trial. It
reads the active Codex provider and credential in memory. It never copies the
credential into the repository, tests, logs, screenshots or result files.

```powershell
Set-Location E:\2026_AgentStudy\Python_code\public_repos\project-copilot-workbench
$env:PROJECT_COPILOT_MODEL_MODE = "codex-switch"
$env:PROJECT_COPILOT_ACK_CODEX_SWITCH = "true"
$env:PROJECT_COPILOT_REASONING_EFFORT = "high"
& ".venv\Scripts\python.exe" -m project_copilot.cli `
  --project examples\synthetic_hvac `
  --runtime artifacts\direction-live-runtime `
  --host 127.0.0.1 --port 8788
```

Open `http://127.0.0.1:8788`. A real-model page must show:

- `真实模型 · 只读分析`;
- the synthetic project, timezone and data-through boundary;
- no defrost-centric navigation;
- a grounded state after a successful tool-backed answer.

Do not use `codex-switch` on a company PC. Rotate a directly embedded personal
provider credential after development exposure and prefer a secret manager or
environment injection when supported.

## 5. Company endpoint configuration

Use `company` mode, never a copied personal credential:

```powershell
$env:PROJECT_COPILOT_OPENAI_API_KEY = Get-Secret `
  -Name "ProjectCopilot-OpenAI" -AsPlainText
. .\config\company-v2.example.ps1 `
  -OpenAIBaseUrl "https://ai-gateway.example.invalid/v1" `
  -OpenAIModel "approved-model-id" `
  -OpenAIWireApi "responses" `
  -AllowedHosts @("ai-gateway.example.invalid")
```

The exact-host allowlist, HTTPS rule, internal CA, disabled proxy inheritance,
zero SDK retries, `store=false`, and strict tool schemas remain mandatory.
Real company documents, endpoints, certificates, credentials, indexes and
evaluation results remain outside this public repository.

## 6. Browser acceptance

Start the approved local server, then run:

```powershell
$env:PROJECT_COPILOT_BROWSER_URL = "http://127.0.0.1:8788"
$env:PROJECT_COPILOT_SCREENSHOT_DIR = "artifacts\direction-browser"
& ".venv\Scripts\python.exe" -m pytest `
  tests/test_browser_acceptance.py::test_direction_chat_model_backed_engineer_journey `
  -vv
```

The journey asks why HP-02 changed its supply-air setpoint, follows up with
“what was the electrical-energy change?”, and checks model-selected knowledge
plus data tools, bounded conversation context, Markdown headings, structured
tables, a chart, source cards, grounded trace and a completed 320-pixel layout.

## 7. Known limitations that must remain visible

1. The public direction page currently runs on the compact synthetic corpus.
   It is not yet connected to the ordinary workspace import lifecycle.
2. BM25 is the current compact retrieval baseline; hybrid embeddings and
   reranking remain approval-gated and unmeasured for this corpus.
3. The SQL tool intentionally rejects CTEs, subqueries and window functions.
   Complex state-machine or interval analysis must become reviewed domain tools
   rather than unrestricted SQL.
4. Citation locations are file paths for Markdown sources. PDF page/section
   click-through requires the approved parser and source-inspection UI.
5. The current evidence-share percentage is a distribution across cited
   sources, not answer confidence, correctness probability or source authority.
6. There is no built-in multi-user identity or authorization. Keep the server
   loopback-only until a separate security design is reviewed.
7. One successful two-turn live browser journey does not choose RAGFlow,
   WrenAI, DB-GPT or any other platform. The final choice requires the
   same-model 52-case bake-off.

## 8. Disk and deployment decision

The generated corpus is about 43 MB. The compact direction runtime needs only a
few GB of ordinary package/runtime headroom. RAGFlow's official 50 GB minimum
is for its full Docker service stack, not for these project files. Do not force
a heavy platform deployment merely to satisfy a framework label.

The wheel includes only the compact DuckDB snapshot and source documents needed
for this synthetic direction page, not duplicate CSV/Parquet evaluation files.

## 9. Next bounded work

1. Connect the single Chat to the active imported workspace and per-project
   analytics snapshot without weakening isolation.
2. Run all 52 frozen cases through the current Haystack path using the same
   approved model and retain raw results, latency, citations and tool attempts.
3. Run the same cases against WrenAI and the DB-GPT challenger only if their
   installation and licensing gates pass; do not declare a winner from demos.
4. Add exact source inspection, page/section links, current/superseded revision
   metadata and effective dates.
5. Add reviewed time-series tools for intervals, state transitions, missing
   data, command/feedback delay and comparison normalization.
6. Repeat independent code/security and HVAC-engineer review. Resolve every
   Critical/Important item before release.

## 10. Resume checklist for another Agent

Read, in order:

1. `AGENTS.md` at the OnionQuant project root;
2. `company/runtime/project_copilot_v2_handoff_20260715.md`;
3. `docs/AGENTIC_RAG_TASK_LEDGER.json`;
4. `docs/AGENTIC_RAG_CHECKPOINT.md`;
5. this handoff;
6. `evaluation/agentic_rag_bakeoff.json` and the gold result JSON.

Continue on the current `codex/agentic-rag-bakeoff` branch. Preserve OnionQuant
Cron, runtime, context, Inbox and Outbox state. Use TDD, systematic debugging,
verification-before-completion and independent review. Never print or commit a
credential and never force-push.
