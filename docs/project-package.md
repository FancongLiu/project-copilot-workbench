# Project Package Contract

A Project Package is stored outside the public Git clone.

```text
project-id/
|-- project.yaml
|-- docs/source/
|-- datasets/raw/
|-- config/
|-- schemas/
|-- metrics/
`-- tests/
```

The `0.1` manifest declares project identity, document and dataset roots, and capability flags. Every declared path is resolved and required to remain inside the package root.

The synthetic example is intentionally minimal. Company-specific schemas, aliases, units, metrics, prompts, and evaluation cases stay on the company computer.
