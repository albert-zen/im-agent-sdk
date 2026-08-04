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
Application implementations. The formal Controller public surface is
`imagent.interaction.controllers`; the historical `imagent.controllers` path
is absent and is not a compatibility boundary.

The reusable conformance kit is physically mirrored under
`src/imagent/interaction/testing/**` so its checks and bounded fakes sit beside
the public Interaction contract package. Its authority remains the engineering
`testing-and-conformance` leaf; this directory is test support, not a fourth
runtime layer or an Interaction runtime dependency.

See the [Interaction navigation](interaction/README.md) and the authoritative
leaf docs for [messages](interaction/messages/design.md),
[operations](interaction/operations/design.md), and
[media](interaction/media/design.md), plus the
[Controller subtree](interaction/controllers/README.md) and
[Channel subtree](interaction/channels/README.md).

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

See the [Gateway navigation](gateway/README.md), including the authoritative
[composition](gateway/composition/design.md),
[lifecycle](gateway/lifecycle/design.md),
[admission](gateway/admission/design.md), and
[input](gateway/input/README.md) leaves, plus the
[projection subtree](gateway/projection/README.md).

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

See the [Applications navigation](applications/README.md), the common leaf
designs for [the Application contract](applications/application-contract/design.md),
[capabilities](applications/capabilities/design.md), [events](applications/events/design.md),
[operations](applications/operations/design.md), and [requests](applications/requests/design.md),
the [presentation subtree](applications/presentation/README.md), and the
complete [Applications adapter subtree](applications/adapters/README.md).
The adapter subtree is the documentation authority for App Server client,
transport, mapping, requests, diagnostics, Codex, Zen, and T3 boundaries;
their current code remains in historical paths until focused mechanical
slices move it.

## Engineering support

Engineering support is a repository concern, not a fourth runtime layer. Its
authoritative tree is [`../engineering/README.md`](../engineering/README.md):

```text
engineering
├── testing-and-conformance
├── schema-conformance
├── repository-maintainability
├── agentkit
└── release
```

Every engineering leaf has a meaningful `design.md` and `testing.md`. The
focused pages own repository support mechanics; runtime contract semantics
remain with the Interaction, Gateway, or Applications leaf named by the
component map. The old broad testing-and-conformance and
repository-maintainability pages remain navigation-only during the link
convergence.

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
