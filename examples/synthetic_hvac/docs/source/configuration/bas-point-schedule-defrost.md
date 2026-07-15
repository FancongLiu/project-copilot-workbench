---
source_type: bas_point_schedule
authority: synthetic_approved
revision: "2.1"
effective_from: 2026-07-15
asset_ids: [HP-01]
supersedes: synthetic-point-schedule-2.0
confidentiality: public_synthetic
---

# HP-01 synthetic BAS point schedule

| Point ID | Meaning | Unit/type | Access | Expected interval |
|---|---|---|---|---|
| hp01.mode | Operating mode | enum | read-only | 10 s |
| hp01.oat | Outdoor-air temperature | degC | read-only | 10 s |
| hp01.outdoor_coil_temp | Outdoor coil temperature | degC | read-only | 10 s |
| hp01.suction_pressure | Suction pressure | kPa (synthetic) | read-only | 10 s |
| hp01.discharge_pressure | Discharge pressure | kPa (synthetic) | read-only | 10 s |
| hp01.suction_temp | Suction temperature | degC | read-only | 10 s |
| hp01.discharge_temp | Discharge temperature | degC | read-only | 10 s |
| hp01.superheat | Controller-calculated superheat | K | read-only | 10 s |
| hp01.subcooling | Controller-calculated subcooling | K | read-only | 10 s |
| hp01.compressor_cmd | Compressor command | boolean | read-only mirror | 10 s |
| hp01.outdoor_fan_cmd | Outdoor fan command | boolean | read-only mirror | 10 s |
| hp01.reversing_valve_cmd | Reversing-valve command | boolean | read-only mirror | 10 s |
| hp01.defrost_cmd | Defrost command | boolean | read-only mirror | 10 s |
| hp01.alarm_code | Active alarm code | string | read-only | event / 10 s snapshot |
| hp01.data_quality | Collector quality flag | enum | read-only | 10 s |

The Workbench never writes these points. A `read-only mirror` label describes
the source controller point, not permission for Project Copilot to command it.
Command/feedback separation, circuit-level identity, pressure reference and
real refrigerant properties are required before a company rule pack can make a
production root-cause claim.
