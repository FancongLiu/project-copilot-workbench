# Agentic RAG direction build: trial and continuation handoff

## 1. Status and product boundary

This document hands off the measured model-backed direction build. It remains
a Chairman trial rather than a company-field release, but the current
lightweight architecture is now selected for this delivery from live evidence,
not from a demo or framework popularity.

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
7. A maximum of nine Agent steps, eight total tool calls, a 120-second outer
   deadline, 50 returned rows, two DuckDB threads and a 256 MB query memory
   limit.
8. Pre-model refusal for explicit delete, write, threshold change, equipment
   start/stop or control commands in Chinese and English.
9. Bounded six-message conversation history so follow-up questions can retain
   the previous asset, window and answer without carrying an unbounded thread.
10. Fail-closed grounding: factual model prose is discarded if no project or
    database tool was successfully used.
11. Typed `inspect_hvac_snapshot` operations for completeness, missing data,
    duplicate timestamps, ingest-order reversals, frozen sensor tuples,
    command-feedback mismatch, short cycling, flow-proof loss, defrost and
    alarm windows. These are reviewed DuckDB operations, not unrestricted SQL.
12. Typed `inspect_metric_extreme` for an allowlisted metric's exact minimum or
    maximum window, with duration, output, COP and superheat. It reports an
    observed extreme and never invents an alarm threshold.

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
Set-Location <project-copilot-workbench-repo>
& ".venv\Scripts\python.exe" scripts/generate_agentic_hvac_bakeoff.py
& ".venv\Scripts\python.exe" evaluation/run_agentic_rag_bakeoff.py
& ".venv\Scripts\python.exe" -m pytest `
  evaluation/test_agentic_rag_bakeoff.py `
  evaluation/test_agentic_rag_gold.py -q
```

The hidden truth directory must never be indexed or sent to a candidate.

### Retained live results

Run the resumable live candidate benchmark only against an approved local
server:

```powershell
& ".venv\Scripts\python.exe" -m evaluation.run_agentic_rag_candidate `
  --candidate-id haystack-duckdb-v35-delivery-final `
  --endpoint http://127.0.0.1:8788/api/direction/query `
  --model-label approved-responses-model `
  --output evaluation/results/agentic-rag-haystack-duckdb-live-v35.json `
  --resume
```

The first retained live baseline executed 52/52 cases with zero upstream
failures and measured 46.2% behavior, 42.3% tool-contract and 34.1%
evidence-contract pass rates. After TDD fixes, typed analysis tools, event-name
normalization, evidence-bound answer regeneration and negative-boundary corrections,
final v35 executed
52/52 with zero execution failures and measured 52/52 behavior, 52/52 tool
contract and 44/44 exact evidence contract. These automatic checks are not an
answer-correctness percentage. Preserve the raw v35 result and its independent
review under `evaluation/reviews/`; never replace a strict failure with a
hand-edited pass.

The final independent HVAC review accepted 52/52 v35 answers. The SHA-bound
adjudication is
`evaluation/results/agentic-rag-haystack-duckdb-live-v35-adjudication.json`
and is bound to raw-result SHA
`f17bf6a25f333570ebb73daeb3c43bed13069438c19c32b5bf27a1208285fbca`.

The live runner aborts after two consecutive upstream provider failures and
records an atomic checkpoint. `--resume` validates benchmark, candidate,
endpoint, model, revision and provenance before reusing completed cases.

## 4. Personal-machine trial with the current Codex Switch provider

This profile is allowed only for this authorized personal-machine trial. It
reads the active Codex provider and credential in memory. It never copies the
credential into the repository, tests, logs, screenshots or result files.

```powershell
Set-Location <project-copilot-workbench-repo>
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

## 7. Four-version architecture acceptance

After the server is healthy, open `http://127.0.0.1:8788/versions`. The routes
share one backend and are intentionally limited to interaction architecture:

| Route | What to evaluate |
|---|---|
| `/versions/baseline` | Regression control: compact answer and expandable sources |
| `/versions/conversation` | Add questions during a long analysis without losing order |
| `/versions/evidence` | Keep the answer readable while exact filenames and excerpts remain easy to verify |
| `/versions/canvas` | Keep long reports, tables and charts stable during follow-up questions |

