# Synthetic data provenance

## Scope and origin

This is a fully synthetic corpus.

Every name, date, identifier, meeting, decision, action, configuration value, equipment description, and telemetry observation in this Project Package was authored specifically for Project Copilot Workbench testing. The package does not contain real company documents, copied manufacturer data, real facility information, personal contact details, credentials, or operational commands.

The fictional organization is `Northstar Test Lab`; the fictional project is `Project Aurora`; and all named people are invented test personas. Similarity to an actual person, site, asset, or project is coincidental.

## Creation method

- Documents were manually composed as deterministic test fixtures.
- The base telemetry fixture is a 72-row, hourly, internally consistent table covering 2026-06-30 through 2026-07-02.
- `scripts/generate_synthetic_defrost.py` deterministically generates 8,640
  ten-second rows for 2026-07-15. It includes one compliant replay and one
  intentionally non-compliant replay so entry, fan/valve state, maximum
  duration, exit, recovery, sampling-gap, and rule-evidence behavior can be
  regression-tested.
- The fictional rule pack is explicitly bound to `AuroraCTRL-700` firmware
  `SYN-3.4.2` and marked `synthetic_demo`. Its values were invented for tests;
  they were not copied from an OEM manual.
- The corpus deliberately contains dated historical and current configuration values so temporal and conflict-resolution behavior can be measured.
- The evaluation gold set was written before measured runs and is stored in `evaluation/gold_cases.json`; measured results are emitted separately and never overwrite the gold expectations.
- Four role-specific data areas under `agent_data` were independently authored
  for design, controls commissioning, field service, and project-delivery
  evaluation. Each role uses a separate runtime workspace and its own frozen
  gold cases. Defrost is only one complex commissioning case; the remaining
  cases cover project knowledge, configuration, meetings, field work, typed
  telemetry analytics, clarification, and refusal.
- No language model output or downloaded HVAC manual was copied into the corpus.

Public ASHRAE commissioning and chilled-water-plant topic pages were consulted only to confirm ordinary domain vocabulary and document workflow shapes. All fixture wording and numerical values remain independently invented. Those references are listed in `docs/evaluation.md` and are not redistributed here.

## Intended and prohibited use

This package is only for software demonstration, retrieval evaluation, security testing, temporal-rule replay, and browser acceptance. It is not an engineering design, commissioning record, safety procedure, maintenance manual, or control sequence for a real plant. A `compliant` synthetic replay means only that the selected rows matched the committed synthetic rule pack. It does not prove refrigerant-circuit health, OEM compliance, root cause, or safe operation. The Workbench must refuse requests to operate live equipment.

## License

The complete `examples/synthetic_hvac` package is dedicated under CC0-1.0. The full legal text is in `LICENSE`. Evaluation code and documentation outside this directory remain under the repository Apache-2.0 license.
