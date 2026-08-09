# Applications component navigation

The Applications layer defines the typed boundary to Agent Applications. It
normalizes native facts but never becomes their authority: the Application
continues to own Projects, Threads, Turns, transcript/history, requests, and
execution. Gateway may consume these contracts but Applications never imports
Gateway implementation or product Controller policy.

The resource hierarchy is unconditional: `ProjectRef` scopes every
`ThreadRef`, and `ThreadRef` scopes every escaped `TurnRef`. Fixed/flat
adapters expose exactly one stable workspace Project plus canonical-root
fingerprint evidence; managed adapters preserve native Project identity.

## Common leaves

| Leaf | Responsibility | Design | Testing |
|---|---|---|---|
| `applications.application-contract` | lifecycle, resource, input, history, and subscription boundary | [design](application-contract/design.md) | [testing](application-contract/testing.md) |
| `applications.capabilities` | honest native support declarations | [design](capabilities/design.md) | [testing](capabilities/testing.md) |
| `applications.events` | canonical events and bounded live fan-out | [design](events/design.md) | [testing](events/testing.md) |
| `applications.diagnostics` | redacted Application and A1 diagnostic contracts | [design](diagnostics/design.md) | [testing](diagnostics/testing.md) |
| `applications.operations` | shared native control intent/results | [design](operations/design.md) | [testing](operations/testing.md) |
| `applications.requests` | typed interactive request/response facts | [design](requests/design.md) | [testing](requests/testing.md) |

## Presentation

The [presentation subtree](presentation/README.md) holds concrete A1
adapter-owned positions. It is not a generic Application hook: facts are
bounded and typed, raw native envelopes never cross it, and the adapter keeps
native item/Turn/history authority.

The common redacted Application diagnostic vocabulary is owned by the
[diagnostics leaf](diagnostics/design.md). Presentation runtimes publish its
fixed A1 fact types without moving mutable state or consumer observability
policy into the common contract.

The common adapter Protocol and its pre-dispatch callback are owned by the
`applications.application-contract` leaf in
`src/imagent/applications/contract.py`. The finite `imagent.applications`
facade exposes the complete Application contract family as exact objects and
resolves explicitly named concrete adapter/presentation exports without
eagerly importing concrete adapters. Capabilities, operations, and requests
remain available only from their canonical owner modules. The historical
`imagent.adapters` module exports no Application names; it retains only the
unrelated Gateway, proactive-authorization, and passive-state aliases.

## Adapters

The complete adapter block now has an authoritative [adapter navigation
page](adapters/README.md), with an App Server parent page and design/testing
pages for every App Server, Codex, Zen, and T3 leaf. Those pages record the
real current implementation, exact target package, public contracts,
dependencies, state/recovery semantics, tests, accepted decisions, and
structural gap before physical reorganization.

The older aggregate [Application adapter design](../application-adapters/design.md)
and [testing guide](../application-adapters/testing.md) remain cross-adapter
transition context. They do not replace the leaf ownership records in the
adapter subtree. The App Server, Codex, Zen, and T3 leaves now use their
focused target owners; the historical paths are not retained as internal
compatibility implementations.

## Shared constraints

- `Message` carries content; typed Application `Operation` carries common
  control intent. Product commands remain in consumer Controllers.
- An adapter reports only native support it can evidence. Unsupported behavior
  fails explicitly and an ambiguous dispatched mutation is never retry
  permission.
- Native live observation has finite per-subscriber capacity and explicit gaps;
  authoritative Application history/replay performs recovery.
- Adapter presentation may return only bounded typed values. Live-only output
  does not advance a completion checkpoint; a recoverable association must be
  reproducible from authoritative history.

The exhaustive current/target code, tests, exports, dependency edges, and
structural gaps are maintained in the [component map](../component-map.yml).
The event implementation is only `src/imagent/applications/events.py`;
`imagent.events`, `imagent.contracts`, and the package-root `events` module
are stable explicit formal facades with exact owner identity. Internal tests
use the owner path except for dedicated facade/clean-install assertions.
