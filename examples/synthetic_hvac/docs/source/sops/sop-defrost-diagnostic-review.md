---
source_type: SOP
authority: synthetic_approved
revision: "1.0"
asset_ids: [HP-01]
confidentiality: public_synthetic
---

# Synthetic defrost diagnostic review SOP

1. Confirm asset, controller, firmware, timezone, rule version and data source.
2. Select a bounded `[start, end)` window; do not send raw full-day rows to the
   language model.
3. Run data-quality checks for schema, units, duplicates, gaps, interval drift
   and collector quality.
4. Replay the approved deterministic state machine and retain every transition
   and violation evidence row.
5. Label the result compliant, non-compliant or insufficient data. Shorter-than
   sample ordering remains unobservable.
6. Have the Agent explain only the evidence package and cite both the rule
   source and measured time window.
7. Never use this workflow to issue a controller command or change a setpoint.
