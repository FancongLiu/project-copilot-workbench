# Synthetic instrumentation and data notes

TT-101 measures chilled-water supply temperature and TT-102 measures chilled-water return temperature. The demonstration analytics calculate delta T as TT-102 minus TT-101 and coefficient of performance as delivered cooling divided by electric power.

Telemetry is sampled once per hour in the bundled dataset. Values are deliberately plausible rather than manufacturer-derived. The dataset omits refrigerant charge, serial numbers, site addresses, personnel contact details, and any command channel.

For this evaluation package, `load_pct` is a synthetic supervisory load indicator. It is not a safety limit and must not be used to operate real equipment.
