# Component tree

This directory is the human navigation surface for the approved three-layer
SDK architecture. The machine-readable
[component map](component-map.yml) is the exhaustive inventory: it binds each
leaf to current and target code, tests, exports, dependencies, state/recovery,
accepted decisions, and structural gaps.

The inventory is intentionally logical before it is physical. A current flat
module may appear in several leaves only when it is declared as a split
candidate. Moving a file does not by itself prove ownership. The rollout is
complete only when code, documentation, and tests mirror this tree and the map
contains no unexplained multi-owner or orphan path.

## Interaction

```text
interaction
├── messages
├── operations
├── media
├── controllers
│   ├── controller-contract
│   ├── command-registry
│   ├── common-commands
│   └── request-presentation
└── channels
    ├── channel-contract
    ├── ingress
    ├── outbound-delivery
    └── adapters
```

Interaction owns content and control-intent contracts, bounded Controller UX,
and native IM ingress/delivery. `Message` carries content; `Operation` carries
control intent. Product commands compose a local registry in the consumer and
do not enter SDK Core. Channel code does not depend on Gateway or concrete
Application implementations.

See the [Interaction navigation](interaction/README.md) and the authoritative
leaf docs for [messages](interaction/messages/design.md),
[operations](interaction/operations/design.md), and
[media](interaction/media/design.md), plus the
[Controller subtree](interaction/controllers/README.md). The remaining Channel
leaves still use their current broad component documents until their focused
slices land.

## Gateway

```text
gateway
├── composition
├── lifecycle
├── admission
├── routing
│   ├── bindings
│   ├── gateway-operations
│   └── projection-routes
├── input
│   ├── content-transformation
│   ├── dispatch
│   └── failure-presentation
├── projection
│   ├── observation
│   ├── checkpoints
│   ├── request-correlation
│   └── recovery
├── presentation
├── delivery
│   ├── planning
│   ├── coordination
│   ├── submissions
│   ├── proactive-authorization
│   ├── proactive-delivery
│   └── outcome-observation
├── persistence
│   ├── state-contracts
│   ├── repository-contracts
│   ├── memory
│   ├── idempotency
│   ├── sqlite
│   └── row-mapping
└── diagnostics
```

Gateway owns bridge orchestration and minimal durable IM state. One
Conversation selects at most one current Thread, one Thread may project to
multiple current Conversations, and one Application observation worker serves
each Thread. Stable IDs drive idempotency; capacity is finite and failures are
explicit. Gateway owns no product command, transcript, Agent runtime, second
Application subscription, second Channel admission path, or durable content
spool/outbox.

## Applications

```text
applications
├── application-contract
├── capabilities
├── events
├── operations
├── requests
├── presentation
│   ├── live-activity
│   └── artifact-materialization
└── adapters
    ├── appserver
    │   ├── client
    │   ├── transport
    │   ├── mapping
    │   ├── requests
    │   └── diagnostics
    ├── codex
    ├── zen
    └── t3
```

External Agent Applications own native Thread, Turn, transcript, request, and
execution truth. The SDK Applications layer defines that boundary and its
concrete adapters normalize native resources/events/history/requests and report
dispatch outcomes honestly. They never expose raw native events to Gateway or
Controller consumers, invent recoverable history, or depend on Gateway.

## Engineering support

Engineering documentation will converge under `docs/engineering/` for:

- testing and conformance;
- language-neutral schema conformance;
- repository maintainability;
- AgentKit workflow;
- release and clean-install verification.

These are repository support concerns, not a runtime layer.

## Leaf documentation rule

Every final leaf has `design.md` and `testing.md`. Contract, recovery,
protocol, security, or operations pages are added only where they carry real
additional authority. A parent `README.md` navigates its children and never
substitutes for a leaf design.

Before physical migration, a leaf may already have focused target docs while
its current code and tests remain declared split candidates. The component map
states which authority applies to each leaf and records the remaining
structural gap. Each rollout slice updates the map together with the affected
code, tests, AgentKit routing, and public exports.
