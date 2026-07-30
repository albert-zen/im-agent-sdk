# Gateway component design

## Purpose

The Gateway composes Channel adapters, optional Controllers, Agent Application
adapters, bridge-state repositories, and projection/recovery services. It is
the deterministic IM boundary, not an Agent runtime.

## Ownership

The Gateway owns:

- resolving an inbound Conversation to its selected Application/Project/Thread;
- executing typed Gateway operations atomically per Conversation;
- routing typed Application operations without reinterpreting them;
- inbound idempotency and outbound delivery correlation;
- establishing Thread observation before input delivery;
- coordinating projection workers, route lookup, and Channel send;
- per-Conversation serialization and explicit delivery errors.

It does not own:

- native resource registries, transcript, Turn, request, or execution truth;
- native active/open Thread state;
- Slash grammar, command aliases, or fixed presentation;
- Channel Markdown, chunking, rate limits, or credentials;
- Application workspace, model, provider, sandbox, or runtime mode;
- a durable job system or retry policy not proven by consumers.

## Dependencies

Gateway may depend on Contracts/Core, adapter ports, bridge-state
repositories, Controllers, and projection/recovery. None of those components
may import Gateway.

## Normal input flow

1. A Channel emits a verified `InboundMessage`.
2. Gateway claims the stable inbound idempotency key.
3. An optional Controller may consume the input through typed actions.
4. Unconsumed content resolves the current `ConversationBinding`.
5. Gateway establishes or refreshes a `ThreadProjectionRoute`.
6. It starts Thread observation before calling `send_input`.
7. The Application emits authoritative user and Agent events.
8. Projection resolves destinations at delivery time.
9. Channel sends an `OutboundMessage`; Gateway records correlation outcome.

Listing never changes a binding. Binding a Thread does not activate native UI
state. Observing a Thread does not select it for future input.

## Failure and restart

Conversation mutations use revision guards and serialize per Conversation.
Idempotency claims are completed only after the scoped operation succeeds and
are released on retryable failure.

Application and Channel failures remain typed or explicitly reported. Gateway
does not convert unknown delivery into success.

On restart, current Gateway rebuilds Thread projection workers from persisted
routes and reconciles from authoritative Application history/catch-up plus
delivery IDs. It never loads an SDK transcript. The current reconciliation can
scan the complete archive; bounded checkpoint behavior is a target in
[Issue #14](https://github.com/albert-zen/im-agent-sdk/issues/14), not an
implemented guarantee.

Current projection delivery awaits the Channel send in a Thread worker.
Application notification callbacks remain non-blocking because they publish
into independent subscriber queues, but those queues are not yet bounded.
Bounded delivery execution, backpressure, and retry belong to the planned
Delivery Coordinator work in
[Issue #12](https://github.com/albert-zen/im-agent-sdk/issues/12); until then
memory pressure from a persistently slow Channel is an explicit limitation.

Current projection worker failure recovery and route reply correlation have
known gaps/ambiguities. Worker creation's current single-event-loop invariant
lacks direct concurrency coverage. Their target invariants and tests are
tracked by
[Issue #14](https://github.com/albert-zen/im-agent-sdk/issues/14).

## Change obligations

Changes to `gateway.py` require checking:

- projection/recovery design when subscription, routing, or recovery changes;
- persistence design when stored bridge state changes;
- Controller design when the inbound extension seam changes;
- Gateway operation and vertical-slice tests;
- all fake, Codex, Zen, T3, and Channel seams affected by orchestration.
