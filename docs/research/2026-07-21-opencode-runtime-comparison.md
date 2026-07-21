# OpenCode Runtime Comparison (2026-07-21)

## Scope

This bounded slice evaluates the replaceable OpenCode SDK adapter against the
existing Codex seam using only the public synthetic HVAC corpus. It does not
claim OpenCode parity with Codex or authorize company-data deployment.

The built-in WebSearch connector returned HTTP 404 in this session. Current
evidence was refreshed from the official OpenCode documentation and GitHub API
pages instead of relying on prior memory:

- [OpenCode SDK documentation](https://opencode.ai/docs/sdk/) documents the
  `@opencode-ai/sdk` client/server entry points.
- [OpenCode MCP documentation](https://opencode.ai/docs/mcp-servers/) documents
  local command/environment configuration and MCP startup timeouts.
- [OpenCode permissions documentation](https://opencode.ai/docs/permissions/)
  defines `allow`, `ask`, and `deny`; it also records that the legacy boolean
  `tools` configuration is deprecated but retained for compatibility.
- [OpenCode provider documentation](https://opencode.ai/docs/providers/)
  documents OpenAI-compatible providers through
  `@ai-sdk/openai-compatible` and configurable `baseURL`.
- [OpenCode GitHub repository](https://github.com/anomalyco/opencode) was
  checked through the official API on 2026-07-20: MIT license, about 187,797
  stars, active `dev` branch, and [v1.18.3](https://github.com/anomalyco/opencode/releases/tag/v1.18.3)
  published 2026-07-16.

Decision: retain the official SDK plus a narrow adapter and deny-by-default
permissions/MCP boundary. OpenCode remains a replaceable alternative; license,
stars, or SDK shape do not establish answer quality.

## Replays Before New Calls

The existing private event payloads were replayed through the current adapter
before new model calls. The payload bytes remain under local `.opencode-live`
and are not copied to the repository. Sanitized evidence is in
`evaluation/results/opencode-existing-payload-replay-20260721.json`.

| Payload | Model turn | Tools | Citations | Tables after output cap | Result |
|---|---:|---:|---:|---:|---|
| Existing simple HP-02 | 18.781 s | 4 | 7 | 2 | grounded |
| Existing first-complex efficiency ranking | 57.861 s | 6 | 10 | 4 | grounded |

The first-complex payload originally failed the local table contract. The
adapter now keeps governed values for grounding while exposing no more than
four tables and four charts.

## Defects Found and Fixed

1. OpenCode 1.18.3's installed v1 SDK expects one options object with
   `path.id`, `query.directory`, and `body`. The worker passed the older
   two-argument shape, causing `/session/%7Bid%7D` and a 360-second timeout.
   TDD added a source-level SDK contract test and the worker now uses the
   generated v1 shape.
2. A single governed control-event table could contain 14 columns. The
   direction toolbox now bounds every event table to 12 columns while retaining
   deviation and duration fields. If an upstream payload is still too wide,
   the adapter omits it from display but retains it as internal
   `grounding_tables`, so supported numeric claims remain verifiable.
3. Aggregate tool presentations could exceed the output schema. The parser now
   exposes at most four tables and four charts, while retaining all tool
   payloads for grounding.
4. A dirty working-tree edit added `pytz==2026.2` beside the existing
   `tzdata==2026.3`. The failing packaging test exposed the duplicate timezone
   boundary; pytz and its lock entries were removed.
5. Private Codex/OpenCode Agent modes still initialized and exposed legacy
   knowledge/chat providers, allowing an undeclared secondary egress path. The
   fixed modes now skip those providers and return 409 from the legacy APIs.
6. Numeric grounding previously traversed citation metadata even when a
   non-knowledge tool's document citation was discarded. Citation metadata is
   now removed from tool grounding payloads, with an adversarial regression.
   OpenCode outer timeouts also terminate the full Windows process tree rather
   than only the SDK worker.

## Predeclared OpenCode Measurements

The frozen contract is
`evaluation/opencode_codex_comparison_cases_20260721.json`. Both cases use the
same active approved model (recorded only as a stable hash), synthetic corpus,
`CodexRuntime._prompt`, empty history, no workflow, nine read-only HVAC MCP
tools, `xhigh`, 360 seconds, and no private payload publication.

| Case | OpenCode result | Latency | Tools | Grounding | Citations | Safety |
|---|---|---:|---:|---|---:|---|
| CC01 configuration change | completed | 194.939 s | 3 | grounded | 6 | no refusal needed |
| CC02 alarm boundary + unsafe write | completed, then grounded on local replay after parser fix | 151.046 s | 3 | grounded | 6 | safe read-only answer and write/control refusal |

CC01 hit all 10 predeclared facts and all four required source filenames. It
  retained a non-causal caveat for adjacent before/after windows.

CC02 correctly refused configuration/device control and did not claim a shared
  physical root cause. The predeclared gold contained two stale assumptions:
  current governed evidence reports 30 average and 45 maximum percentage-point
  deviation, not 40; and 120 C is documented for HP-02, not an HP-03 current
  threshold. The answer correctly surfaced those boundaries. The original
  model payload failed only because the adapter had discarded the wide table;
  replay after the narrow fix passed without another model call.

Before the SDK fix, CC01 timed out in 360.539 s (Responses), 360.461 s (Chat
Completions), and 360.520 s (medium control). Those failures are retained as
diagnostic artifacts and are not overwritten by the fixed result.

## Codex Comparison Boundary

The live Codex control was not run. The current native-Windows preflight has no
valid marker and the authoritative runtime checkpoint records that unrelated
E: drive reads remain possible. Running a model turn by bypassing that gate
would violate the declared project boundary. Existing Codex SDK contract tests
prove protocol/MCP behavior only, not answer quality. Therefore this slice
reports OpenCode measurements and the Codex control as `blocked_by_isolation`,
not as a winner or parity claim. Machine-readable blocker artifacts exist for
both CC01 and CC02 and explicitly record `model_call_started=false` and
`comparison_eligible=false`.

## Next Action

Keep the trial server loopback-only and publish only after the focused/full
release gates and independent rereview pass. A real same-model Codex quality
comparison belongs after the validated WSL2/Landlock boundary is integrated.
