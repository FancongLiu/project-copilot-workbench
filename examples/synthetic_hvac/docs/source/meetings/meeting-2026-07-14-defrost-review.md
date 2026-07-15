---
source_type: meeting
authority: synthetic_approved
meeting_id: MTG-2026-07-14-DEFROST
timezone: Asia/Shanghai
asset_ids: [HP-01]
confidentiality: public_synthetic
---

# HP-01 synthetic defrost review

Participants: controls engineer, field technician, commissioning lead and data
engineer (all fictional roles).

The team approved rule pack `SYN-HP01-DEFROST` for synthetic acceptance only.
The data engineer confirmed a ten-second read-only point export. The field
technician noted that command order faster than ten seconds is not observable
in this dataset. The commissioning lead required the analysis to return
`insufficient_data` instead of filling large gaps or using a mismatched
firmware/rule version.

Action A-DEF-01: replay the normal event near 04:00 and the injected abnormal
event near 16:00 on 2026-07-15. Owner: synthetic controls engineer. Evidence:
`defrost_telemetry.csv` and `defrost-rules.json`.
