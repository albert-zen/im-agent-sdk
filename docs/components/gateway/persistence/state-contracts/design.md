# Gateway persistence state contracts design

Component ID: `gateway.persistence.state-contracts`

Parent: `gateway.persistence`

## Purpose

This leaf defines immutable, validated values for the minimal bridge state
that Gateway repository Ports exchange. The values describe stable identity,
revision, routing, correlation, checkpoint, and delivery evidence. They do not
perform I/O or become a second source of Application truth.

## Ownership

This leaf owns:

- `ConversationBinding` as one current Application/Project/Thread selection
  for a stable Conversation plus its optimistic revision;
- `ProjectionPolicy` and `ThreadProjectionRoute`, including stable Thread and
  Conversation endpoints, optional reply context, and one opaque checkpoint;
- create-only `TurnReplyCorrelation` and `RequestRouteCorrelation` values;
- proactive-delivery reservation identity, immutable route snapshots,
  per-destination outcome evidence, and typed receipts; and
- pure validation of those complete values.

It does not own repository I/O, mutation ordering, route selection, binding
or projection orchestration, native request state, delivery execution,
message/artifact content, transcripts, credentials, leases, or retries.

## State boundaries

A Conversation binding records only Gateway's current input selection. Its
revision is a repository compare-and-swap fact, not an Application Thread
revision. The value contains no binding history, active-Turn state, or
projection worker authority.

A projection route is one stable Thread-to-Conversation edge. Its checkpoint
is an opaque authoritative Agent item identity paired with its checkpoint
time; neither the item ID nor the time is a bridge sequence number. Route
policy names the accepted destination-selection semantics but the value does
not execute that policy.

A Turn reply correlation stores only the stable accepted Turn and its original
IM reply destination. Repeated creation of the same immutable value is safe;
retargeting the same Thread/Turn is not. A request correlation stores only the
Application-scoped request identity, response-shape validation facts,
destination, and bridge routing state. Its `open`, `responded`, `resolved`, and
`stale` states never replace native request truth. The passive value validates
one declared state; the repository contract separately enforces the monotonic
transition graph and uses caller-supplied expected states only as an atomic
compare-and-swap fence.

A delivery submission contains an SDK-controlled origin/principal identity,
target and payload fingerprints, the complete immutable destination snapshot
set, and mutable per-destination outcome evidence. The snapshot set pins one
logical delivery to the originally resolved destinations across retries and
restart. It contains no text, artifact bytes, arbitrary local path, callback,
or replayable work body, so it is neither a content spool nor an outbox.

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

The values currently share `contracts/model.py`, `contracts/delivery.py`, and
`contracts/request_validation.py`, plus their JSON schemas. That is an
explicit split candidate. A later mechanical slice will move the complete
passive surface to `gateway/persistence/state_contracts.py` and its facade
without retaining two internal implementations.

## Authority

- [Vision](../../../../VISION.md)
- [Architecture](../../../../ARCHITECTURE.md)
- [Persistence design](../../../persistence/design.md)
- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0009](../../../../decisions/0009-proactive-delivery-routing.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
