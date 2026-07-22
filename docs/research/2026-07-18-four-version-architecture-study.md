# Four-version architecture study

Date: 2026-07-18

## Decision boundary

Freeze the current evidence-first single Chat as the baseline. Build three
additional architecture-level experiences over the same API response and the
same private workspace. Do not fork retrieval, SQL, safety, source identity or
model settings between versions.

The built-in WebSearch endpoint returned `404 POST /v1/alpha/search`. Current
evidence was therefore verified through GitHub's official API, repository
trees, source files and official project documentation.

## Shared response contract

Every version consumes the same logical turn:

```text
answer_markdown + tables[] + charts[] + citations[] + activities[] + status
```

Every version must:

- remain a single ordinary Chat entry;
- preserve exact original filenames as the visible source identity;
- hide internal paths from answer text and copied prose;
- expose excerpts and relative evidence paths only on demand;
- keep the same model, corpus, tools, safety boundary and numeric grounding;
- avoid permanent project graphs and low-value technical traces.

## Baseline: evidence-first compact Chat

Current behavior. One continuous answer stream, compact original-filename
source summary, expandable excerpts and optional query-scoped evidence paths.
It is the control condition and must remain runnable throughout the bake-off.

## Variant A: continuous conversation desk

Architectural hypothesis: engineers asking long, spoken, multi-part questions
benefit more from uninterrupted conversational flow than from a larger answer
workspace.

- Keep the main full-width Chat.
- While one answer is generating, new questions enter a visible editable queue
  instead of disabling the conversation.
- Required clarifications appear inline and request only the smallest missing
  scope.
- Sources use the shared compact original-filename protocol.

Evidence:

