# Application events design

Component ID: `applications.events`

Parent: `applications`

## Purpose and ownership

This leaf defines canonical `AgentEvent` values, their type/ordering fields,
and bounded independent live fan-out through `EventBroadcaster`. It owns
`AgentEvent`, `AgentEventType`, `EventSequenceScope`, `EventBroadcaster`,
`EventStreamGap`, and `validate_agent_event`.

It does not own a native event journal, transcript, Gateway projection worker,
Channel delivery, or a second Application subscription. The adapter owns
normalization from native event/history; Gateway owns its one-worker observation
and route/checkpoint recovery.

## Inputs, outputs, and dependencies

Normalized native events enter this leaf and each subscriber receives its own
bounded Thread event stream. It depends on Interaction message values for
canonical Agent content, but never on Gateway. Stable event/item identities
and any sequence/cursor fields describe only native guarantees; text and time
do not establish identity.

Current exports are split across `imagent.contracts` and `imagent.events`; the
target facade is `imagent.applications.events`, implemented once in
`src/imagent/applications/events.py`.

## State, recovery, and structure

Subscriber queues are process-local, finite, and non-durable. A full queue
terminates only that subscriber with an explicit gap; it discards the live
projection rather than blocking native notification work or other Threads.
Gateway recovers from native replay where honestly supported, otherwise by
subscribing then reconciling authoritative history/catch-up. The SDK never
persists event bodies to bridge the gap.

Current code is `schemas/v1/events.schema.json`,
`src/imagent/contracts/{model.py,validators.py}`, and `src/imagent/events.py`.
Current evidence is `tests/test_event_fanout.py` and `tests/test_contracts.py`;
the target is `tests/applications/test_events.py`. Event values remaining in
the cross-owner contract model are the explicit structural gap.

## Authority

- [Architecture](../../../ARCHITECTURE.md)
- [Projection/recovery design](../../projections-and-recovery/design.md)
- [ADR 0004](../../../decisions/0004-event-fanout-and-recovery.md)
- [ADR 0013](../../../decisions/0013-bounded-application-event-admission.md)
