# Company Deployment Gate

Public-repository verification proves only that the synthetic application works. It does not approve real company data.

## Required Decisions

1. Confirm whether the company OpenAI-compatible endpoint remains inside an approved data boundary.
2. Confirm whether document fragments, schema metadata, and aggregate results may be sent to that endpoint.
3. Select company-internal embeddings or an IT-approved local embedding component.
4. Approve Windows, Python/native wheels, localhost binding, certificates, artifact transfer, and rollback.
5. Store the real Project Package outside the Git clone.

## Runtime Controls

- deny outbound traffic except loopback and explicitly approved company endpoints;
- disable AnythingLLM telemetry, Community Hub, connectors, Agents, Web access, MCP, and automatic downloads;
- set `HAYSTACK_TELEMETRY_ENABLED=False`;
- prohibit DuckDB extension installation, attachment, file readers, and write SQL;
- use an approved credential store instead of environment files committed to source control;
- retain version, SBOM, hash manifest, test evidence, and rollback package.

## Acceptance Evidence

- clean installation on the target Windows image;
- packet capture or firewall logs showing only approved destinations;
- cited knowledge evaluation, refusal evaluation, data golden tests, and SQL mutation tests;
- secret scan, SBOM, dependency audit, artifact hashes, and rollback rehearsal.
