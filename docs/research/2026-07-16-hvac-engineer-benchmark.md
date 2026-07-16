# Commercial-HVAC engineer benchmark and tool selection

Date: 2026-07-16 (Asia/Shanghai)

## Decision

Project Copilot Workbench is a commercial-HVAC project knowledge and governed
data-analysis product. Defrost replay is one difficult temporal-control example;
it is not the product boundary.

Keep the current Haystack, Pandera, DuckDB/SQLGlot, Polars, and transitions
architecture. Add the isolated engineer-role benchmark in this release. Do not
replace the application with a large RAG platform or add a new time-series,
evaluation, or observability dependency until a measured company-local campaign
shows that the existing boundary cannot meet a specific acceptance case.

## Research method and limitation

The research started with the required WebSearch call, but the configured
search connector returned HTTP 404 for `POST /v1/alpha/search`. This is a tooling
failure, not evidence that no prior work exists. Two bounded read-only research
Agents then independently used current official public pages, vendor document
libraries, GitHub REST/API, repository source, release metadata, and PyPI
metadata. No large vendor PDF, proprietary standard, or upstream source tree was
copied into this repository.

GitHub counts below are a dated API snapshot, not quality measurements. A
project's own feature or enterprise-adoption statement is treated as vendor
evidence and not independently proven adoption.

## Real engineer workflow evidence

The sources converge on one general workflow:

1. Identify the asset, controller, firmware, project phase, document revision,
   and the applicable operating envelope.
2. Reconcile equipment schedules, approved sequences, BAS point lists, current
   configuration, meeting decisions, field changes, SOPs, and open actions.
3. Map point names, units, read/write semantics, command versus feedback, time
   zone, sample interval, and data quality before analyzing a trend.
4. Run a bounded time-window comparison or diagnostic using structured data.
5. Report cited evidence, the first deviation or anomaly, unknowns, the next
   measurement, and the limits of the conclusion. Do not operate equipment.
6. Preserve the test record, source hashes, issue owner, disposition, training,
   handover, and rollback evidence.

Current official references used to shape this workflow:

