# Repository maintainability component design

## Purpose

This operational component keeps code ownership, durable intent, component
docs, checks, and agent review routing aligned. It is not an SDK runtime
component.

## Ownership

It owns:

- root `AGENTS.md` as a short map;
- `agentkit.yml` component-routing and impact configuration;
- the machine-readable component ownership/dependency/import-lint map;
- the pinned repo-local AgentKit launcher and plugin;
- documentation navigation and dependency-rule docs;
- CI verification, provenance policy, and future roadmap placement;
- tests that detect mapping/file-structure drift.

It does not own product semantics. Those live in Vision, Architecture,
accepted ADRs, Contracts, and component docs. `.agentkit/` task state,
receipts, and logs are ignored operational state and must never become product
documentation.

## Documentation authority

- `docs/VISION.md`: one global product purpose/non-goals authority.
- `docs/ARCHITECTURE.md`: system map, ownership, and dependency direction.
- `docs/decisions/`: accepted cross-component choices.
- `docs/components/<component>/`: local ownership, flow, failure, and tests.
- `docs/REUSE.md`: source provenance and extraction.
- `docs/ROADMAP.md`: future/unresolved work only.
- README: user entry, not internal design authority.

Old broad documents are removed after their unique truth moves to component
docs. Redirect-like navigation is acceptable; copied behavioral truth is not.

## AgentKit boundary

AgentKit routes, reminds, checks, and records lifecycle receipts. It does not
understand or author product intent. The pinned launcher avoids reliance on a
global executable and keeps clean checkouts reproducible. It selects UTF-8
mode for the AgentKit child process so Windows check output is not decoded
through a locale-specific code page.

`agentkit.yml` does not carry a second import-layer graph. Before delegating
`check` or `lint-architecture` to AgentKit, the repository launcher validates
actual `src/imagent` imports against `docs/components/component-map.yml`.
Exact public-symbol ownership takes precedence over a split module's owner
set; split candidates require an explicit owner/dependency pairing; declared
formal facades may only re-export mapped symbols; exact current-path
exceptions must match a live import. Unknown modules, forbidden edges, cycles,
ownership drift, and unused exceptions fail the gate.

Current AgentKit v1 has three honest limitations:

- component path matching uses one `code` field for source and tests;
- global design/workflow docs are first-class, but arbitrary global intent
  docs lack a separate read-only/reverse-impact role;
- deleted paths are matched only against the post-change manifest, so removal
  of an obsolete API/doc/test is reported as unmapped unless a stale mapping
  or empty shim is retained.

The config comments and mapping tests document the current fallback. Upstream
tracking:

- [AgentKit #3: role-aware test paths](https://github.com/albert-zen/AgentKit/issues/3)
- [AgentKit #4: global intent and reverse impact](https://github.com/albert-zen/AgentKit/issues/4)
- [AgentKit #5: deletion and rename impact](https://github.com/albert-zen/AgentKit/issues/5)

The repository does not use broad `src/**` catch-alls, stale deleted-file
mappings, or empty compatibility files to hide these gaps.

AgentKit mapping may split one documentation component into stable native
subcomponents when one code path should read only its applicable adapter page.
For example, the Application adapter docs remain one responsibility family,
while AppServer (Codex/Zen) and T3 use separate mapping entries.

## Change obligations

Any change to module layout, component docs, global intent paths, CI, or
launcher pin must update `agentkit.yml`, navigation, mapping tests, and
doctor/check/routing smoke evidence.

## Maintainability budget policy

Maintainability budgets are named responsibility review triggers, not generic
file-size targets. Each budget keeps limited headroom beyond the measured
implementation. A warning is resolved in one of two ways:

- extract a state-owning boundary or pure mapping/transport seam that can be
  tested independently; or
- calibrate a responsibility-specific budget when splitting would duplicate
  state or break an ordering or transaction invariant.

The Gateway remains the single bridge composition root, so its orchestration
budget is 1,100 lines and 40 functions. Projection runtime coordination has a
separate 950-line budget because input acceptance, bounded event admission,
route barriers, and worker recovery share one per-Thread ordering invariant.
SQLite gateway state has a separate 925-line budget because its repository
protocols share one connection and transaction boundary.

Where stable seams exist, code is split instead of expanding the original
budget. App Server process/WebSocket transports live in `transports.py`, and
stateless native response mapping lives in `appserver_mapping.py`. The reusable
App Server client keeps a 1,750-line ceiling after that extraction. Extracted
mapping, wire-transport, and diagnostic-state modules have their own narrow
budgets so they cannot silently regrow to the client or adapter ceiling.

A budget change must include before-and-after metrics, the responsibility or
invariant that justifies the result, zero maintainability warnings, mapping and
component-import checks, focused behavior tests, and the full repository
verification.
