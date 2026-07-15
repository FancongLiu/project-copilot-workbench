# V2 framework selection evidence — 2026-07-15

This note records current evidence used for Project Copilot Workbench V2. It is
not a general popularity ranking. The decision weights public-repository
licensing, Windows/offline operation, OpenAI-compatible company endpoints,
governed tool use, and the cost of extending the existing Python codebase.

## Search method

- Public Web search: Bing query for current Haystack Agent, ToolInvoker, hybrid
  retrieval, Docling, and OpenAI-compatible documentation.
- GitHub API: repository metadata collected on 2026-07-15 (stars, license,
  archived flag, and latest push timestamp).
- Primary-source review: upstream READMEs and license files.

GitHub stars are a community-health signal, not a quality percentage.

## Current evidence

| Component | GitHub evidence on 2026-07-15 | License | Relevant capability | Decision |
|---|---:|---|---|---|
| [Haystack](https://github.com/deepset-ai/haystack) | 25,904 stars; pushed 2026-07-15 | Apache-2.0 | Python pipelines, Agent, ToolInvoker, explicit retrieval/routing/generation, OpenAI-compatible base URL | Select as orchestration and retrieval framework; already used by V1 |
| [Qdrant](https://github.com/qdrant/qdrant) / [client](https://github.com/qdrant/qdrant-client) | 33,297 / 1,324 stars; active | Apache-2.0 | Durable local path mode, dense/sparse vectors, optional FastEmbed | Supported scale-out vector adapter; not mandatory for the single-PC default |
| [Docling](https://github.com/docling-project/docling) | 63,206 stars; pushed 2026-07-15 | MIT | PDF, DOCX, PPTX, XLSX, HTML, images and text; local/air-gapped execution; Windows support; Haystack integration | Select as optional Office/PDF parser extra |
| [Unstructured](https://github.com/Unstructured-IO/unstructured) | 15,135 stars; pushed 2026-07-13 | Apache-2.0 | Broad partitioning ecosystem | Valid alternative, but Docling has the stronger current local/Windows fit and simpler unified document model |
| [LlamaIndex](https://github.com/run-llama/llama_index) | 50,867 stars; pushed 2026-07-13 | MIT | Mature ingestion, retrieval, and document agents | Do not replace Haystack: capability is strong, but replacement adds a second orchestration model without a V2 requirement advantage |
| [AnythingLLM](https://github.com/Mintplex-Labs/anything-llm) | 63,341 stars; pushed 2026-07-13 | MIT | Complete workspaces, uploads and agents | Keep only as an external compatibility adapter; embedding the product duplicates the V2 UI/runtime and adds extra egress/telemetry surfaces |
| [Dify](https://github.com/langgenius/dify) | 148,922 stars; pushed 2026-07-15 | Modified Apache-2.0 | Full workflow/RAG platform | Reject as codebase: multi-tenant and branding conditions do not match this Apache-2.0 public product |
| [Open WebUI](https://github.com/open-webui/open-webui) | 145,502 stars; pushed 2026-07-15 | Custom license | Full UI, tools, skills, knowledge | Reject as codebase: branding restriction above 50 users and product replacement cost |
| [Ragas](https://github.com/vibrantlabsai/ragas) | 14,851 stars; last push reported 2026-02-24 | Apache-2.0 | LLM/RAG evaluation metrics and test generation | Keep as an optional external evaluator; deterministic acceptance metrics remain runnable offline without an evaluator LLM |
| [DeepEval](https://github.com/confident-ai/deepeval) | 16,870 stars; pushed 2026-07-14 | Apache-2.0 | Agent and RAG evaluation | Valid optional evaluator; not required by the offline release gate |

## Architecture choice

1. Keep FastAPI, DuckDB, Polars/Pandera, SQLGlot, atomic snapshots, and the
   existing fail-closed network policy.
2. Extend Haystack rather than add a second orchestration framework:
   `Agent(max_agent_steps=...)`, governed `Tool` objects, ToolInvoker, BM25 and
   embedding retrieval, and reciprocal-rank fusion.
3. Persist each workspace's Haystack document store to its runtime directory.
   The default remains a single-PC embedded deployment; a Qdrant adapter is the
   documented scale-out seam, not a mandatory server.
4. Use the official OpenAI-compatible SDK path for company chat and embeddings.
   Host allowlists, HTTPS enforcement, explicit project approval, timeouts, and
   disabled proxy inheritance remain mandatory.
5. Use a deterministic model/embedding test double only for synthetic offline
   acceptance. It is visibly labeled and does not claim production semantic
   quality.
6. Use Docling through a lazy optional adapter for Office/PDF formats. Markdown,
   UTF-8 text, JSON, and approved CSV datasets stay lightweight.
7. Replace fixed natural-language intents with typed analytics tool operations.
   The model selects an allowlisted operation and parameters; the model never
   emits executable SQL.

## Falsification / revisit conditions

Revisit this choice if current evidence shows any of the following:

- the company endpoint cannot provide OpenAI-compatible tool calls;
- Haystack Agent cannot enforce the required bounded execution or tool schema;
- Docling's offline bundle cannot be approved for the company Windows image;
- measured retrieval quality requires a server-grade hybrid store at the
  intended corpus size.

## Recent-project follow-up

The Chairman requested a second pass focused on directly deployable projects
active in the latest month. See
`docs/research/2026-07-15-recent-rag-shortlist.md`. The pass adds LightRAG v1.5.4
as the preferred optional scale-up backend and adopts the Kelsus synthetic-data
and benchmark-ledger workflow. It does not replace the embedded default without
measured acceptance evidence.
