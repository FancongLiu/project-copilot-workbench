# Project Package Contract

A real Project Package is stored outside the public Git clone. The Web UI and
CLI can also import a ZIP containing supported source files; archive paths are
validated against traversal, symlinks, duplicate destinations, file-count,
compressed-size, and uncompressed-size limits before extraction.

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

The `0.1` manifest declares project identity, document and dataset roots, and
capability flags. Every declared path is resolved and required to remain inside
the package root.

V2 source metadata uses these categories:

- `background`
- `configuration`
- `meeting`
- `SOP`
- `decision`
- `dataset`

The base installation parses `.md`, `.txt`, `.json`, and `.csv`. The optional
Docling and `docling-haystack` extra handles supported PDF and Office formats
through structured `HybridChunker` output in the same source inventory and
index. It requires an approved local tokenizer directory through
`PROJECT_COPILOT_DOCLING_TOKENIZER_PATH`; PDF parsing additionally requires
`PROJECT_COPILOT_DOCLING_ARTIFACTS_PATH`. Runtime download is not allowed. A
parser failure remains visible as a source with `status=error`; it is not
silently omitted.

For ZIP import, directory names such as `meetings/`, `sops/`, `decisions/`, and
`configuration/` determine the default category. Selected-file uploads accept
an explicit category from the caller. Filenames are normalized to safe
basenames and must be unique inside one workspace.

The public synthetic example is deliberately realistic enough for acceptance
testing but contains no company facts. Company-specific schemas, aliases,
units, metrics, prompts, documents, telemetry, and evaluation cases stay on the
company computer.

## Defrost-analysis package extension

A project that requests defrost logic review should also contain:

```text
docs/source/background/asset-register.*
docs/source/configuration/bas-point-schedule.*
docs/source/configuration/defrost-asset-context.json
docs/source/configuration/defrost-control-sequence.*
docs/source/configuration/defrost-rules.json
docs/source/meetings/*controls-or-change-review*
docs/source/sops/*defrost-diagnostic-review*
datasets/raw/*defrost-telemetry*.csv
```

The machine-readable rule pack must bind the rule to an asset, controller
model, firmware version, source file/section, timezone, sample interval,
tolerance, and compliance scope. The human-readable source remains the
engineering authority; the JSON is a reviewed executable projection, not a
replacement. Exact OEM compliance must be blocked when the exact
model/firmware documentation or required points are absent.
