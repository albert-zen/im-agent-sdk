# Repository maintainability component design

## Purpose

This operational component keeps code ownership, durable intent, component
docs, checks, and agent review routing aligned. It is not an SDK runtime
component.

## Ownership

It owns:

- root `AGENTS.md` as a short map;
- `agentkit.yml` component/layer/impact configuration;
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

Current AgentKit v1 has two honest limitations:

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
