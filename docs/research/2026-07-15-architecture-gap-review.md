# V2 architecture gap review

Date: 2026-07-15

This review was performed after the first complete V2 implementation and
before release. It used current upstream documentation/source plus an
independent read-only Agent. The main FastAPI/Haystack/DuckDB architecture is
retained; the review found several gaps that were more safely closed with
maintained upstream components than with custom algorithms.

## Decisions and implemented corrections

| Gap | Current upstream evidence | Decision |
|---|---|---|
| Retrieval had BM25/dense RRF but no cross-encoder reranker | [`sentence-transformers-haystack` 0.1.1](https://pypi.org/project/sentence-transformers-haystack/0.1.1/) and its maintained [`SentenceTransformersSimilarityRanker`](https://github.com/deepset-ai/haystack-core-integrations/blob/integrations/sentence_transformers-v0.1.1/integrations/sentence_transformers/src/haystack_integrations/components/rankers/sentence_transformers/sentence_transformers_similarity.py) | Added an optional local-model adapter. It requires an existing directory, `trust_remote_code=false`, an explicit approval flag, a separate offline wheel/model bundle and frozen-gold Recall/MRR/NDCG A/B before activation. |
| Office/PDF parsing flattened the entire document and lost page/heading metadata | [`docling-haystack` 1.2.0](https://pypi.org/project/docling-haystack/1.2.0/), [`DoclingConverter`](https://github.com/deepset-ai/haystack-core-integrations/blob/integrations/docling-v1.2.0/integrations/docling/src/haystack_integrations/components/converters/docling/converter.py), and [Docling chunking](https://docling-project.github.io/docling/concepts/chunking/) | Replaced the custom Markdown export adapter with official structured `DOC_CHUNKS`, `HybridChunker`, `page_number`, `dl_meta`, split metadata and heading-derived sections. Runtime tokenizer download is blocked by requiring an approved local tokenizer path. |
| Agent wall-clock budget was checked only when a tool began | Haystack 2.31 [`Agent.run_async`](https://github.com/deepset-ai/haystack/blob/v2.31.0/docs-website/reference_versioned_docs/version-2.31/haystack-api/agents_api.md) and Python 3.12 [`asyncio` timeouts](https://docs.python.org/3.12/library/asyncio-task.html#timeouts) | The primary Web route now awaits the asynchronous Agent under one end-to-end deadline; model request timeout is no longer larger than the default Agent budget. Slow-model regression proves a bounded refusal. |
| Embeddings ignored the internal CA used by chat | [HTTPX 0.28 SSL contexts](https://github.com/encode/httpx/blob/0.28.1/docs/advanced/ssl.md) and [OpenAI custom HTTP client](https://github.com/openai/openai-python/tree/v2.37.0#configuring-the-http-client) | Added one explicit SSLContext builder used by company chat and embeddings, with `trust_env=false`; deprecated string `verify` configuration is not used. |
| Two-step source inspection could not use a retrieved citation ID | Haystack `DocumentSplitter` reserves its own `source_id` metadata | Moved the durable inventory identity to `project_source_id` and retained citations in the tool result sent to the Agent. A scripted regression now proves search -> source inspection -> final answer. |
| Workspace updates could lose concurrent imports and expose a partial index | Existing project dependency `filelock` plus same-filesystem atomic replacement | Added one workspace lifecycle lock across import/delete/re-index, atomic temporary-index replacement, and a concurrent eight-import regression. No new coordination framework was introduced. |
| Evaluation was source-presence only | Haystack 2.31 `DocumentRecallEvaluator`, `DocumentMRREvaluator`, and `DocumentNDCGEvaluator` | The offline report now records ranking-sensitive Recall, MRR and NDCG by exact `meta.source`, in addition to per-case retrieval, grounding, tool, refusal and latency evidence. |

## Offline parser and reranker boundary

The optional components are deliberately not part of the base hash-locked
runtime. Both pull large model stacks and require a separate company acceptance
bundle. Docling model artifacts must be explicitly prefetched using the
[official offline procedure](https://docling-project.github.io/docling/usage/advanced_options/#model-prefetching-and-offline-usage), and the tokenizer/model directories must be hashed and immutable.

The local development E: drive did not have enough free space for a safe full
Docling/model installation. The release therefore includes:

- adapter and metadata contract tests in the base suite;
- an explicit optional-integration CI/company gate for generated synthetic
  PDF/DOCX, offline model/tokenizer assets, page/section citations and restart;
- a stop condition in the company handoff: base formats remain the only
  approved production imports until that separate gate passes.

This limitation is recorded rather than hidden or replaced with a fake claim.

## LightRAG security correction

Stable LightRAG v1.5.4 remains useful only for isolated loopback synthetic A/B.
The security changes for unauthenticated network exposure, `/health`
disclosure, external-parser task-ID path injection and container hardening
first appeared in the [v1.5.5rc1 prerelease](https://github.com/HKUDS/LightRAG/releases/tag/v1.5.5rc1). No company data may enter v1.5.4; adoption requires a fixed stable release and a fresh threat model.

## Rejected expansions

RAGFlow, Dify, GraphRAG, PageIndex, Qdrant service, Phoenix and a full LightRAG
backend still add more deployment and security surface than the measured V2
problem requires. DeepEval remains a possible future Agent-specific evaluation
extra; the current offline release uses Haystack's already-installed
deterministic evaluators and does not add an LLM judge.
