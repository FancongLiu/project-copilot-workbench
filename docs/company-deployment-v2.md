# Company Deployment Runbook — V2

This runbook moves a reviewed Project Copilot Workbench release from a
personal development PC, through the public GitHub repository, to a restricted
Windows company PC. It is written for version `0.2.0` and must be reviewed
again when the version, dependency locks, runtime schema, or network policy
changes.

The public repository contains only generic code and synthetic HVAC data. Real
Project Packages, company model names, internal URLs, certificates, credentials,
runtime indexes, logs, and evaluation results remain on the company PC.

## Supported deployment profiles

| Profile | Status | Use |
|---|---|---|
| Windows wheel + offline wheelhouse | **Default and executable** | Single-user company PC, loopback Web App, company-approved OpenAI-compatible endpoint |
| Source checkout + locked dependencies | Development only | Personal PC and controlled company test VM |
| Git bundle/source archive | Transfer format | Moves reviewed source without giving the company PC public Internet access |
| Project Copilot runtime container | **Not release-approved** | The current CLI intentionally binds only to loopback inside its host. Publishing a container port would require a new authenticated proxy/binding ADR and tests. |
| LightRAG service | Synthetic loopback A/B only | Stable v1.5.4 predates security fixes first shipped in v1.5.5rc1. Do not use company data or treat it as a deployment baseline; it is not wired into the application. See [LightRAG direct deploy](light-rag-direct-deploy.md). |

Do not weaken loopback binding to make a container or LAN demo convenient.

## 1. Release flow and custody boundary

1. Develop and test with `examples/synthetic_hvac` only.
2. Run the complete local verification gate.
3. Commit and push without force-push. Require green GitHub Actions for the
   exact commit.
4. Build the wheel, Windows dependency wheelhouse, SBOM, source bundle, and
   SHA-256 manifest from that exact commit.
5. Transfer the immutable release directory through the company-approved
   channel. Do not copy a developer virtual environment.
6. Verify every hash on the company PC before installation.
7. Install without an Internet index and start with synthetic data first.
8. Configure the company endpoint and import a real Project Package only after
   IT, data-owner, and security acceptance.

Record the Git commit, release version, Python version, Windows edition/build,
CPU architecture, CI run URLs, approvers, and artifact manifest together.

## 2. Build the release on the personal PC

Use Windows, Python 3.12, and the same CPU architecture as the company PC.
Start in a clean checkout of the reviewed commit.

```powershell
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path .).Path
$Commit = (git rev-parse HEAD).Trim()
$Release = Join-Path $Repo "artifacts\release-$Commit"

git status --short
if (git status --porcelain) { throw "Release checkout is not clean" }

scripts\bootstrap.cmd
scripts\verify.cmd

& ".venv\Scripts\python.exe" -m pip install `
  build==1.5.0 pip-audit==2.10.1 cyclonedx-bom==7.3.0
& ".venv\Scripts\python.exe" -m pip_audit `
  -r requirements.runtime.lock --strict
& ".venv\Scripts\python.exe" -m build --wheel

New-Item -ItemType Directory -Force `
  "$Release\wheel", "$Release\wheelhouse", "$Release\evidence" | Out-Null
Copy-Item dist\*.whl "$Release\wheel\"
Copy-Item requirements.runtime.lock, requirements.documents.lock, requirements.documents-ci.lock, `
  pyproject.toml, LICENSE, NOTICE, `
  THIRD_PARTY_NOTICES.md "$Release\"
Copy-Item config\company-v2.example.ps1 "$Release\"

& ".venv\Scripts\python.exe" -m pip download `
  --only-binary=:all: `
  --require-hashes -r requirements.runtime.lock `
  --dest "$Release\wheelhouse"
& ".venv\Scripts\python.exe" -m pip download `
  pip==26.1.2 --dest "$Release\wheelhouse"

git archive --format=zip `
  --output "$Release\project-copilot-source-$Commit.zip" HEAD
git bundle create "$Release\project-copilot-$Commit.bundle" HEAD

git log -1 --format=fuller | Set-Content `
  "$Release\evidence\git-commit.txt" -Encoding utf8
