# Four-version human UI review — 2026-07-18

Independent read-only review used the same local backend at
`http://127.0.0.1:8788` and inspected desktop and mobile journeys. The reviewer
did not edit product code.

| Architecture | Engineer score | Decision |
|---|---:|---|
| Frozen baseline | 2/5 | Retain only as a regression control |
| V1 continuous conversation desk | 3/5 | Merge its queue behavior; do not ship as a separate final product |
| V2 answer and evidence workbench | 4/5 | Best default foundation |
| V3 engineering deliverable canvas | 4/5 | Best complex-answer mode, not an always-open mobile surface |

## Findings that changed the implementation

- Every question entry point now uses one serial request scheduler. V1 exposes
  the queue; other variants preserve order without allowing concurrent answers
  to overwrite history or side panels.
- Long answers now position the start of the new answer in the visible Chat
  region instead of jumping to the bottom of its table or chart.
- V3 keeps a path-safe Markdown summary in Chat. Its full canvas opens
  automatically only for complex desktop results; mobile users open it on
  demand.
- Default citation controls show at most two exact original filenames plus the
  total count. The evidence panel still exposes every exact filename and
  excerpt.
- The hand-written Markdown regex was replaced with pinned Marked and DOMPurify
  assets. Link labels remain human-readable while internal locations, active
  HTML and remote images are removed from answer rendering.

## Recommendation

No single variant reached the predeclared 4.2/5 release threshold. The measured
direction is therefore a hybrid, not an unsupported claim that one mock-up is
optimal:

1. use V2 as the default single-Chat information architecture;
2. retain V1's queue as a shared composer capability;
3. open V3's deliverable canvas only for long answers, tables or charts;
4. keep the baseline available only for regression comparison.

The current four routes remain available so the Chairman can compare the
architecture choices before the root route is changed.
