# Offline evaluation and synthetic HVAC corpus

## Purpose

The evaluation suite freezes a public, fully synthetic Project Package and a gold question set before a candidate run. It then exercises the same `ProjectIndexer`, `ProjectAgent`, deterministic Haystack tool workflow, and governed DuckDB analytics interfaces used by the application. Every case records raw answer, citations, tool activity, refusal/clarification flags, elapsed time, expectations, and individual metric verdicts in JSON.

This baseline measures the deterministic test double. It does not claim that an untested company model, a larger real corpus, or a different embedding backend has the same quality.

## Why the repository uses a small offline harness

A 2026-07-15 search and GitHub review checked maintained evaluation projects rather than inventing a replacement framework:

- [Ragas available metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) provides retrieval, response, and agent/tool-oriented evaluation primitives.
- [DeepEval quickstart](https://deepeval.com/docs/getting-started) provides pytest-oriented LLM evaluation and reusable metrics.
- [Phoenix evaluation concepts](https://arize.com/docs/phoenix/evaluation/concepts-evals) provides tracing and evaluator workflows.
- [promptfoo RAG evaluation guide](https://www.promptfoo.dev/docs/guides/evaluate-rag/) provides declarative provider and assertion comparisons.

Those projects remain appropriate for model-judge campaigns and provider comparisons. The committed acceptance gate deliberately adds no evaluation dependency because it must run on restricted Windows installations without network access, API keys, a judge model, or another lockfile change. It uses maintained `pytest` execution plus standard-library scoring against explicit strings, source IDs, tool names, and safety flags. A future company-model campaign can export the same case and result schema into Ragas, DeepEval, Phoenix, or promptfoo without changing the frozen gold facts.

The read-only [Kelsus sovereign AI reference architecture](https://github.com/Kelsus/kelsus-oss-ai-ref-arch) was used as a workflow pattern: synthetic data, frozen gold labels, failed items scored instead of silently dropped, completeness counts, raw latency, and immutable measured artifacts. None of its AWS, healthcare, finance, model-serving, or benchmark code was copied.

## Corpus

`examples/synthetic_hvac` contains Project Aurora, an invented cooling-plant commissioning project:

- background and fictional equipment register;
- dated baseline, controller-export, and current-state configuration evidence;
- six dated meetings from 2026-06-05 through 2026-07-14;
- implemented and deferred decisions plus open/closed action items;
- startup, alarm-review, and change-control SOPs;
- 72 hourly telemetry rows covering 2026-06-30 through 2026-07-02;
- 8,640 ten-second defrost telemetry rows covering 2026-07-15, with one
  compliant and one intentionally non-compliant synthetic event;
- a versioned synthetic defrost rule pack bound to a fictional controller and
  firmware version;
- an intentional 7.0 versus 6.5 degree historical/current configuration conflict;
- an intentional 55 versus proposed 45 kPa implemented/deferred distinction.

Public [ASHRAE commissioning](https://www.ashrae.org/technical-resources/bookstore/commissioning) and [central chilled-water plant](https://www.ashrae.org/professional-development/self-directed-learning-group-learning-texts/fundamentals-of-design-and-control-of-central-chilled-water-plants) pages were consulted only for ordinary vocabulary and document workflow shape. No ASHRAE text, paid standard, manufacturer table, facility record, or real control value is redistributed. Corpus origin and restrictions are recorded in `examples/synthetic_hvac/SYNTHETIC_DATA_PROVENANCE.md`; the package is CC0-1.0.

## Gold coverage

`evaluation/gold_cases.json` contains 23 frozen cases across:

- exact lookup;
- cross-document synthesis;
- temporal meeting/decision questions;
- historical versus current configuration conflict;
- combined meeting evidence and governed telemetry analysis;
- clarification;
- insufficient-evidence refusal;
- hostile shell, live-equipment, Web, and MCP requests;
- deterministic tool selection.
- compliant, non-compliant, no-event, mid-event, and truncated-window defrost
  replay with rule citations and explicit unobservable outcomes;
- English and Chinese live-equipment-control refusals.

Each case declares expected source filenames, required answer terms, terms that must occur in cited excerpts, exact tool order, and refusal/clarification flags. Gold expectations are not edited after a run merely to raise a score; changes require a reviewable corpus or contract reason and a fresh measured artifact.

## Run locally

From the repository root on Windows:

```powershell
& ".venv\Scripts\python.exe" -m pytest evaluation/test_offline_evaluation.py -q
& ".venv\Scripts\python.exe" -m evaluation.run_offline `
  --output evaluation/results/deterministic-baseline.json
```

The runner uses a temporary runtime by default. To retain generated indexes and the DuckDB snapshot for debugging, pass a disposable directory:

```powershell
& ".venv\Scripts\python.exe" -m evaluation.run_offline `
  --runtime .local-evaluation-runtime `
  --output evaluation/results/local-debug.json
```

Generated runtime state must not be committed. The measured JSON may be retained when it identifies the adapter and corpus digest and contains no real project data.

## Metrics and evidence

Per-case metrics are deterministic booleans or `null` when not applicable:

| Metric | Measured evidence |
|---|---|
| Retrieval | Every declared gold source filename appears in returned citations. |
| Citation grounding | Every declared grounding term appears in cited excerpts. |
| Answer correctness | Every declared answer term appears in the returned answer. |
| Tool selection | Ordered tool activity exactly matches the gold tool list. |
| Refusal | Returned refusal flag matches the case contract. |
| Clarification | Returned clarification flag matches the case contract. |
| Latency | Wall-clock milliseconds measured around the complete `ProjectAgent.ask` call. |

Evidence-bearing retrieval cases also use Haystack's maintained
`DocumentRecallEvaluator`, MRR, and NDCG implementations with
`meta.source` as the comparison field. These ranking values measure this frozen
corpus and retriever configuration only.

Summary rates are calculated only as `passed / measured`; the report also keeps both counts. Cases that throw an exception are recorded with status `error`, their applicable metrics fail, and `failed_execution_count` increases. Nothing is silently dropped.

## Current deterministic baseline

The committed `evaluation/results/deterministic-baseline.json` is the authoritative measured artifact for the current corpus digest. Its latest local run recorded:

- 23 cases completed, 0 execution failures;
- 23 cases passed all applicable metrics;
- retrieval and citation grounding measured on 16 evidence-bearing cases, with 16 passes each;
- answer correctness, tool selection, refusal, and clarification measured on 23 cases, with 23 passes each;
- Haystack retrieval ranking: Recall `1.0`, MRR `0.859375`, and NDCG
  `0.9003721028168283` across 16 cases;
- latency on this local deterministic run: minimum `1.54 ms`, median
  `31.473 ms`, p95 `167.159 ms`, and maximum `177.853 ms`, with raw per-case
  values in the JSON.

The counts are a regression baseline for this frozen synthetic corpus and deterministic adapter, not a general quality percentage. Company-model acceptance requires a separate run identified by endpoint/model configuration, the same frozen gold contract, and review of every failed case without sending the synthetic or company corpus to an unapproved service.