- [Open WebUI queued messages](https://github.com/open-webui/open-webui/blob/main/src/lib/components/chat/MessageInput/QueuedMessageItem.svelte)
- [Onyx queued message bar](https://github.com/onyx-dot-app/onyx/blob/main/web/src/sections/input/QueuedMessageBar.tsx)
- [AnythingLLM clarifying questions](https://github.com/Mintplex-Labs/anything-llm/blob/master/frontend/src/components/WorkspaceChat/ChatContainer/ChatHistory/ClarifyingQuestion/index.jsx)

## Variant B: answer and evidence workbench

Architectural hypothesis: long engineering answers become easier to verify
when evidence is removed from the prose flow and opened in a dedicated panel.

- Keep the complete structured answer in the main region.
- Replace inline citation expansion with one compact original-filename button.
- Open excerpts and level-by-level relative paths in an on-demand evidence
  panel; never show raw internal storage names.
- Preserve copying of the main answer without project paths.

Evidence:

- [AnythingLLM citation and Sources Sidebar](https://github.com/Mintplex-Labs/anything-llm/blob/master/frontend/src/components/WorkspaceChat/ChatContainer/ChatHistory/Citation/index.jsx)
- [Onyx citations design](https://github.com/onyx-dot-app/onyx/blob/main/docs/mobile-chat/9a-citations/02-high-level-design.md)
- [RAGFlow reference document list](https://github.com/infiniflow/ragflow/blob/main/web/src/components/next-message-item/reference-document-list.tsx)

## Variant C: engineering deliverable canvas

Architectural hypothesis: tables, charts and multi-section analysis should
remain stable while the engineer continues to ask follow-up questions.

- Keep a short conversational summary in the Chat.
- Open the full Markdown answer, tables, charts and evidence in a persistent
  deliverable canvas for complex turns.
- New questions continue through the same composer without replacing the
  current deliverable.
- The canvas is a report surface, not a second ingestion or tool dashboard.

Evidence:

- [LibreChat ArtifactsPanel](https://github.com/danny-avila/LibreChat/blob/main/client/src/components/SidePanel/ArtifactsPanel.tsx)
- [LibreChat Sources](https://github.com/danny-avila/LibreChat/blob/main/client/src/components/Web/Sources.tsx)
- [AnythingLLM chart rendering](https://github.com/Mintplex-Labs/anything-llm/blob/master/frontend/src/components/WorkspaceChat/ChatContainer/ChatHistory/Chartable/index.jsx)
- [GitNexus reference panel](https://github.com/abhigyanpatwari/GitNexus/blob/main/gitnexus-web/src/components/CodeReferencesPanel.tsx) (interaction reference only; PolyForm Noncommercial code is not copied)

## Evaluation selection

Use deterministic business checks for timestamps, numeric truth, SQL results,
required tools, citations, tables and charts. Add maintained evaluation tools
only at their deep seams:

- [DeepEval](https://github.com/confident-ai/deepeval) for multi-turn
  conversation completeness and goal accuracy;
- [Ragas](https://github.com/vibrantlabsai/ragas) for faithfulness, context
  precision/recall and Agent tool-call cross-checks;
- [Phoenix](https://github.com/Arize-ai/phoenix) for frozen datasets,
  experiment comparison and blind pairwise review.

The first implementation gate is still the existing deterministic harness and
browser acceptance. No framework score may override a critical factual,
historical-window, citation or safety failure.

## Falsification and stop rules

- Reject any variant that invents a critical equipment fact, uses the wrong
  historical window without warning, cites a nonexistent source, or leaks an
  internal path.
- A supplied historical range must not trigger another current-date question.
- Multi-part subquestion coverage must be at least 90% and critical numeric
  accuracy at least 95% before readability is compared.
- The average engineer score must be at least 4.2/5 and the blind preference
  over baseline must exceed 60%; otherwise the architecture has not justified
  replacing the simpler baseline.
- Two rounds with the same hard failure or less than 2% improvement stop that
  branch. Styling-only improvement is not an architecture win.

## Rendering and request-order correction

Independent browser review found that architecture comparison would be invalid
if answers could complete out of order or if a variant exposed internal paths.
The shared safety seam was therefore corrected before scoring the variants:

- [Marked 18.0.6](https://github.com/markedjs/marked/releases/tag/v18.0.6)
  performs browser Markdown parsing under the MIT license. GitHub showed about
  37k stars and activity on 2026-07-15.
- [DOMPurify 3.4.12](https://github.com/cure53/DOMPurify/releases/tag/3.4.12)
  sanitizes the parsed HTML under Apache-2.0. GitHub showed about 17k stars and
  a 2026-07-11 release.
- One serial request scheduler now protects conversation history and V2/V3
  panels. V1 exposes its pending queue; the other variants retain the same
  ordering without adding queue UI.

This is a common privacy and reliability correction, not a feature advantage
assigned to any one variant.

## Independent engineer review

The first human-style desktop/mobile review scored the frozen baseline 2/5,
V1 3/5, V2 4/5 and V3 4/5. No single variant met the predeclared 4.2/5 release
threshold. The evidence-led recommendation is therefore:

1. V2 as the default single-Chat information architecture;
2. V1 queue behavior merged into the common composer;
3. V3 opened only for long answers, tables or charts, and only on demand on
   mobile;
4. the baseline retained as a regression control.

The detailed review is stored in
`evaluation/reviews/four-version-human-ui-review-20260718.md`.

## Shared-backend fault found by the complex benchmark

The first three-case smoke run passed only one case. An explicit historical
window failed because safe SQL functions `ABS` and `EXTRACT` were absent from
the SQLGlot allowlist and DuckDB's time-zone result path lacked an explicit
project timezone. The fix retained the one-table, one-SELECT,
allowlisted-column and row-limit boundaries and added only the two read-only
functions. A later Windows/Linux GitHub Actions run showed that `pytz` was not
the correct direct dependency for Python `ZoneInfo`; current Python guidance
and the maintained `python/tzdata` repository support shipping
`tzdata==2026.3`, while each snapshot session now requires the manifest
timezone instead of inheriting the host locale.

After the correction, MX09 answered the supplied historical window directly
in about 44 seconds, used the governed database plus typed alarm inspection,
returned a table and chart, did not ask for today's date, and passed all
automatic hard gates. This is the kind of architecture-level backtest result
that must drive further changes; it is not a styling observation.

## Final shared-backend result

The completed 14-case live run is retained at
`evaluation/results/four-version-shared-backend-live.json`. All 14 requests
completed, but only 1/14 passed the automatic hard gate after the final scorer
was applied. Independent HVAC-engineer adjudication recorded pass 0, partial 6
and fail 8, or 21.4/100. The full reasoning is in
`evaluation/reviews/four-version-complex-benchmark-human-review-20260718.md`.

The failures falsify the idea that a better answer layout alone can make the
current system comparable to an unconstrained expert Chat. The dominant
classes were:

- final free-text numbers failing to bind reliably to structured tool results;
- one failed subquestion causing the whole answer to disappear instead of
  preserving verified partial results;
- evaluation, test, script and handoff files entering engineer-facing
  citations;
- event-window semantics being over-interpreted even when individual numbers
  were grounded.

Three raw benchmark answers also contained internal paths. The shared browser
renderer now removes internal relative paths plus Windows and POSIX absolute
paths across all four routes, while citation controls retain exact original
filenames. That current UI correction does not alter or overwrite the frozen
raw result.

The measured recommendation is therefore conditional: retain V2 as the likely
default information architecture, merge the V1 queue into the common composer,
and reserve V3 for complex deliverables, but do not promote any route until the
same 14 questions clear the shared-backend gate. The next implementation seam
is structured evidence-to-answer generation, followed by partial delivery,
source whitelisting and deterministic event-semantic validation. Continuing UI
detail work before those changes would optimize presentation around failed
answers.
