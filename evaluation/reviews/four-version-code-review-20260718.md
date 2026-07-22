# Four-version independent code review — 2026-07-18

Initial read-only review found Critical 0, Important 5 and Minor 2. The review
covered request ordering, source privacy, baseline isolation, complex benchmark
gates, citation density, mobile panels and security boundaries.

Important findings and dispositions:

1. Non-V1 question entry points could submit concurrently and overwrite history
   or a V2/V3 side panel. Fixed with one shared serial scheduler and browser
   assertions for ordering and inherited history.
2. Hand-written Markdown parsing leaked nested-link and bare internal paths; V3
   bypassed the path-safe renderer entirely. Replaced with Marked 18.0.6 plus
   DOMPurify 3.4.12, one shared rendering boundary and path/XSS browser tests.
3. The baseline shares common renderer and transport code. Its distinct DOM and
   architecture are protected by route canary tests; common safety and privacy
   fixes intentionally apply to all four versions.
4. The complex benchmark did not fail on missing facts or quality gates. The
   runner now includes expected facts, table schema, global path patterns,
   quality-failure exit status, manual-review fields and resumable checkpoints.
5. Source controls could list every long filename. They now show no more than
   two exact original filenames plus the total count, with the full list in the
   expanded evidence surface.

## Final closeout rereview

After the shared benchmark completed, a second read-only review found two
remaining Important issues: an informational allowlist could mask a later
imperative write clause, and the renderer did not hide `datasets/`,
`configuration/`, `company/` or POSIX absolute paths. Both were closed with
exact unit/browser regressions. Historical analysis remains allowed; a mixed
analysis-plus-write request completes only the safe analysis and explicitly
refuses the write. The final rereview reported Critical 0 and Important 0 and
confirmed the Marked plus DOMPurify XSS boundary remained intact.
