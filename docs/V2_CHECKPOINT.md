# Project Copilot Workbench V2 compact checkpoint

Last updated: 2026-07-16 (Asia/Shanghai)

## Objective and authority

Deliver V2 end to end from the clean-task handoff. Public code and synthetic
data only. Do not touch OnionQuant runtime, Cron, context persistence, Inbox, or
Outbox. The current Goal preserves all acceptance criteria. Current task state
is authoritative in `docs/V2_TASK_LEDGER.json`; this file is its compact
human-readable recovery projection, and `docs/V2_TASK_EVENTS.jsonl` retains the
append-only transition summary.

## Completed

- Read project instructions and the clean V2 handoff.
- Applied the Chairman cognitive model and tech-selection framework.
- Audited V1 source, dependencies, CI and public repository state.
- Refreshed public Web/GitHub evidence and recorded the framework matrix.
- Accepted ADR 002: Haystack bounded Agent + durable workspace retrieval,
  optional Docling parsing, governed typed analytics, company OpenAI-compatible
  production path, deterministic synthetic acceptance path.
- Completed a second, recent-30-day GitHub shortlist after Chairman feedback.
  LightRAG v1.5.4 is the selected optional direct-deploy backend candidate;
  Kelsus is the selected evaluation/runbook pattern. Read-only upstream clones
  are in ignored `artifacts/research-upstream/` at recorded commits.

## Current state

- Branch: `main`, base commit `41d1b448bdfc0c6288f51b146a96ccb4610fda73`
  before V2 edits. Delivery commit `6559256` and verified remediation commit
  `19003d56c5801f5c937d9ceb890ccee6ae690bdd` are on remote `main`. Evidence
  commit `4ed30478b22f96415c48cc01a3f6d70cffbbe81e` triggered the first GitHub
  Actions run. CI alignment commit
  `d59cf85acf5cafd7419af9666e60dc410981d0ca` is on remote `main`.
- Runtime/Cron/inbox/outbox state has not been modified.
- V2 workspace, import, retrieval, bounded Agent, governed analytics, company
  model/embedding configuration, Web UI, CLI, synthetic corpus, evaluation and
  deployment documentation are implemented in the working tree.
- Desktop and 320px mobile Playwright acceptance passed against the final
  restarted loopback server, including create/switch/import, query, source
  deletion success/failure, Telemetry, defrost, refusal, focus and project-race
  paths. Final screenshots are in `docs/assets/`.
- The latest metadata consistency test confirmed SOP paths take precedence
  over ambiguous `control` keywords.
- Research-driven architecture gaps are closed: official optional reranking,
  structured Docling chunks, real Agent wall-clock cancellation, shared
  internal-CA clients, lifecycle locks/atomic index replacement, multi-turn
  regression, and Haystack ranking metrics.
- ADR 003 accepts a deterministic defrost replay engine plus bounded AI
  explanation. The synthetic package now includes 8,640 ten-second rows, a
  controller/firmware-scoped rule pack, and compliant/non-compliant events.
- V2 permits only `synthetic_demo`. `event_reconstruction` and `oem_exact`
  fail closed until an external approval manifest binds immutable telemetry,
  rule, point-schedule, asset, controller and firmware hashes. Fractional
  sampling intervals and dwell/duration values are preserved; cross-threshold
  sampled transitions are reported as interval-censored `unobservable`, never
  silently compliant.
- The fresh deterministic evaluation completed 23/23 cases with zero execution
  failures. Evidence-bearing retrieval measured Recall 1.0, MRR 0.859375, and
  NDCG 0.9003721028 across 16 cases. Safety coverage now includes no event,
  mid-event start, truncated event, coarse/uncovered data and Chinese control
  requests.
- PDF parsing now requires approved local Docling artifacts as well as a local
  tokenizer. A real offline PDF/DOCX integration smoke is present in GitHub CI;
  the heavy models are intentionally not installed on this nearly-full drive.
- After all review remediation, fresh `scripts/verify.cmd` passed with 150 tests and
  4 documented optional skips. The 23-case frozen evaluation again passed 23/23
  with zero execution failures; evidence cases remained Recall 1.0, MRR
  0.859375 and NDCG 0.9003721028. Strict dependency audit found no known
  vulnerabilities, LicenseCheck exited cleanly, the wheel/sdist and reproducible
  CycloneDX SBOM were rebuilt, and the installed wheel returned healthy truthful
  loopback egress state.
- Independent HVAC UX and defrost-safety reviews both finished with Critical 0
  and Important 0. The first whole-diff review then found atomic workspace,
  egress truth, immutable analytics, telemetry validation, Docling CI and
  offline deployment blockers. The working tree fixes them with immutable
  project generations, content-addressed DuckDB files, empty/non-finite data
  rejection, explicit DuckDB types, truthful per-channel egress, a production-
  matched parser CI lock, pinned tokenizer revision and executable Windows
  offline parser acceptance. Fresh desktop/320px browser acceptance also passes
  against final code. A final documentation subreview then found three remaining
  reproducibility/runbook issues; the layout model now uses an immutable
  Hugging Face commit, the isolated parser command defines its release root, and
  the final manifest is explicitly generated after every selected optional
  bundle. A further recheck reproduced Windows case-insensitive filename aliasing
  and a stale fixed-name compatibility analytics database. Import now rejects
  case variants, Win32 reserved/invalid names and corrupt legacy collisions
  before staging; the compatibility database is keyed by current dataset hash.
  The Agent also converts unexpected Haystack pipeline/tool failures into a
  sanitized refusal with generic failed activity and suppresses default error
  snapshot files, preventing HTTP 500 and exception leakage. The final
  independent release rereview reports Critical 0 and Important 0.