git status --short | Set-Content `
  "$Release\evidence\git-status.txt" -Encoding utf8

$Smoke = Join-Path $Repo "artifacts\wheel-smoke-$Commit"
py -3.12 -m venv $Smoke
& "$Smoke\Scripts\python.exe" -m pip install `
  --no-index --find-links "$Release\wheelhouse" pip==26.1.2
& "$Smoke\Scripts\python.exe" -m pip install `
  --no-index --find-links "$Release\wheelhouse" `
  --require-hashes -r requirements.runtime.lock
& "$Smoke\Scripts\python.exe" -m pip install `
  --no-index --no-deps (Get-ChildItem "$Release\wheel\*.whl").FullName
& "$Smoke\Scripts\python.exe" -m pip check
& ".venv\Scripts\cyclonedx-py.exe" environment `
  "$Smoke\Scripts\python.exe" --pyproject pyproject.toml `
  --mc-type application --output-reproducible --output-format JSON `
  --output-file "$Release\evidence\sbom.cdx.json"
```

`pip download` must complete on the same Windows/Python/architecture target. If
it produces a source archive (`.tar.gz` or `.zip`) for a runtime dependency,
stop: the offline install would need a compiler/toolchain and is not the
approved wheel-only profile.

Create a reproducible file manifest after all artifacts are present. Run the
manifest block only after every selected optional bundle in the following
subsections has been built and verified. If any artifact changes later,
regenerate `SHA256SUMS.json`; never transfer an older manifest:

```powershell
$Base = (Resolve-Path $Release).Path
$ManifestPath = Join-Path $Base "SHA256SUMS.json"
$Rows = Get-ChildItem $Base -Recurse -File |
  Where-Object FullName -ne $ManifestPath |
  Sort-Object FullName |
  ForEach-Object {
    [pscustomobject]@{
      path = $_.FullName.Substring($Base.Length + 1).Replace("\", "/")
      bytes = $_.Length
      sha256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
  }
$Rows | ConvertTo-Json -Depth 3 | Set-Content $ManifestPath -Encoding utf8
Get-FileHash $ManifestPath -Algorithm SHA256
```

Store the manifest hash in the release ticket through a different trusted
channel than the artifact transfer.

### Optional Office/PDF parser bundle

The base wheel supports Markdown, UTF-8 text, JSON, and CSV without Docling.
PDF/DOCX/PPTX/XLSX parsing requires the separately reviewed `documents` extra.
The repository pins `docling==2.113.0` and `docling-haystack==1.2.0` and ships
their complete production dependency set in `requirements.documents.lock`.
`requirements.documents-ci.lock` is constrained to that exact production graph
and adds only the parser-smoke dependencies. It is used to build one superset
wheelhouse; the production venv still installs the smaller production lock.
Parser artifacts and the tokenizer remain separate immutable model bundles.

Prepare and hash a separate bundle only if company acceptance explicitly needs
these formats. Prefetch models on the connected release-builder, never on the
restricted company PC:

```powershell
New-Item -ItemType Directory -Force "$Release\wheelhouse-documents" | Out-Null
$DocumentBuilder = Join-Path $Repo "artifacts\document-builder-$Commit"
py -3.12 -m venv $DocumentBuilder
& "$DocumentBuilder\Scripts\python.exe" -m pip install --upgrade pip==26.1.2
& "$DocumentBuilder\Scripts\python.exe" -m pip install `
  --require-hashes -r requirements.documents-ci.lock
& "$DocumentBuilder\Scripts\python.exe" -m pip check
& "$DocumentBuilder\Scripts\python.exe" -m pip download `
  --only-binary=:all: `
  --require-hashes -r requirements.documents-ci.lock `
  --dest "$Release\wheelhouse-documents"
$UnexpectedParserArtifacts = Get-ChildItem "$Release\wheelhouse-documents" -File |
  Where-Object Extension -ne ".whl"
if ($UnexpectedParserArtifacts) {
  $UnexpectedParserArtifacts.FullName
  throw "Parser wheelhouse contains a non-wheel artifact"
}

New-Item -ItemType Directory -Force `
  "$Release\models\docling", "$Release\models\docling-tokenizer" | Out-Null
& "$DocumentBuilder\Scripts\python.exe" scripts\prefetch_docling_assets.py `
  --artifacts-dir "$Release\models\docling" `
  --tokenizer-dir "$Release\models\docling-tokenizer" `
  --tokenizer-revision 1110a243fdf4706b3f48f1d95db1a4f5529b4d41 `
  --layout-model-revision b5b4bd59ad2b69aab715e9b1f1dfd74394c45fd4
```

The wheel download is deliberately wheel-only; any missing wheel is a release
blocker rather than permission to build an sdist on the restricted PC. The
release manifest must include every parser wheel, model/tokenizer file and its
license/provenance record. Both revisions above are immutable Hugging Face
commits, not floating default branches; record each repository ID, revision and
downloaded file hash in release evidence. After building this optional bundle,
regenerate `SHA256SUMS.json` with the final manifest block above.

Before approval, prove in an isolated no-egress VM that all parser dependencies
and model assets are present. The structured converter uses Docling
`HybridChunker` and requires an approved local Hugging Face tokenizer directory
through `PROJECT_COPILOT_DOCLING_TOKENIZER_PATH`; PDFs additionally require
`PROJECT_COPILOT_DOCLING_ARTIFACTS_PATH`. The application passes the approved
artifact directory into `PdfPipelineOptions`, disables OCR/table extraction for
this baseline, disables remote services, uses `local_files_only=true` for the
tokenizer, and fails instead of downloading at runtime. GitHub CI runs a real
synthetic PDF/DOCX smoke with Hugging Face lookups forced offline. Repeat that
test on the exact Windows company build: require chunks with section/page
metadata, application-restart persistence, and successful search. A successful
`pip install` alone does not prove offline parsing.

On the isolated Windows acceptance VM, expand the transferred source archive,
create a disposable parser-test venv, and run the exact integration test with
network model lookup disabled:

```powershell
$Release = "D:\ProjectCopilot\releases\REPLACE_WITH_COMMIT"
$ParserTest = "D:\ProjectCopilot\parser-acceptance\venv"
$ParserSource = "D:\ProjectCopilot\parser-acceptance\source"
New-Item -ItemType Directory -Force $ParserSource | Out-Null
Expand-Archive `
  -LiteralPath "$Release\project-copilot-source-REPLACE_WITH_COMMIT.zip" `
  -DestinationPath $ParserSource -Force
py -3.12 -m venv $ParserTest
& "$ParserTest\Scripts\python.exe" -m pip install `
  --no-index --find-links "$Release\wheelhouse" pip==26.1.2
& "$ParserTest\Scripts\python.exe" -m pip install `
  --no-index --find-links "$Release\wheelhouse-documents" `
  --require-hashes -r "$Release\requirements.documents-ci.lock"
& "$ParserTest\Scripts\python.exe" -m pip install `
  --no-index --no-deps (Get-ChildItem "$Release\wheel\*.whl").FullName
& "$ParserTest\Scripts\python.exe" -m pip check
$env:DOCLING_ARTIFACTS_PATH = "$Release\models\docling"
$env:PROJECT_COPILOT_DOCLING_ARTIFACTS_PATH = "$Release\models\docling"
$env:PROJECT_COPILOT_DOCLING_TOKENIZER_PATH = "$Release\models\docling-tokenizer"
$env:PROJECT_COPILOT_RUN_DOCLING_INTEGRATION = "1"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
Push-Location $ParserSource
& "$ParserTest\Scripts\python.exe" -m pytest tests\test_docling_integration.py -q
Pop-Location
```

Run this while outbound traffic is blocked and capture the process/network
evidence. The test creates synthetic PDF/DOCX inputs, parses them, imports them,
restarts the indexer, searches the result, and checks PDF page metadata.

### Optional local reranker bundle

The embedded retrieval path can add the maintained
`sentence-transformers-haystack==0.1.1` cross-encoder ranker after BM25/dense
candidate fusion. This is an optional heavy extra, not a public network call:

```powershell
New-Item -ItemType Directory -Force "$Release\wheelhouse-reranking" | Out-Null
& ".venv\Scripts\python.exe" -m pip download `
  ".[reranking]" --dest "$Release\wheelhouse-reranking"
```

Download an approved cross-encoder model into a separate immutable local
directory, record every file hash and license, and prohibit remote code. Enable
it only with both `PROJECT_COPILOT_RERANKER_MODEL_PATH` and
`PROJECT_COPILOT_ACK_RERANKER_APPROVED=true`. Compare the same frozen HVAC gold
set before/after with Haystack Recall, MRR and NDCG plus latency and memory. Do
not enable a reranker merely because it exists; it must improve the stated
acceptance cases within the company PC resource budget.

## 3. Verify transfer integrity on the company PC

Copy the release into an immutable staging directory such as
`D:\ProjectCopilot\releases\<commit>`. Verify before extracting or installing:

```powershell
$ErrorActionPreference = "Stop"
$Release = "D:\ProjectCopilot\releases\REPLACE_WITH_COMMIT"
$Rows = Get-Content "$Release\SHA256SUMS.json" -Raw | ConvertFrom-Json
$Failures = foreach ($Row in $Rows) {
  $Path = Join-Path $Release $Row.path
  if (-not (Test-Path -LiteralPath $Path)) {
    "missing: $($Row.path)"
    continue
  }
  $Actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($Actual -ne $Row.sha256) { "hash mismatch: $($Row.path)" }
}
if ($Failures) { $Failures; throw "Release verification failed" }

git bundle verify "$Release\project-copilot-REPLACE_WITH_COMMIT.bundle"
```

Also compare the `SHA256SUMS.json` hash with the independently recorded ticket
value. Quarantine the whole release if any value differs.

## 4. Company PC directory layout

Keep code, runtime state, source data, logs, backups, and secrets separate.

```text
D:\ProjectCopilot\
|-- app\0.2.0\                 # installed venv / launcher
|-- releases\<commit>\         # immutable received artifacts
|-- runtime\                    # generated indexes and DuckDB snapshots
|-- projects\<project-id>\      # approved Project Packages
|-- logs\                       # process/reverse-proxy logs
`-- backups\                    # encrypted, access-controlled backups
```

Do not place real projects under the Git checkout, wheelhouse, or public
release directory.

## 5. Offline Windows installation

Use a company-approved 64-bit Python 3.12 installation. Create a new venv for
every application release; do not upgrade an in-use environment in place.

```powershell
$ErrorActionPreference = "Stop"
$Release = "D:\ProjectCopilot\releases\REPLACE_WITH_COMMIT"
$App = "D:\ProjectCopilot\app\0.2.0"

py -3.12 -m venv "$App\venv"
& "$App\venv\Scripts\python.exe" -m pip install `
  --no-index --find-links "$Release\wheelhouse" pip==26.1.2
& "$App\venv\Scripts\python.exe" -m pip install `
  --no-index --find-links "$Release\wheelhouse" `
  --require-hashes -r "$Release\requirements.runtime.lock"
& "$App\venv\Scripts\python.exe" -m pip install `
  --no-index --no-deps (Get-ChildItem "$Release\wheel\*.whl").FullName

& "$App\venv\Scripts\python.exe" -m pip check
& "$App\venv\Scripts\project-copilot.exe" --help
```

If and only if the separately reviewed Office/PDF bundle passed the isolated
parser acceptance above, replace the runtime dependency install command with
the following production parser install. Do not install the CI lock in the
production venv:

```powershell
& "$App\venv\Scripts\python.exe" -m pip install `
  --no-index --find-links "$Release\wheelhouse-documents" `
  --require-hashes -r "$Release\requirements.documents.lock"
& "$App\venv\Scripts\python.exe" -m pip check
```

Smoke-test with synthetic data and a disposable runtime before real data:

```powershell
$Source = "D:\ProjectCopilot\releases\REPLACE_WITH_COMMIT\project-copilot-source-REPLACE_WITH_COMMIT.zip"
$SmokeSource = "D:\ProjectCopilot\smoke-source"
Expand-Archive -LiteralPath $Source -DestinationPath $SmokeSource -Force

$env:PROJECT_COPILOT_MODEL_MODE = "deterministic"
$env:PROJECT_COPILOT_KNOWLEDGE_PROVIDER = "local"
$env:HAYSTACK_TELEMETRY_ENABLED = "False"
& "$App\venv\Scripts\project-copilot.exe" `
  --project "$SmokeSource\examples\synthetic_hvac" `
  --runtime "D:\ProjectCopilot\runtime-smoke" --port 8788
```

From a second terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8788/api/health
```

Expected: HTTP 200, `status=ok`, `project_id=synthetic-hvac-demo`, and
`egress_mode=loopback-only`. Stop the smoke process after the check.

## 6. Company OpenAI-compatible endpoint

The production Agent path is enabled only when all of these are present:

| Setting | Meaning |
|---|---|
| `PROJECT_COPILOT_MODEL_MODE=company` | Select the real company model path |
| `PROJECT_COPILOT_OPENAI_BASE_URL` | Full OpenAI-compatible API base, normally ending in `/v1` |
| `PROJECT_COPILOT_OPENAI_MODEL` | Company-approved model/deployment identifier |
| `PROJECT_COPILOT_OPENAI_API_KEY` | Secret injected at process launch; never committed or stored in the example file |
| `PROJECT_COPILOT_ALLOWED_HOSTS` | Comma-separated exact hostnames; no URL paths or wildcard domains |
| `PROJECT_COPILOT_CA_BUNDLE` | Optional PEM bundle for an internal/private CA |
| `PROJECT_COPILOT_EMBEDDING_MODEL` | Optional approved embedding model; omit to keep local BM25-only retrieval |
| `PROJECT_COPILOT_EMBEDDING_BASE_URL` | Optional separate OpenAI-compatible embedding base URL; defaults to the chat base URL |
| `PROJECT_COPILOT_EMBEDDING_API_KEY` | Optional separately injected embedding secret; defaults to the chat API key |
| `PROJECT_COPILOT_ACK_EMBEDDINGS_APPROVED=true` | Required explicit acknowledgement before any document text is sent for embeddings |

Load the reviewed template after the secret has been injected by the approved
service launcher or secret manager:

```powershell
$env:PROJECT_COPILOT_OPENAI_API_KEY = Get-Secret `
  -Name "ProjectCopilot-OpenAI" -AsPlainText

. "D:\ProjectCopilot\releases\REPLACE_WITH_COMMIT\company-v2.example.ps1" `
  -OpenAIBaseUrl "https://ai-gateway.example.invalid/v1" `
  -OpenAIModel "approved-model-id" `
  -AllowedHosts @("ai-gateway.example.invalid") `
  -CaBundle "C:\CompanyPKI\company-ca-bundle.pem"
```

The example script may be blocked by an enterprise PowerShell execution
policy. Do not weaken machine/user policy to `Unrestricted`. Use the company's
approved script-signing process or configure the same variables in the approved
service launcher, then retain the signature/policy evidence.

The safest first company acceptance run leaves embeddings disabled and uses
the persistent local BM25 index. Enable hybrid retrieval only after the data
owner approves sending imported document chunks to the embedding endpoint:

```powershell
$env:PROJECT_COPILOT_EMBEDDING_API_KEY = Get-Secret `
  -Name "ProjectCopilot-Embeddings" -AsPlainText

. "D:\ProjectCopilot\releases\REPLACE_WITH_COMMIT\company-v2.example.ps1" `
  -OpenAIBaseUrl "https://ai-gateway.example.invalid/v1" `
  -OpenAIModel "approved-model-id" `
  -AllowedHosts @("ai-gateway.example.invalid") `
  -EmbeddingModel "approved-embedding-id" `
  -EmbeddingBaseUrl "https://ai-gateway.example.invalid/v1" `
  -ApproveEmbeddings
```

Record the embedding model/deployment identifier, endpoint host, vector
dimension, approval ticket and activation time. Changing the embedding model
requires a full re-index into a new or backed-up runtime; never compare old and
new indexes as if they were equivalent. Disabling the acknowledgement must
fail startup rather than silently sending text.

When the separate parser/reranker bundles have passed their gates, the same
configuration template accepts local directories:

```powershell
. "D:\ProjectCopilot\releases\REPLACE_WITH_COMMIT\company-v2.example.ps1" `
  -OpenAIBaseUrl "https://ai-gateway.example.invalid/v1" `
  -OpenAIModel "approved-model-id" `
  -AllowedHosts @("ai-gateway.example.invalid") `
  -DoclingTokenizerPath "D:\ProjectCopilot\models\docling-tokenizer" `
  -DoclingArtifactsPath "D:\ProjectCopilot\models\docling" `
  -RerankerModelPath "D:\ProjectCopilot\models\approved-cross-encoder" `
  -ApproveReranker
```

`Get-Secret` is an example interface; use only the company's approved vault.
Do not put the key in a `.ps1`, `.env`, scheduled-task argument, GitHub secret
output, or command history.

The application validates that the base URL hostname is in the explicit
allowlist and requires HTTPS for non-loopback hosts. It creates its HTTP client
with environment proxy inheritance disabled. Therefore:

- `HTTP_PROXY`/`HTTPS_PROXY` are not a supported route for the model call;
- if the company requires a proxy, expose an IT-approved internal API gateway
  as the configured base URL and allowlist that gateway hostname;
- redirects, DNS aliases, certificates, and firewall destinations must still
  be reviewed because an application hostname check is not a network firewall.

### Internal CA/TLS verification

```powershell
Test-Path -LiteralPath "C:\CompanyPKI\company-ca-bundle.pem"
certutil -hashfile "C:\CompanyPKI\company-ca-bundle.pem" SHA256
curl.exe --cacert "C:\CompanyPKI\company-ca-bundle.pem" `
  "https://ai-gateway.example.invalid/v1/models"
```

The final call may require authentication and may return 401/403; that still
proves the TLS handshake. A certificate error is a deployment failure. Never
disable certificate verification.

## 7. Start the production Web App

The app remains loopback-only. If more than one user needs access, place an
independently reviewed authenticated TLS reverse proxy on the same host; do not
change the app's bind host. The proxy must connect to loopback and rewrite the
upstream `Host` header to an allowed value such as `127.0.0.1`; the app's
Trusted Host middleware intentionally rejects arbitrary public hostnames.

```powershell
$App = "D:\ProjectCopilot\app\0.2.0"
$Project = "D:\ProjectCopilot\projects\approved-hvac-project"
$Runtime = "D:\ProjectCopilot\runtime"

& "$App\venv\Scripts\project-copilot.exe" `
  --project $Project --runtime $Runtime --port 8788
```

Open `http://127.0.0.1:8788`. The built-in app has no multi-user identity or
authorization system. Loopback binding is part of the security boundary.

## 8. Project Package import and re-index

An unpacked startup Project Package must contain a valid `project.yaml` and
keep its declared `documents.root` and `datasets.root` inside the package.
See [Project Package contract](project-package.md).

### Bootstrap from an unpacked package

```powershell
& "D:\ProjectCopilot\app\0.2.0\venv\Scripts\project-copilot.exe" `
  --project "D:\ProjectCopilot\projects\approved-hvac-project" `
  --runtime "D:\ProjectCopilot\runtime" --port 8788
```

### Create/import/re-index through the CLI

```powershell
$Exe = "D:\ProjectCopilot\app\0.2.0\venv\Scripts\project-copilot.exe"
$Runtime = "D:\ProjectCopilot\runtime"

& $Exe --runtime $Runtime `
  --create-workspace approved-hvac --display-name "Approved HVAC Project"
& $Exe --runtime $Runtime --workspace approved-hvac `
  --category meeting --import-file "D:\ApprovedImports\meeting-2026-07-01.md"
& $Exe --runtime $Runtime --workspace approved-hvac `
  --category dataset --import-file "D:\ApprovedImports\telemetry.csv"
& $Exe --runtime $Runtime --reindex-workspace approved-hvac
& $Exe --runtime $Runtime --list-workspaces
```

### Web import behavior

- Select or create a project in **Project files**.
- Upload individual files with an explicit category, or upload one ZIP.
- The ZIP importer rejects traversal, symlinks, duplicate basenames, more than
  500 files, or more than 50 MB extracted content.
- An individual file is limited to 5 MB.
- Inventory shows source ID, SHA-256, parser, status, size, and error.
- **Re-index** rebuilds the workspace index from stored sources.
- **Delete** removes one source from the active immutable generation and
  rebuilds the index. Older generations are never served; physical purge is
  governed by the approved backup/retention procedure and must happen while the
  service is stopped.

The Web ZIP importer is a safe source-import operation. It skips `project.yaml`;
it does not replace startup validation of an unpacked Project Package.

## 9. Firewall, telemetry, proxy, and no-egress evidence

### Required runtime policy

- Leave `PROJECT_COPILOT_KNOWLEDGE_PROVIDER=local` unless the separate
  AnythingLLM approval gate is intentionally used.
- Set `HAYSTACK_TELEMETRY_ENABLED=False`.
- Permit the Project Copilot process to contact only the approved company model
  hostname/port when `MODEL_MODE=company`.
- Deny public model APIs, package indexes, code hosting, Web search, MCP,
  arbitrary connectors, and telemetry collectors from the runtime account.
- Keep Windows Update, DNS, time synchronization, endpoint protection, and
  other machine services outside the Project Copilot process evidence set.

Windows Firewall rule design is environment-specific. An application block
rule can override an allow rule, so do not improvise a “block all plus allow
one” rule set on a production PC. Have IT enforce the destination allowlist at
the host firewall, VLAN, secure web gateway, or internal API gateway.

### Evidence collection

1. Run the offline automated test:

   ```powershell
   & ".venv\Scripts\python.exe" -m pytest tests\test_zero_egress.py -q
   ```

2. In an isolated test VM, capture connections while performing startup,
   import, re-index, a cited query, governed analytics, and refusal tests.

   ```powershell
   Get-NetTCPConnection -State Established |
     Sort-Object OwningProcess, RemoteAddress, RemotePort |
     Format-Table -AutoSize

   pktmon start --capture --pkt-size 0 `
     --file-name D:\ProjectCopilot\evidence\project-copilot.etl
   # Perform the scripted acceptance flow here.
   pktmon stop
   pktmon etl2pcap D:\ProjectCopilot\evidence\project-copilot.etl `
     --out D:\ProjectCopilot\evidence\project-copilot.pcapng
   ```

3. Correlate the app PID, DNS results, firewall/proxy logs, and packet capture.
   Record every observed destination and the owner-approved reason.

Synthetic deterministic mode should show no application egress and reports
`egress_mode=loopback-only`. Company chat, embedding, or an approved external
knowledge provider reports `egress_mode=approved-provider` plus only the active
channel labels; it never returns URLs, model IDs, or secrets. Packet evidence in
company mode may show only those approved endpoints plus explicitly documented
name resolution. A quality test is not a no-egress test.

## 10. Logs and audit

Uvicorn emits process/access logs to stdout/stderr. Use a company-approved
service wrapper or process supervisor to write them under
`D:\ProjectCopilot\logs`, rotate them, and forward security events to the SIEM.
Do not log model API keys, authorization headers, full imported documents, or
raw query bodies.

The UI returns a concise tool activity trace and citations for each answer, but
version `0.2.0` does **not** provide a durable multi-user audit ledger. Reverse
proxy access logs can prove time, route, status, client identity, and request ID
without storing content. If the company requires durable question/answer audit,
that is a separate reviewed feature, not a deployment toggle.

Retain at least:

- application version and Git commit;
- artifact and CA bundle hashes;
- startup/shutdown and health events;
- workspace/import/re-index/delete administrative events;
- reverse-proxy authentication/access events;
- firewall/proxy/no-egress evidence;
- backup, restore, migration, and rollback records;
- acceptance results and reviewer sign-off.

## 11. Backup and restore

Runtime state contains source copies, source metadata, persisted Haystack
indexes, and derived DuckDB files. The original approved Project Package is the
source of truth. Back up the Project Package and runtime separately, encrypted
and access-controlled.

Stop the app before a filesystem backup:

```powershell
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Backup = "D:\ProjectCopilot\backups\$Stamp"
New-Item -ItemType Directory -Force $Backup | Out-Null

tar.exe -a -c -f "$Backup\runtime.zip" `
  -C "D:\ProjectCopilot\runtime" .
tar.exe -a -c -f "$Backup\project-package.zip" `
  -C "D:\ProjectCopilot\projects\approved-hvac-project" .
Get-FileHash "$Backup\runtime.zip", "$Backup\project-package.zip" `
  -Algorithm SHA256 | Export-Csv "$Backup\hashes.csv" -NoTypeInformation
```

Restore into new paths; never overwrite the only working copy:

```powershell
$Restore = "D:\ProjectCopilot\restore-test"
New-Item -ItemType Directory -Force "$Restore\runtime", "$Restore\project" | Out-Null
tar.exe -xf "D:\ProjectCopilot\backups\STAMP\runtime.zip" `
  -C "$Restore\runtime"
tar.exe -xf "D:\ProjectCopilot\backups\STAMP\project-package.zip" `
  -C "$Restore\project"
```

Start against the restored paths, run health/inventory/query/analytics checks,
and re-index every restored workspace. A backup is accepted only after a
successful isolated restore rehearsal.

## 12. Migration, rollback, and upgrade

### Upgrade

1. Freeze imports and stop the app.
2. Back up and hash the Project Package and runtime.
3. Install the new wheel into a new versioned venv.
4. Read release notes, dependency/SBOM/license changes, and any runtime schema
   migration instructions.
5. Start the new version against a copied runtime first.
6. Re-index, run the complete acceptance checklist, and observe logs/network.
7. Promote only after sign-off. Keep the old venv and backup immutable.

### Migration to another PC

Transfer the verified application release, Project Package backup, runtime
backup, non-secret configuration, CA bundle through the approved certificate
process, and secret references. Recreate secrets in the target vault rather
than copying plaintext values. Verify hashes and restore to new paths.

### Rollback

1. Stop the failed release.
2. Preserve its logs and runtime for investigation.
3. Restore the pre-upgrade runtime snapshot into a new directory.
4. Start the previous versioned venv against that restored runtime.
5. Run health, inventory, exact lookup, governed analytics, refusal, and
   no-egress checks.
6. Record the rollback reason and evidence.

Do not run an older binary against a runtime already modified by a newer
release unless release notes explicitly prove backward compatibility.

## 13. Troubleshooting

| Symptom | Check | Action |
|---|---|---|
| `Company API host is missing from the explicit allowlist` | Base URL hostname and `PROJECT_COPILOT_ALLOWED_HOSTS` | Add only the exact approved hostname; do not add `*` |
| HTTPS/certificate failure | CA bundle path, expiry, hostname/SAN, system clock | Correct the trust chain; never disable TLS verification |
| Model call fails immediately | `MODEL_MODE`, base URL, model ID, secret injection, gateway route | Test the approved `/v1` contract outside the app without printing secrets |
| Corporate proxy works elsewhere but not here | HTTP client uses `trust_env=False` | Use an approved internal API gateway as the base URL |
| Browser POST/DELETE returns 403 | Missing same-origin header | API clients must send `X-Project-Copilot: 1`; the bundled UI does this |
| Office/PDF source is `error` | Docling extra/model assets unavailable | Install and validate the separate offline parser bundle, or convert to approved text |
| ZIP import rejected | Size, file count, traversal, symlink, duplicate basename | Rebuild a safe package; do not weaken archive checks |
| Analytics says dataset required | No CSV imported with category `dataset` | Import the approved validated telemetry CSV |
| Workspace lock/metadata issue | Another process is using the same runtime | Stop duplicate instances; one writer per runtime root |
| Restored answer differs | Runtime/app/model/embedding version changed | Re-index and compare the recorded release/config hashes |
| Container port is unreachable | Current app is loopback-only inside its host | Use the Windows wheel; container runtime is not approved in V2 |

## 14. Required acceptance evidence

Use [acceptance checklist](acceptance-checklist.md) and [admin/user guide](admin-user-guide.md).
No production data is approved until every mandatory item has an owner,
timestamp, result, and artifact path.
