# Architecture

The application owns a small interface and policy layer. Mature projects own retrieval, dataframes, schema validation, analytical SQL, and parsing.

## Modules

- `contract.py`: validates a versioned Project Package and blocks path traversal.
- `providers.py`: selects local Haystack retrieval or the AnythingLLM query adapter.
- `company_api.py`: explicit-host OpenAI-compatible client for bounded evidence requests.
- `analytics.py`: Polars ingestion, Pandera validation, and read-only DuckDB snapshots.
- `analysis.py`: approved natural-language analysis intents.
- `sql_guard.py`: SQLGlot AST checks, table allowlists, and row limits.
- `release_guard.py`: public-tree leak prevention.
- `web.py`: FastAPI workbench and security headers.

Knowledge and data modules share project identity and audit concepts, but they do not share tool authority. Retrieved document text can never obtain SQL, file, network, or Shell permissions.

## Extension Points

AnythingLLM remains a separate, version-pinned service. Future semantic layers such as WrenAI must implement a narrow adapter and pass the same SQL and egress gates before activation.
