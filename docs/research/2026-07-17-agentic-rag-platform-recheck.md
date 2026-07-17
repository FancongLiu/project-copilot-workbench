# Agentic RAG platform recheck — 2026-07-17

This pass was triggered by the Chairman's correction that the project files are
small and a useful workbench should not require a heavy deployment merely to
query them. The built-in WebSearch endpoint returned `404` for both search and
direct-open requests. Current evidence below therefore comes from the official
GitHub API, official repositories, release pages and upstream documentation.

## Local footprint evidence

Measured on the development machine before selection:

| Item | Measured size |
|---|---:|
| Entire repository including local environment and artifacts | 887.82 MB |
| Repository-local Python virtual environment | 762.85 MB |
| Test/browser/runtime artifacts | 73.87 MB |
| Remaining source, configuration and business data | about 51 MB |
| Built release wheel | about 4.6 MB |

The business corpus is not the storage problem. The earlier 50 GB figure is
the official deployment allowance for a complete multi-service platform such
as RAGFlow, not a requirement of Project Copilot or its imported files. At the
measurement point E: had 0.42 GB free, while D: had 46.86 GB and F: had 31.88
GB. Optional isolated experiments belong on D: or F:, not inside the active E:
repository.

## Current official GitHub snapshot

Counts are GitHub API observations, not quality scores.

| Candidate | 2026-07-17 evidence | Current fit |
|---|---|---|
| [Haystack](https://github.com/deepset-ai/haystack) | 25,916 stars, 2,924 forks, Apache-2.0, latest release [v2.31.0](https://github.com/deepset-ai/haystack/releases/tag/v2.31.0) on 2026-07-08, repository pushed 2026-07-16 | Keep. Its bounded Agent, ToolInvoker and Python component model already integrate with the existing DuckDB/SQLGlot safety boundary and Windows wheel. |
| [WrenAI](https://github.com/Canner/WrenAI) | 15,824 stars, 1,815 forks, latest release [wren-v0.13.0](https://github.com/Canner/WrenAI/releases/tag/wren-v0.13.0) on 2026-07-13, repository pushed 2026-07-16 | Treat current Wren as a lightweight NL2SQL/semantic-layer and MCP component, not the old all-in-one Classic product. It is a future governed data-tool candidate, not a document-RAG replacement. |
| [DB-GPT](https://github.com/eosphoros-ai/DB-GPT) | 19,493 stars, 2,827 forks, MIT, latest release [v0.8.1](https://github.com/eosphoros-ai/DB-GPT/releases/tag/v0.8.1) on 2026-06-18, repository pushed 2026-07-16 | The only feasible current full-stack challenger on this machine. Test in WSL2 on D:/F: only after the same provider proves Chat Completions plus embedding compatibility; the accepted local model path currently uses Responses. |
| [RAGFlow](https://github.com/infiniflow/ragflow) | 85,213 stars, 9,950 forks, Apache-2.0, latest release [v0.26.4](https://github.com/infiniflow/ragflow/releases/tag/v0.26.4) on 2026-07-07, repository pushed 2026-07-16 | Defer. The official self-hosted baseline requires Docker and a multi-service allowance including 16 GB RAM and 50 GB disk; its SQL Agent does not directly preserve the current DuckDB path. |

## Measured architecture decision

The lightweight candidate was tested with the authorized Responses API model
over the same 52-case synthetic HVAC corpus. The historical baseline measured
46.2% behavior / 42.3% tool contract / 34.1% evidence contract. Final v35
executed 52/52 requests with zero execution failures and measured 52/52
behavior, 52/52 tool contract and 44/44 exact evidence contract. Those are
automatic structure and grounding checks, not answer correctness; the separate
HVAC adjudication remains the acceptance authority for response usefulness.
The final v35 adjudication accepted 52/52 answers and is SHA-bound to the raw
result; this does not generalize beyond the frozen synthetic corpus and model
configuration.

The improvement came from small, deep modules rather than a larger platform:

- typed read-only snapshot inspection for data quality, control events and
  alarm events;
- a governed metric-extreme tool for minimum/maximum windows without inventing
  alarm thresholds;
- current-versus-superseded configuration authority rules;
- deterministic safety refusal for writes, equipment control and cross-project
  access;
- a resumable raw-evidence benchmark runner.
- provider-failure containment that aborts after two consecutive upstream
  failures and resumes only after provenance validation;
- exclusive event-end semantics, a 60-second formal compressor mismatch
  threshold, alarm-code filtering, and common controller event-name aliases.

Decision: continue with Haystack plus DuckDB for the current deliverable. Keep
Wren as a future NL2SQL component test and DB-GPT as the only near-term
full-stack challenger. Do not install RAGFlow on this machine merely to claim a
framework bake-off; its infrastructure cost is unrelated to the small project
corpus and would not improve the measured business answer by itself.
