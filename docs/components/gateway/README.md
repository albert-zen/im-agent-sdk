# Gateway components

Gateway composes the single bridge path: admission, Conversation routing,
Application dispatch and observation, projection/recovery, bounded delivery,
bridge persistence, and read-only diagnostics. It owns no product command,
Channel transport, Agent transcript, or second runtime.

## Current leaf navigation

- [delivery](delivery/README.md) — deterministic planning and the remaining
  coordination/submission/proactive/outcome leaves as they move in focused
  slices.
- [presentation design](presentation/design.md) and
  [testing](presentation/testing.md) — O1 bounded per-destination projection
  presentation and suppression.
- [Gateway aggregate design](design.md) and [testing](testing.md) — current
  orchestration evidence while the remaining leaves are extracted.

The machine-readable [component map](../component-map.yml) is authoritative for
the complete target leaf inventory, current/target paths, dependencies, tests,
and explicit gaps.
