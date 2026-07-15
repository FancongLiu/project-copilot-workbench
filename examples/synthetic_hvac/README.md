# Synthetic HVAC Project Package

This fully synthetic package exercises the V2 project-workspace workflow with:

- project background, heat-pump asset register, and BAS point schedule;
- dated baseline and current-state configuration evidence;
- six dated meetings, a decision/action register, and four SOPs;
- a 72-row chilled-water telemetry dataset;
- an 8,640-row, 10-second heat-pump defrost telemetry day;
- a versioned synthetic defrost rule pack bound to a fictional controller and
  firmware version;
- one compliant event near 04:00 and one deliberately non-compliant event near
  16:00 for deterministic replay;
- intentional historical/current configuration tension;
- no real facility data, secrets, endpoints, or command path.

Import the directory as the bundled demo or create a ZIP that preserves `project.yaml`, `docs/source/**`, and `datasets/raw/**`. Source basenames are unique because the current import inventory stores them by filename.

See `SYNTHETIC_DATA_PROVENANCE.md` for origin and use restrictions. All content in this package is CC0-1.0.

The defrost sequence is a software test contract with
`compliance_scope=synthetic_demo`. It is not an OEM sequence, not a substitute
for the exact unit model/firmware documentation, and not permission to control
equipment.
