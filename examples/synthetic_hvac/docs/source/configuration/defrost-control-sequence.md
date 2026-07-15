---
source_type: control_sequence
authority: synthetic_approved
revision: "1.0"
effective_from: 2026-07-15
asset_ids: [HP-01]
approver: Synthetic Commissioning Authority
confidentiality: public_synthetic
---

# HP-01 synthetic defrost control sequence

This sequence is an original fictional acceptance artifact. It is not an OEM
sequence and must not be applied to real equipment.

## Entry and exit

Rule `SYN-HP01-DEFROST` applies only to asset HP-01, controller AuroraCTRL-700,
firmware SYN-3.4.2 and the 2026-07-15 rule version.

The unit becomes a defrost candidate while it is in heating mode, the
compressor command is on, outdoor air is at or below 5 C, and outdoor coil
temperature is at or below 0 C. These predicates must remain continuously true
for at least 30 seconds. The controller must initiate defrost no later than 90
seconds after the candidate began.

During active defrost, the outdoor fan command must be off and the
reversing-valve command must be on. Defrost may end when outdoor coil
temperature reaches 5 C or when the 300-second maximum duration is reached.
After the command clears, the outdoor fan remains off for a 20-second recovery
dwell before normal heating resumes.

## Evidence and uncertainty

Ten-second data can verify minute-scale thresholds and dwell times, but cannot
prove the order of two actions that occur inside the same sample interval. A
real rule pack must mark such a rule `unobservable` unless a faster event log is
available. Missing points, duplicate timestamps, gaps, bad quality flags,
asset/firmware mismatch or an unapproved rule version make the result
`insufficient_data`, not a guessed pass/fail.

The system records the first deviation, state transitions, observed values,
rule ID, version, source file and source section. It is a read-only diagnostic;
it never commands the unit.
