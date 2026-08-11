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
AgentMessage, history, Thread, request, or Gateway truth. Adapter mapping must
validate a finite native fact and its method-required identity before invoking
this leaf. An App Server mapping failure emits no partial event and terminates
the affected live projection through the fixed
`application_native_mapping_failed` recovery gap. Stable event/item identities
and any sequence/cursor fields describe only native guarantees; text and time
do not establish identity.

Every canonical event carries a required `ProjectRef`. When it also carries a
`ThreadRef`, the two Project references must match exactly. This holds for
message, Thread, Turn, status, and request events in managed, fixed, and flat
modes; adapters may not omit ancestry merely because the native protocol has
no Project object. Turn-scoped event families additionally require a
`TurnRef` whose nested Thread equals the event `ThreadRef`; request-opened and
request-resolved events use that same ancestry. Message-created and
message-completed events carry one typed `AgentMessage` whose Thread equals the
event Thread.

The sole implementation and import owner is `imagent.applications.events`, in
`src/imagent/applications/events.py`. The historical `imagent.events`,
`imagent.contracts`, and package-root `events` facades are absent.

## State, recovery, and structure

Subscriber queues are process-local, finite, and non-durable. A full queue
terminates only that subscriber with an explicit gap; it discards the live
projection rather than blocking native notification work or other Threads.
Gateway recovers from native replay where honestly supported, otherwise by
subscribing then reconciling authoritative history/catch-up. The SDK never
persists event bodies to bridge the gap.

The implementation is `schemas/v1/events.schema.json` plus
`src/imagent/applications/events.py`. Focused owner and fan-out evidence is
`tests/applications/test_events.py`; native adapter fan-out evidence is kept
with the Codex adapter, and Gateway observation/lifecycle/recovery evidence is
kept with its Gateway owners. The former root fan-out file is not an internal
compatibility path. The versioned schema uses the same required Project and
nested Thread/Turn shapes as the Python validator. The event module contains
no duplicate `AgentMessage` or resource model.

## Authority

- [Architecture](../../../ARCHITECTURE.md)
- [Projection/recovery design](../../projections-and-recovery/design.md)
- [ADR 0004](../../../decisions/0004-event-fanout-and-recovery.md)
- [ADR 0013](../../../decisions/0013-bounded-application-event-admission.md)
- [ADR 0016](../../../decisions/0016-uniform-workspace-and-consumer-actions.md)
