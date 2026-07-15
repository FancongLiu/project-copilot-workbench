# Recent RAG / document-agent shortlist — 2026-07-15

This is the follow-up “search before build” pass requested during V2 delivery.
It focuses on projects active or released in the last 30 days and on code that
can be installed or deployed directly. Repository counts are GitHub API values
captured on 2026-07-15; they are not quality percentages.

## Top deployable candidates

| Project | Current evidence | Direct-use shape | Fit for this workbench |
|---|---|---|---|
| [RAGFlow](https://github.com/infiniflow/ragflow) | 85,106 stars; Apache-2.0; v0.26.4 released 2026-07-07 | Docker Compose product with ingestion pipelines, Docling/DeepDoc, reranking, citations and Agents | Strong complete product, but the documented baseline is Docker, 50 GB disk and a multi-service stack. It also exposes code-executor/MCP capabilities that this product disables. Keep as a separately governed enterprise alternative, not an embedded dependency. |
| [LightRAG](https://github.com/HKUDS/LightRAG) | 37,702 stars; MIT; stable v1.5.4 plus v1.5.5rc1 released 2026-07-13 | `pip install lightrag-hku[api]`, Windows support, Docker, offline guide, upload/status/delete APIs, graph+vector mix mode, reranking and citations | Optional synthetic loopback A/B only. Stable v1.5.4 predates security fixes first present in v1.5.5rc1; do not use company data or adopt until a fixed stable release is re-reviewed. |
| [PrivateGPT](https://github.com/zylon-ai/private-gpt) | 57,331 stars; Apache-2.0; v1.0.1 released 2026-06-18 | Installable API/workbench for OpenAI-compatible inference, files, citations and agentic RAG | Very close to the product shape, but its standard tool surface includes Web, code and MCP. Useful as an alternative backend/reference; adopting it wholesale would require disabling and proving every unsafe capability. |
| [PageIndex](https://github.com/VectifyAI/PageIndex) | 34,041 stars; MIT; v0.3.0.dev3 released 2026-07-10 | Self-hosted reasoning/tree index for long PDF/Markdown documents | Promising for long technical manuals and explicit section/page navigation. Current public release is a dev tag and indexing depends heavily on LLM reasoning. Evaluate later as a specialist tool, not the default mixed project index. |
| [Microsoft GraphRAG](https://github.com/microsoft/graphrag) | 34,443 stars; MIT; v3.1.0 released 2026-05-28 | Graph-based indexing/query package | Mature option for global cross-document themes, but has greater indexing cost and is not necessary for exact configuration/meeting/data acceptance. Benchmark before adoption. |
| [Graphify](https://github.com/Graphify-Labs/graphify) | 87,406 stars; MIT; created 2026-04; v0.9.16 released 2026-07-14 | Installable folder-to-queryable-knowledge-graph skill | Recent and popular, but optimized for code/folder knowledge and Claude-driven graph extraction. It does not replace governed telemetry tools or company endpoint controls. Watch, do not depend on it yet. |

## Workflow/reference repositories

- [awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps)
  (121,633 stars, Apache-2.0) supplies runnable Agent/RAG examples. Reuse
  workflow shapes and test ideas, not copied application glue.
- [Kelsus sovereign AI reference architecture](https://github.com/Kelsus/kelsus-oss-ai-ref-arch)
  (Apache-2.0, created 2026-06-24) is small but directly relevant as a delivery
  pattern: fully synthetic data, no-egress ADR, executable runbook, gold labels,
  benchmark ledger, and measured retrieval/citation/latency evidence.
- [SpecRAG](https://github.com/blackhaiyu-sudo/specrag) is a new evidence RAG
  aimed at PRDs/SOPs/screenshots, but 104 stars and no maintenance history are
  below the project’s dependency threshold. It is an idea source only.

## Verified upstream code snapshots

The ignored local research cache contains read-only shallow clones used to
verify actual routes and runbooks:

- LightRAG v1.5.4: commit `9a45b64c2ee25b1d806e90db926a8af37480bb16`.
- Kelsus main: commit `97af85a349f25fe715fda8dddcb264f2e17a0ca8`.

LightRAG’s source confirms bounded integration primitives that match this
product: `POST /documents/upload`, track/status inventory, per-document delete,
`POST /query`, query modes including `hybrid`/`mix`, optional reranking, and
references with chunk content. Its upload implementation also validates paths,
file types and maximum size. These are upstream patterns to reuse, not code to
copy into the public repository.

## Selection update

The V2 default remains the existing Python/FastAPI/Haystack architecture because
it has the smallest reviewed attack surface, already preserves DuckDB/SQLGlot
governance, runs as one Windows wheel, and can be tested without external model
or graph services.

The new evidence changes two deliverables:

1. Keep a documented **LightRAG synthetic A/B profile** for larger cross-document
   corpora. It is a bounded optional research candidate, pinned to stable v1.5.4,
   not a silent replacement.
2. Copy the **Kelsus delivery workflow**, not its AWS stack: synthetic corpus,
   explicit gold evaluation cases, measured JSON results, no-egress ADR,
   acceptance ledger and runbook.

Adoption gate for LightRAG: a stable release containing the reviewed v1.5.5rc1
security fixes must first pass a fresh threat model; it must then beat the embedded baseline on cross-document
correctness/citation coverage using the same synthetic HVAC evaluation set,
while still passing Windows offline, endpoint allowlist and no-unapproved-egress
tests. Popularity alone is not an adoption gate.
