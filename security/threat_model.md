# Threat Model

| Threat | Default control | Evidence |
|---|---|---|
| Public-repository data leak | Physical package separation and release guard | `python -m project_copilot.release_guard .` |
| Prompt injection | Local retrieval has no tools; AnythingLLM uses query mode | Injection tests and request audit |
| SQL mutation or exfiltration | SQLGlot single-SELECT policy and DuckDB read-only connection | SQL mutation corpus |
| Hidden network calls | Telemetry disabled and demo zero-egress test | Firewall or packet capture |
| Cross-project retrieval | One provider and runtime database per Project Package | Project-isolation tests |
| Malicious file parsing | Initial local mode accepts text/Markdown only | File-type policy |
| Result overclaiming | Citations, approved metrics, SQL visibility, and refusal | Golden evaluations |

The browser UI never receives credentials and does not expose a generic proxy endpoint.
