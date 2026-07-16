---
source_type: work_order
authority: synthetic_approved
revision: "1.0"
role: field_service_engineer
confidentiality: public_synthetic
---

# Synthetic field-service work order

Work order `WO-ROLE-17` asks the field engineer to review a high supply-water
temperature observation. The required first checks are event timestamp,
TT-101 supply temperature, TT-102 return temperature, load percentage, power,
sensor quality, effective configuration, and the decision register.
The synthetic isolation marker for this role document is `WOAUTH8K7`.

The work order does not assert a root cause. Refrigerant charge, valve
position, water flow, and sensor calibration remain unverified. The engineer
must record those unknowns and may not acknowledge alarms or command equipment
from the Workbench.
