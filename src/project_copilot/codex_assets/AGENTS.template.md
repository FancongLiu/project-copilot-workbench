# Project Analysis Rules

This workspace contains a fully synthetic project used only for evaluation.

## Evidence and output

- Lead with the direct engineering conclusion, then the smallest useful table.
- Cite exact human-readable source filenames, never internal IDs or absolute paths.
- Separate observed facts, project-specific contracts, and unproven inference.
- Historical questions with explicit dates do not require today's date.

## Data access

- Never scan, print, or import the full telemetry CSV.
- Use only the required `hvac` MCP server for telemetry calculations.
- Allowed MCP tools are `schema`, `data_quality`, and `cop_ranking`.
- Do not construct shell SQL or search for a database file. Combine MCP results with project documents.
- Do not install extensions, attach databases, export data, create files, or modify project data.

## Tool discipline

- Prefer a few targeted operations over broad discovery.
- Treat MCP failure or missing project evidence as a reason to stop, not to improvise another data path.
- Keep intermediate output bounded and never echo secrets or environment variables.
