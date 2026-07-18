# Project Analysis Rules

This workspace contains a fully synthetic project used only for evaluation.

## Evidence and output

- Lead with the direct engineering conclusion, then the smallest useful table.
- Cite exact human-readable source filenames, never internal IDs or absolute paths.
- Separate observed facts, project-specific contracts, and unproven inference.
- Historical questions with explicit dates do not require today's date.
- When rejecting an unsupported number proposed by the user, start that sentence
  explicitly with `无法确认` or `拒绝`; do not repeat it through double-negative
  wording that could be mistaken for a factual claim.

## Data access

- Do not execute Shell, PowerShell, Python, file-write, or Web-search commands.
- Use `search_project_knowledge` to inspect approved documents and obtain exact
  original filenames plus excerpts.
- Never scan, print, or import the full telemetry CSV.
- Use only the required `hvac` MCP server for telemetry calculations.
- Prefer typed MCP tools: `inspect_hvac_snapshot`,
  `inspect_configuration_history`, `inspect_configuration_change_effect`, and
  `inspect_metric_extreme`.
- Use `schema`, `data_quality`, and `cop_ranking` for fixed audits. Use
  `query_hvac_database` only for one bounded read-only SELECT when no typed
  inspection covers the question.
- Never construct shell SQL or search for a database file. Combine governed
  database results with `search_project_knowledge` evidence.
- Do not install extensions, attach databases, export data, create files, or modify project data.

## Tool discipline

- Prefer a few targeted operations over broad discovery.
- Treat MCP failure or missing project evidence as a reason to stop, not to improvise another data path.
- Keep intermediate output bounded and never echo secrets or environment variables.
