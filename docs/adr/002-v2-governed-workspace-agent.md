# ADR 002: Governed workspace Agent for V2

- Status: Accepted
- Date: 2026-07-15
- Evidence: `docs/research/2026-07-15-v2-framework-selection.md`

## Context

V1 has a safe read-only analytics core, but its document index is process-local,
the Web App cannot import or manage sources, the company model client is outside
the primary workflow, and four string-matched intents stand in for reasoning.
The required product is a company project copilot: users first create a project,
import its complete approved context, and then ask questions that may require
iterative knowledge and data tools.

## Decision

Build one bounded vertical product around five deep modules:

1. `WorkspaceManager`: isolated runtime directories and immutable generation
   snapshots. Upload/archive/CLI import, re-index and delete stage a complete
   source/inventory/index generation and atomically switch one state pointer.
2. `ProjectIndexer`: mature parser adapters, Haystack chunking, durable document
   store, BM25 + embedding retrieval and fused citations.
3. `GovernedAnalyticsTool`: typed semantic operations over the existing
   validated, read-only DuckDB snapshot. No model-generated SQL is executed.
4. `ProjectAgent`: Haystack Agent with search, configuration, meeting/decision,
   analytics, source-inspection, and clarification tools. Enforce step, tool,
   and wall-time budgets and expose only a concise activity trace.
5. FastAPI/Web UI: workspace switch/create, import status, source inventory,
   Copilot conversation, citations, trace, and operations/security views.

The production generator is the allowlisted company OpenAI-compatible endpoint.
The synthetic demo uses a deterministic test double through the same Agent/tool
boundary. Hidden chain-of-thought is never requested, stored, or displayed.

## Security invariants

- Runtime data is outside the Git repository by default.
- Archive extraction rejects traversal, symlinks, excessive files and size.
- Imported sources have explicit category metadata and bounded sizes.
- Non-loopback model endpoints require HTTPS and an exact hostname allowlist.
- Model tools cannot access shell, Python execution, Web, MCP, equipment, or
  unrestricted SQL.
- DuckDB stays read-only with external access and extension loading disabled.
- Telemetry DuckDB files are content-addressed and immutable so Windows readers
  never race an in-place database replacement.
- Every answer returns auditable citations or a refusal/clarification.

## Consequences

- V2 stays installable as a normal Python wheel and preserves the V1 safety
  core.
- Office/PDF parsing is a larger optional offline bundle because Docling and its
  models must be reviewed and mirrored separately.
- Embedded Haystack persistence is appropriate for a personal/company PC. The
  documented Qdrant seam is used when measured corpus size or concurrency makes
  the embedded store insufficient.
- Deterministic acceptance proves orchestration and grounding behavior, not a
  universal answer-quality percentage for an unknown company model.
