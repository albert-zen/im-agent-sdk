# Applications component navigation

The Applications layer defines the typed boundary to Agent Applications. It
normalizes native facts but never becomes their authority: the Application
continues to own Projects, Threads, Turns, transcript/history, requests, and
execution. Gateway may consume these contracts but Applications never imports
Gateway implementation or product Controller policy.

## Common leaves

| Leaf | Responsibility | Design | Testing |
|---|---|---|---|
| `applications.application-contract` | lifecycle, resource, input, history, and subscription boundary | [design](application-contract/design.md) | [testing](application-contract/testing.md) |
| `applications.capabilities` | honest native support declarations | [design](capabilities/design.md) | [testing](capabilities/testing.md) |
| `applications.events` | canonical events and bounded live fan-out | [design](events/design.md) | [testing](events/testing.md) |
| `applications.operations` | shared native control intent/results | [design](operations/design.md) | [testing](operations/testing.md) |
| `applications.requests` | typed interactive request/response facts | [design](requests/design.md) | [testing](requests/testing.md) |

## Presentation

The [presentation subtree](presentation/README.md) holds concrete A1
adapter-owned positions. It is not a generic Application hook: facts are
bounded and typed, raw native envelopes never cross it, and the adapter keeps
native item/Turn/history authority.

The common adapter Protocol and its pre-dispatch callback are owned by the
`applications.application-contract` leaf in
`src/imagent/applications/contract.py`. The finite `imagent.applications`
facade exposes those exact objects; the historical `imagent.adapters` module
is only a temporary compatibility facade for them and for unrelated legacy
Channel/Gateway aliases.

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
adapter subtree. Native adapter code remains in its historical paths until
later focused mechanical slices move it.

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