Do not compare model quality by running different questions in different
versions. All four routes post to `/api/direction/query`; run the same question
and treat answer quality as the shared-backend score. Compare only readability,
interaction burden and evidence discoverability between pages.

The current independent result is not “one winner passed everything.” Scores
were baseline 2/5, conversation 3/5, evidence 4/5 and canvas 4/5, below the
predeclared 4.2/5 replacement threshold. The recommended next product shape is
V2 as the default Chat, V1 queue behavior in the common composer, and V3 only
for complex results. Keep the root route unchanged until the Chairman reviews
the four pages.

More importantly, the shared intelligence did not clear the harder workload.
The final 14-case live run completed every request with zero execution
failures, but only 1/14 passed the automatic hard gate. Independent HVAC review
recorded pass 0, partial 6 and fail 8 (21.4/100). Keep
`evaluation/results/four-version-shared-backend-live.json` and
`evaluation/reviews/four-version-complex-benchmark-human-review-20260718.md`
together: the raw result is the machine evidence and the review records the
engineering-semantic failures. Do not build a fifth UI before the shared
backend can deliver verified subquestions, filter internal citations and bind
event interpretations to deterministic contracts.

The browser ships pinned local Marked and DOMPurify assets. Node.js is not
required on the company PC at runtime; the vendored files are included in the
Python package. `tzdata==2026.3` is a required runtime dependency because
Python `ZoneInfo` needs packaged IANA data on Windows, and each DuckDB snapshot
session receives the project timezone explicitly. Install from
`requirements.runtime.lock`; do not omit it or historical-window queries can
fail before otherwise valid SQL execution.

## 8. Known limitations that must remain visible

1. The public direction page currently runs on the compact synthetic corpus.
   It is not yet connected to the ordinary workspace import lifecycle.
2. BM25 is the current compact retrieval baseline; hybrid embeddings and
   reranking remain approval-gated and unmeasured for this corpus.
3. The model-written SQL tool intentionally rejects CTEs, subqueries and window
   functions. Common data-quality, event and extreme-window work now uses typed
   domain tools; new sequence analyses must follow that reviewed pattern rather
   than weakening SQL policy.
4. Citation locations are file paths for Markdown sources. PDF page/section
   click-through requires the approved parser and source-inspection UI.
5. The current evidence-share percentage is a distribution across cited
   sources, not answer confidence, correctness probability or source authority.
6. There is no built-in multi-user identity or authorization. Keep the server
   loopback-only until a separate security design is reviewed.
7. Automatic behavior/tool checks do not prove engineering correctness. Keep
   the independent 52-case adjudication as a separate release gate and retain
   every rejected answer as regression evidence.
8. Event intervals use `[start_time, end_time)`: the exclusive end is the last
   matching sample plus the project sample interval. A compressor
   command-feedback mismatch is a formal event only at 60 seconds or longer;
   shorter windows are observations. Alarm codes such as A311 are filters, not
   equipment IDs.

## 9. Disk and deployment decision

The measured repository is 887.82 MB including a 762.85 MB local virtual
environment and 73.87 MB of test/runtime artifacts. Source, configuration and
business data are about 51 MB. RAGFlow's official 50 GB allowance is for its
full Docker service stack, not for these project files. Do not force a heavy
platform deployment merely to satisfy a framework label.

The wheel includes only the compact DuckDB snapshot and source documents needed
for this synthetic direction page, not duplicate CSV/Parquet evaluation files.
The final wheel is 4,610,767 bytes (about 4.6 MB); the DuckDB snapshot is about
7.1 MB when uncompressed inside the installed package. Both wheel and sdist
exclude benchmark `hidden_truth`.

## 9. Next bounded work

1. Connect the single Chat to the active imported workspace and per-project
   analytics snapshot without weakening isolation.
2. Re-run the independent HVAC adjudication after any prompt, retrieval,
   event-alias or numeric-grounding change; never infer correctness from the
   automatic contract score alone.
3. Run the same cases against WrenAI and the DB-GPT challenger only if their
   installation and licensing gates pass; do not declare a winner from demos.
4. Add exact source inspection, page/section links, current/superseded revision
   metadata and effective dates.
5. Extend typed time-series tools only for measured failure classes; missing
   data, event windows, command/feedback delay and metric extremes are already
   covered in the current synthetic direction path.
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
