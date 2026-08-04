# Application events design

Component ID: `applications.events`

Parent: `applications`

## Purpose and ownership

This leaf defines canonical `AgentEvent` values, their type/ordering fields,
and bounded independent live fan-out through `EventBroadcaster`. It owns
`AgentEvent`, `AgentEventType`, `EventBroadcaster`, `EventStreamGap`, and
`validate_agent_event`. `EventSequenceScope` is the capability declaration
that qualifies whether native ordering can be claimed, so it belongs to
`applications.capabilities`.

It does not own a native event journal, transcript, Gateway projection worker,
Channel delivery, or a second Application subscription. The adapter owns
normalization from native event/history; Gateway owns its one-worker observation
and route/checkpoint recovery.

## Inputs, outputs, and dependencies

Normalized native events enter this leaf and each subscriber receives its own
bounded Thread event stream. It consumes Applications-owned `AgentMessage`
values for canonical message payloads and their strong `ThreadRef` scope,
alongside Interaction message and validation values, the Applications
capability declaration, and the request leaf. It owns only the event envelope,
ordering fields, validation, fan-out, and explicit gaps; it never owns
AgentMessage, history, Thread, request, or Gateway truth. Stable event/item
identities and any sequence/cursor fields describe only native guarantees; text
and time do not establish identity.

The implementation is `imagent.applications.events`, in
`src/imagent/applications/events.py`. `imagent.contracts` and `imagent.events`
remain exact public facades for the v1 values; neither contains a second event
implementation.

## State, recovery, and structure

Subscriber queues are process-local, finite, and non-durable. A full queue
terminates only that subscriber with an explicit gap; it discards the live
projection rather than blocking native notification work or other Threads.
Gateway recovers from native replay where honestly supported, otherwise by
subscribing then reconciling authoritative history/catch-up. The SDK never
persists event bodies to bridge the gap.

The implementation is `schemas/v1/events.schema.json` plus
`src/imagent/applications/events.py`. Focused owner evidence is
`tests/applications/test_events.py`; adapter and Gateway integration coverage
remains alongside those consumers. The schema and event semantics are
unchanged by this mechanical move, and the event module contains no duplicate
AgentMessage or resource model.

## Authority

- [Architecture](../../../ARCHITECTURE.md)
- [Projection/recovery design](../../projections-and-recovery/design.md)
- [ADR 0004](../../../decisions/0004-event-fanout-and-recovery.md)
- [ADR 0013](../../../decisions/0013-bounded-application-event-admission.md)
