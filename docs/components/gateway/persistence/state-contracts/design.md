# Gateway persistence state contracts design

Component ID: `gateway.persistence.state-contracts`

Parent: `gateway.persistence`

## Purpose

This leaf defines immutable, validated values for the minimal bridge state
that Gateway repository Ports exchange. The values describe stable identity,
generation, routing, correlation, checkpoint, and delivery evidence. They do not
perform I/O or become a second source of Application truth.

## Ownership

This leaf owns:

- `ConversationBinding` as one current Application/Project/Thread selection
  for a stable Conversation plus its optimistic generation;
- `ThreadProjectionRoute`, including stable Thread and Conversation endpoints,
  optional reply context, and one opaque checkpoint;
- create-only `TurnReplyCorrelation` and `RequestRouteCorrelation` values;
- proactive-delivery reservation identity, immutable route snapshots,
  per-destination outcome evidence, and typed receipts; and
- pure validation of those complete values.

It does not own repository I/O, mutation ordering, route selection, binding
or projection orchestration, native request state, delivery execution,
message/artifact content, transcripts, credentials, leases, or retries.

## State boundaries

A Conversation binding records only Gateway's current input selection. Its
generation is a durable repository compare-and-swap fact, not an Application
Thread generation. The value contains no binding history, active-Turn state, or
projection worker authority.
Generation values are strict non-Boolean, non-negative integers; Boolean
coercion cannot satisfy a binding or receipt compare-and-swap boundary.

The selection is hierarchical: a Thread requires an equal Project and
Application selection. Every Thread-bearing route, checkpoint, reply/request
correlation, and delivery snapshot reaches the same required Project through
`ThreadRef`; fixed/flat mode has no persistence exception. A binding may still
be unbound, Application-only, or Project-only.
Every present Application and Project reference is itself validated before
those ancestry comparisons, so a structurally complete binding cannot carry an
empty or otherwise invalid ancestor identity.

A projection route is one stable Thread-to-Conversation edge. Its checkpoint
is an opaque authoritative Agent item identity paired with its checkpoint
time; neither the item ID nor the time is a bridge sequence number. The
projection-route owner defines and executes the accepted destination-selection
policy; this passive value neither imports nor executes it.

A Turn reply correlation stores only the stable accepted Turn and its original
IM reply destination. Repeated creation of the same immutable value is safe;
retargeting the same Thread/Turn is not. A request correlation stores only the
Application-scoped request identity, response-shape validation facts,
destination, and bridge routing state. Its `open`, `responded`, `resolved`, and
`stale` states never replace native request truth. The passive value validates
one declared state; the repository contract separately enforces the monotonic
transition graph and uses caller-supplied expected states only as an atomic
compare-and-swap fence.

A delivery submission contains an SDK-controlled origin/caller-delivery
identity plus the admitting principal as reservation evidence, target and
payload fingerprints, the complete immutable destination snapshot
set, and mutable per-destination outcome evidence. The snapshot set pins one
logical delivery to the originally resolved destinations across retries and
restart. It contains no text, artifact bytes, arbitrary local path, callback,
or replayable work body, so it is neither a content spool nor an outbox.
Durable destination evidence uses only closed submission/receipt/item/segment
states, bounded stable delivery/attachment/native-message identities, and
bounded retry timing. `DeliveryReceipt.detail`, item/segment detail, and
free-form destination errors are presentation/debug facts and are never part
of `DestinationDeliveryRecord`; the delivery runtime may expose them only in
the process that observed them.
The submission origin enum is physically defined beside the record so the
record remains a closed passive value, but its supported public facade and
behavioral ownership stay in `gateway.delivery.submissions`; the persistence
facade does not export it. The remaining passive state/record/reservation
values are public from this leaf. The proactive target,
intent, result, and validator vocabulary is Gateway delivery behavior, not
passive state. The closed submission identity/fingerprint helpers are also
Gateway delivery behavior, not passive state; their sole implementation is
`gateway.delivery.submissions`.

Stable scalar identities and every persisted collection are validated against
their declared limits and cross-reference rules before fingerprinting,
reservation, SQL, or native delivery/request side effects. Invalid complete
typed values fail explicitly rather than being repaired by a repository
implementation. `DeliverySubmissionRecord.destinations` has at most 64
members. An interactive request and its persisted response shape have at most
32 questions; an approval or one question has at most 64 choice IDs. These
are admission limits, not retention policy: the SDK neither evicts delivery
evidence nor spools excess work.

## Dependencies and recovery

State contracts depend only on stable Interaction/Application references and
request types. Repository implementations own atomic mutation and recovery.
After restart these values can restore bridge routing evidence, but visible
Agent output remains recoverable only from authoritative Application history
or catch-up.

## Structure

The values previously shared `contracts/model.py`, `contracts/delivery.py`, and
`contracts/request_validation.py`, plus their JSON schemas. The mechanical
extraction now has one implementation in
`gateway/persistence/state_contracts.py`; the versioned schemas remain in
`schemas/v1` and the old internal modules are removed.

`imagent.gateway.persistence` is the finite Python facade. The historical
cross-layer `imagent.contracts` module is absent; scoped actions consume the
focused persistence-state owner directly.

## Authority

- [Vision](../../../../VISION.md)
- [Architecture](../../../../ARCHITECTURE.md)
- [Gateway persistence](../README.md)
- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0009](../../../../decisions/0009-proactive-delivery-routing.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
- [ADR 0016](../../../../decisions/0016-uniform-workspace-and-consumer-actions.md)
