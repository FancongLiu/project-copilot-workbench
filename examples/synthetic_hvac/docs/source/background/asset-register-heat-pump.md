---
source_type: asset_register
authority: synthetic_approved
revision: "1.0"
effective_from: 2026-07-01
asset_ids: [HP-01]
confidentiality: public_synthetic
---

# Synthetic heat-pump asset register

HP-01 is a fully fictional commercial packaged heat-pump unit used only for
Project Copilot testing. It is not based on a real manufacturer, model or
firmware implementation.

| Field | Synthetic value |
|---|---|
| Asset ID | HP-01 |
| Product family | AuroraHeat AH-320 |
| Outdoor unit | ODU-01 |
| Refrigeration circuit | RC-1 |
| Nominal heating capacity | 320 kW |
| Refrigerant | Synthetic-RX (non-physical placeholder) |
| Compressor | Variable-speed scroll bank |
| Controller | AuroraCTRL-700 |
| Firmware | SYN-3.4.2 |
| BAS protocol | BACnet/IP |
| Trend interval | 10 seconds |
| Rule pack | SYN-HP01-DEFROST version 2026-07-15 |

The fictional refrigerant label prevents the dataset from being used for real
pressure/temperature engineering calculations. Root-cause tests in this
release validate control sequence evidence, not refrigerant property claims.