- [ASHRAE commissioning resources](https://www.ashrae.org/technical-resources/bookstore/commissioning)
  describe roles, design documents, procedures, reports, training, systems
  manuals, and ongoing commissioning. Public descriptions were used; paid
  standard text was not copied.
- [Trane Odyssey Symbio 700 BACnet/Modbus points list, BAS-PTS005B-EN](https://elibrary.tranetechnologies.com/public/commercial-hvac/Literature/Points%20List/BAS-PTS005B-EN_03042026.pdf)
  shows the practical need to preserve object identity, access semantics,
  arbitration, units, limits, and existence conditions. The synthetic corpus
  does not reproduce its tables or model-specific values.
- [Trane Odyssey installation/startup guide, SS-SVN016C-EN](https://elibrary.tranetechnologies.com/public/commercial-hvac/Literature/Installation/SS-SVN016C-EN_06282024.pdf)
  demonstrates configuration, wiring, alarms, service-test, and trend-export
  workflow shapes. Model-specific instructions are not generalized.
- [Trane Axiom WSHP IOM, WSHP-SVX019E-EN](https://elibrary.tranetechnologies.com/public/commercial-hvac/Literature/Installation%20Operation%20and%20Maintenance/WSHP-SVX019E-EN_03142026.pdf)
  demonstrates inspection, startup logs, operating conditions, water/air-side
  checks, pressures, and symptom-to-next-check troubleshooting. It must not be
  treated as a universal operating table.
- [LBNL OpenBuildingControl verification](https://obc.lbl.gov/specification/verification.html)
  compares controller and reference-model output time series using point
  mapping, unit conversion, tolerances, and sequence charts. The maintained
  [OBC repository](https://github.com/lbl-srg/obc) and
  [Funnel](https://github.com/lbl-srg/funnel) provide a mature future pattern
  for tolerance-based temporal acceptance.
- [IBPSA BOPTEST](https://github.com/ibpsa/project1-boptest) provides repeatable
  building-control test cases, REST inputs/measurements/results, scenarios, and
  KPIs. It is useful for future simulation campaigns but cannot prove field
  wiring or mechanical condition.
- [Project Haystack](https://project-haystack.org/) and
  [Brick Schema](https://brickschema.org/) provide maintained cross-vendor
  equipment, point, unit, site, system, and relationship vocabularies. They are
  the preferred references for a future point-map contract.
- [Building Data Genome 2](https://github.com/buds-lab/building-data-genome-project-2)
  is useful for building-level meter and weather analytics, but its hourly data
  is too coarse for control interlocks or defrost sequence acceptance.

## Mature project comparison

Scoring is a relative 0-5 engineering assessment. `M` is maturity/ecosystem,
`A` activity, `W` Windows/Python 3.12/offline fit, `X` interoperability, `F`
HVAC-role fit, `O` operational simplicity, and `L` license/distribution fit.
The unweighted total is out of 35. It is not an official score or a measured
application quality percentage.

| Candidate | Dated GitHub evidence | License | M | A | W | X | F | O | L | Total | Selection decision |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| [Haystack](https://github.com/deepset-ai/haystack) | 25,908 stars; pushed 2026-07-15 | Apache-2.0 | 5 | 5 | 5 | 5 | 5 | 4 | 5 | 34 | Keep. Current app already uses bounded Agents, tools, retrieval, and maintained ranking evaluators. |
| [LlamaIndex](https://github.com/run-llama/llama_index) | 50,872; pushed 2026-07-13 | MIT | 5 | 5 | 5 | 5 | 4 | 3 | 5 | 32 | Strong reader alternative; switching now adds cost without a measured gain. |
| [DeepEval](https://github.com/confident-ai/deepeval) | 16,877; pushed 2026-07-14 | Apache-2.0 | 4 | 5 | 4 | 4 | 5 | 4 | 5 | 31 | Preferred optional company-model RAG and Agent tool/argument campaign. |
| [Ragas](https://github.com/vibrantlabsai/ragas) | 14,854; current repository name verified | Apache-2.0 | 4 | 4 | 4 | 5 | 4 | 4 | 5 | 30 | Good RAG/test generation; less direct Agent tool coverage than DeepEval for the next campaign. |
| [Evidently](https://github.com/evidentlyai/evidently) | 7,698; pushed 2026-05-02 | Apache-2.0 | 4 | 4 | 5 | 5 | 5 | 4 | 5 | 32 | Later data/LLM reports and drift checks, not a Pandera replacement. |
| [StatsForecast](https://github.com/Nixtla/statsforecast) | 4,839; pushed 2026-07-14 | Apache-2.0 | 4 | 5 | 5 | 5 | 5 | 5 | 5 | 34 | Future transparent forecasting/residual baseline after labeled time series exist. |
| [PyOD](https://github.com/yzhao062/pyod) | 9,915; current release evidence verified | BSD-2-Clause | 5 | 5 | 5 | 5 | 5 | 4 | 5 | 34 | Future multivariate anomaly ranking; it does not establish root cause. |
| [STUMPY](https://github.com/stumpy-dev/stumpy) | 4,114; current release evidence verified | BSD-3-Clause | 4 | 4 | 5 | 4 | 4 | 4 | 5 | 30 | Optional motif/discord tool; window choice remains a domain decision. |
| [sktime](https://github.com/sktime/sktime) | 9,857; pushed 2026-07-14 | BSD-3-Clause | 5 | 5 | 5 | 5 | 3 | 3 | 5 | 31 | Broad interface, but detection is still maturing and unnecessary now. |
| [Vanna](https://github.com/vanna-ai/vanna) | 23,774; archived; pushed 2026-02-02 | MIT | 4 | 1 | 4 | 5 | 5 | 3 | 5 | 27 | Reject as a new dependency while archived. Any future NL2SQL comparison must retain DuckDB/SQLGlot policy and audit. |
| [Salesforce Merlion](https://github.com/salesforce/Merlion) | 4,485; archived | BSD-3-Clause | 3 | 1 | 2 | 4 | 2 | 2 | 5 | 19 | Reject as a new foundation despite good historical time-series coverage. |

Heavy observability platforms were not selected for the current restricted
Windows deployment. MLflow is a plausible later local tracking ledger;
Langfuse adds Docker/ClickHouse operations; Phoenix uses Elastic License 2.0.
TimeGPT's core service is closed and API-key dependent. None solves the current
highest-priority product gaps more directly than the existing stack.

## Measured role benchmark

Four CC0, fully synthetic data areas are under
`examples/synthetic_hvac/agent_data`:

| Role | Representative work |
|---|---|
| Design engineer | Design-basis evidence, current configuration, temperature-delta analysis |
| Commissioning engineer | Witness prerequisites, peak load, 10-second temporal replay |
| Field service engineer | Alarm/work-order evidence, latest reading, unsafe-control refusal |
| Project delivery engineer | Meeting/action handover, decision chronology, ambiguity clarification |

Each role imports the shared synthetic Project Aurora package plus its own role
files into a separate `WorkspaceManager` runtime root. The 2026-07-16 measured
deterministic run recorded:

- 4 isolated roles and 16 cases;
- 16 completed, zero execution failures, and 16 passing every applicable
  explicit metric;
- 7 evidence-bearing cases: Recall 1.0, MRR 0.9285714285714286, and NDCG
  0.950131561401019 using Haystack evaluators;
- 16/16 answer-term, ordered tool-selection, refusal, and clarification checks;
- four cross-role negatives query opaque markers that exist only in another
  role overlay; all returned no citations and refused, proving the measured
  workspaces did not cross-read role sources. This is a fixture-isolation test,
  not user authentication, authorization, or a production access-control claim;
- local latency in the committed artifact: 2.942 ms minimum, 89.8145 ms
  median, and 239.719 ms p95/max. Latency is machine-load sensitive and is
  retained as raw per-case evidence rather than treated as a product SLO.

The artifact is `evaluation/results/hvac-role-benchmark.json`. These values
apply only to the frozen synthetic corpus and deterministic test double. They
do not measure a company model, real vendor manuals, site telemetry, engineer
judgment, or safe equipment operation.

## Product gap score

This is a review rubric, not a benchmark pass rate. Measured evidence above is
combined with explicit engineering review of untested surfaces.

| Area | Weight | Score | Evidence and gap |
|---|---:|---:|---|
| Project knowledge, retrieval, citations | 20 | 18 | Strong synthetic exact/cross-document evidence; no company-scale corpus test. |
| Structured and time-series analysis | 25 | 16 | Governed typed operations and one deep temporal rule; generic asset/time-window analytics and anomaly campaigns remain. |
| Agent routing and safety | 15 | 14 | All role tool/refusal cases pass; the company model and tool arguments need model-backed evaluation. |
| Engineer workflow and point semantics | 15 | 12 | Role areas, versions, decisions, and field notes exist; no cross-vendor Haystack/Brick point-map importer. |
| Evaluation and reproducibility | 10 | 9 | Frozen cases, per-case evidence, ranking metrics, isolated runtimes; no calibrated LLM judge. |
| Deployment and operations | 10 | 10 | Existing offline Windows, hash, TLS, egress, backup, rollback, and CI gates remain intact. |
| Model/version/safety applicability | 5 | 4 | Synthetic rule binding fails closed; broader vendor/model applicability manifests remain company-local work. |
| **Total** | **100** | **83** | Useful governed prototype with clear next data-analysis and semantic-mapping work. |

## Optimization decisions

Implemented now:

1. Four isolated role data areas and frozen role-specific gold sets.
2. A reusable offline role runner that keeps raw cases, citations, tool traces,
   failures, latency, and Haystack Recall/MRR/NDCG.
3. A broader product contract: knowledge, configuration, meetings, field work,
   governed analytics, temporal diagnostics, clarification, and refusal.

Next bounded priorities:

1. **P1 point-map contract.** Add a company-local importer/validator for vendor
   point identity, equipment relationship, unit, time zone, read/write/access,
   command/feedback, arbitration, range, existence condition, and revision.
   Align terminology with Project Haystack/Brick; do not copy vendor tables.
2. **P1 generic bounded analytics.** Add typed asset, time range, metric,
   aggregation, grouping, and data-quality parameters over approved tables.
   Keep DuckDB read-only, SQLGlot AST policy, row/time limits, and explicit
   operations; do not enable unrestricted model-generated SQL.
3. **P1 company-model evaluation.** Export the frozen role cases to an optional
   DeepEval campaign for faithfulness, context precision/recall, task
   completion, tool correctness, argument correctness, and step efficiency.
   Run only against the approved company endpoint and retain human calibration.
4. **P2 sequence tolerance.** Add OBC/Funnel-style reference-versus-controller
   time-series comparison after a reviewed point map and reference sequence
   exist. Use BOPTEST only for reproducible simulation, never as field proof.
5. **P2 anomaly evidence.** Evaluate StatsForecast residuals, PyOD ranking, and
   optional STUMPY motifs on a larger labeled synthetic/company-private corpus.
   An anomaly remains a lead, not a root-cause or control instruction.

## Next research directions

- Measure retrieval and tool arguments on a private, de-identified company
  corpus with approved model access; do not move its cases into this public repo.
- Compare Project Haystack tags and Brick relationships on two materially
  different vendor point lists before freezing the point-map schema.
- Build a labeled multi-asset synthetic campaign for alarms, sensor drift,
  water/airflow limits, pressure/temperature relationships, energy, and
  configuration changes; keep model-specific operating values in private,
  version-bound manifests.
- Calibrate any DeepEval LLM judge against independent HVAC-engineer ratings and
  retain disagreement cases instead of converting judge scores into truth.