- First remote run `29451105406` passed Ubuntu tests/evaluation/release guard,
  Windows tests/evaluation/release guard, browser and package/SBOM jobs. The
  documents job correctly exposed that a Windows-generated parser lock cannot
  install on an Ubuntu job because it contains `pywin32`; the job now runs on
  the Windows company target. Gitleaks' sole finding was the public immutable
  Hugging Face tokenizer commit misclassified through the constant name. A
  narrow exact-value allowlist extends all default rules; local Gitleaks v8.24.3
  now scans the same commit range with no leaks.
- Second remote run `29451735488` passed Ubuntu/Windows tests, evaluation,
  release guard, browser, package/SBOM, and secrets. Its only failure was the
  Windows documents install: Torch requires setuptools, while pip-tools had
  excluded setuptools from hash mode as an unsafe package. Both parser locks
  now use `--allow-unsafe` and hash-pin `setuptools==83.0.0`.
- Current official pip/pip-tools documentation and GitHub issues also showed
  that an sdist hash cannot authenticate a newly built wheel copied into a
  release wheelhouse, and build-isolation dependencies are outside outer
  `--require-hashes`. The runbook therefore uses a clean, hash-locked connected
  builder with `--no-build-isolation`, creates target-specific offline locks
  from the final wheelhouse, mechanically checks normalized name/version
  parity, and makes the restricted company PC install only wheels with
  `--isolated --no-index --no-cache-dir --only-binary=:all: --require-hashes`.
  Fresh local gates after this remediation pass 150 tests with 4 documented
  skips; release guard, Ruff, formatting, diff check, and Gitleaks are clean.
- A final independent offline-release review found one Important risk: a failed
  rerun of the same commit could reuse stale release, venv, wheelhouse, model,
  offline-lock, or `dist` artifacts. Every such output now fails if it already
  exists; `dist` must be empty before building and contain exactly one
  application wheel afterward. The follow-up review reports Critical 0 and
  Important 0, and every PowerShell code block parses successfully.
- Commit `e35ee2acdea4622ecbc2f0fde9943e43d5ba81e4` pushed the hash-locked
  builder/double-lock remediation. GitHub run `29453989896` passed Windows and
  Ubuntu tests/evaluation/release guard, package/SBOM/license, browser, secrets,
  and the new build-tool download gate. The real Docling smoke then exposed a
  separate asset-version mismatch: Docling 2.113.0 defaults to
  `docling-layout-heron`, while the prefetch script still downloaded
  `docling-layout-old`. Official Docling source and Hugging Face evidence bind
  the correct folder to immutable Heron commit
  `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8`; the TDD correction is now in the
  working tree.

## Durable delivery loop from Chairman guidance

Treat the product as a general project-knowledge workbench whose first deep
domain is commercial HVAC. For every material architecture choice or measured
evaluation failure:

1. Search current official Web and GitHub evidence before designing a fix.
2. Compare maintained deployable components by license, community, Windows /
   offline fit, company API compatibility, security boundary and integration
   cost.
3. Use bounded parallel research for independent alternatives; keep one write
   owner for the implementation path and preserve the project concurrency cap.
4. Expand only fully synthetic/public-safe domain evidence: unit/product
   configuration, controls, commissioning changes, dated meetings and field
   notes, SOPs, decisions/actions, alarms and telemetry.
5. Re-run the frozen evaluation and browser journey. Classify every failure as
   corpus, parsing, retrieval, ranking, tool selection, grounding, analytics,
   UI, deployment or policy before changing code.
6. Prefer a mature component or documented upstream pattern. Write only the
   narrow adapter, configuration and project-specific policy that remain.
7. Preserve measured before/after evidence and do not report invented rates.

New domain directions that exceed this bounded V2 release are recorded as P1
follow-up research instead of silently expanding the current implementation.

## Next actions

1. Commit and push the Docling 2.113.0 Heron artifact correction.
2. Monitor the replacement GitHub Actions run to green, record commit/CI
   evidence, and leave the final loopback trial server available.

## Acceptance evidence ledger

| Criterion | Status | Evidence |
|---|---|---|
| A1 UI import | verified local | final desktop/320px Playwright flow |
| A2 auditable inventory | verified local browser | immutable generations, rollback and source lifecycle |
| A3 model/test-double Agent | implemented | Haystack integration tests |
| A4 multi-step cited answers | verified local | 23-case frozen evaluation set including defrost replay boundaries |
| A5 governed analytics tools | implemented | typed operations + SQL policy tests |
| A6 refusal/clarification | implemented | hostile and missing-evidence cases |
| A7 tests/CI/browser/wheel | local passed, CI pending | 150 passed, 4 optional skips; final browser and rebuilt wheel smoke passed |
| A8 scanners/audit/SBOM/license/public data | local passed, CI pending | audit, license, SBOM and release guard passed |
| A9 independent review | verified | final release rereview Critical 0, Important 0 |
| A10 commit/push/trial server | latest remediation pending push; server healthy | remote `d59cf85...`; healthy `127.0.0.1:8788` |
