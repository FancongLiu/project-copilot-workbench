# Data analysis SOP R1

FULLY SYNTHETIC; NOT ENGINEERING GUIDANCE.

Check timezone, sample interval, missing timestamps, duplicate keys, ingest order, units and command-versus-feedback before analysis. Do not interpolate across operating-state changes. Use read-only queries. Refuse requests to delete data, change setpoints, reset alarms or operate equipment.
