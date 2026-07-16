---
source_type: commissioning_plan
authority: synthetic_approved
revision: "1.0"
role: commissioning_engineer
confidentiality: public_synthetic
---

# Synthetic controls commissioning witness plan

Witness item `CX-ROLE-02` requires a point-to-point identity check, timestamp
and timezone confirmation, sampling-interval check, command-versus-feedback
mapping, rule-version binding, and a read-only trend export before replay.
The synthetic isolation marker for this role document is `CXAUTH9Q2`.

The engineer must preserve the original export hash and record gaps, duplicate
timestamps, bad-quality flags, and changes made after the witnessed run. A
missing prerequisite produces `insufficient_data`; it never authorizes a
guessed pass. The final record contains observed evidence and first deviation.
