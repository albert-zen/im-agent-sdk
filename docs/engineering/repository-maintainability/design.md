# Repository maintainability design

## Purpose

Repository maintainability keeps ownership, durable intent, navigation,
verification, provenance, and review routing aligned. It is an engineering
support leaf, not an SDK runtime component.

## Ownership

This leaf owns the repository-level guidance and checks that connect the
runtime leaves:

- root `AGENTS.md` as a short map of durable rules;
- documentation navigation and dependency-rule documentation;
- the machine-readable component inventory and mapping tests;
- repository link, architecture, and inventory checks;
- CI verification, provenance policy, and roadmap placement; and
- maintainability budgets and their review evidence.

AgentKit lifecycle mechanics, schema validation, and package/release details
have their own engineering leaves. Runtime semantics remain in Vision,
Architecture, accepted ADRs, contracts, and component docs. The README is a
user entry point, not internal design authority.

## Documentation authority

The durable authority chain is:

- [Vision](../../VISION.md) for purpose and non-goals;
- [Architecture](../../ARCHITECTURE.md) for ownership, dependencies, and
  flows;
- [accepted ADRs](../../decisions/README.md) for reviewed cross-component
  choices;
- `docs/components/<layer>/<leaf>/` for runtime semantics;
- `docs/engineering/<leaf>/` for repository support responsibilities;
- [Reuse](../../REUSE.md) for source provenance and extraction; and
- [Roadmap](../../ROADMAP.md) for future or unresolved work only.

Focused pages are authoritative. Transitional broad pages may remain as
navigation while links migrate, but copied behavioral truth must not create a
second authority. Removing a broad page is safe only after every unique landed
rule has moved to an authoritative leaf.

## Mapping and AgentKit boundary

The component map is the single ownership and import-lint inventory. It binds
current and target code, tests, public exports, dependencies, ADRs, and
structural gaps. `agentkit.yml` consumes that inventory for change routing and
review guidance; it is not a second import graph. Unknown paths, unexplained
reverse-layer edges, undeclared facade re-exports, cycles, stale exact
exceptions, and unowned tests fail the inventory checks.

Pinned launcher behavior, lifecycle state, tool limitations, deletion routing,
and clean-checkout evidence belong to the separate
[AgentKit leaf](../agentkit/design.md). Package metadata, public-facade and
optional-dependency independence belong to the
[release leaf](../release/design.md). Repository maintainability owns only the
component-map/interface boundary and must not duplicate those mechanics.

## Change obligations

Any change to module layout, component docs, global intent paths, or CI updates
the component map, applicable AgentKit routing, human navigation, mapping tests,
and routing smoke evidence in the same slice.

## Executable test owner

The complete repository-maintainability test mirror lives in
`tests/engineering/test_repository_maintainability.py`. It contains the
component-map and documentation-link coverage that historically lived in the
two root test modules, with the same assertions and discovery behavior. The
historical root paths are intentionally absent; they are not compatibility
facades and must not be recreated as import shims.

`tests/test_agentkit_mapping.py` remains the AgentKit routing owner, and
release/package tests remain with the release leaf. The move changes only the
physical test owner and its repository-root calculation; component-map rules,
architecture policy, path ownership, link-check behavior, and AgentKit
behavior remain unchanged.

## Maintainability budget policy

Budgets are named responsibility review triggers, not generic file-size goals.
Each budget keeps limited headroom beyond the measured implementation. A
warning is resolved in one of two ways:

- extract a state-owning boundary or pure mapping/transport seam that can be
  tested independently; or
- calibrate a responsibility-specific budget only when splitting would
  duplicate state or break an ordering or transaction invariant.

The Gateway remains the single composition root.

The current budgets are:

- Gateway orchestration: 1,100 lines, 40 functions;
- projection runtime coordination: 950 lines, because input acceptance,
  bounded event admission, route barriers, and worker recovery share one
  per-Thread ordering invariant;
- SQLite gateway state: 925 lines, because repository protocols share one
  connection and transaction boundary;
- App Server client: 1,750 lines after transport/mapping extraction;
- native channel transports: 1,400 lines, 65 functions, 18 classes; and
- the narrower extracted mapping, transport, and diagnostic modules have
  their own budgets.

Where stable seams exist, code is split instead of expanding the original
budget. App Server process/WebSocket transports live in a transport leaf, and
stateless native response mapping lives in a mapping leaf. Extracted mapping,
wire-transport, and diagnostic-state modules have their own narrow budgets so
they cannot silently regrow to the client or adapter ceiling.

The exact executable budgets live in `agentkit.yml`. A budget change requires
zero maintainability warnings, mapping and component-import checks, focused
behavior tests, and the full repository verification.
