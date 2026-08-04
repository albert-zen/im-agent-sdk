# Persistence component design

## Purpose

Persistence stores only the minimal durable state owned by the IM bridge. It
must be possible to delete rebuildable caches and recover visible Agent output
from native authoritative sources.

## Ownership

Persistence owns implementations for:

- `ConversationBinding` with optimistic revision guards;
- `ThreadProjectionRoute`;
- minimal per-Turn IM reply correlations;
- minimal per-destination interactive-request correlations;
- inbound and outbound idempotency claim/completion state.
- proactive-delivery identity, immutable route snapshots, destination states,
  and typed receipts.

The current implementations are in-memory repositories and
`SQLiteGatewayState`; `sqlite_rows.py` contains only SQLite row/merge mapping
helpers used by that implementation.

`SQLiteGatewayState` intentionally keeps bindings, routes/correlations, and
idempotency in one adapter because they share one connection, lock, migration,
and transaction boundary. Row conversion is extracted, but splitting the
transaction owner merely to meet a line-count warning would weaken that
boundary without creating a second responsibility.

It must not store:

- Agent transcript items or a second Thread history;
- authoritative Turn, request, execution, or status state;
- Application project/Thread registries;
- Channel credentials;
- product job queues or orchestration state.

## State model

| Stored state | Authority | Durability |
|---|---|---|
| Conversation input selection | Gateway | optional in-memory or SQLite |
| Thread output route | Gateway | optional in-memory or SQLite |
| inbound idempotency | Gateway | deployment choice |
| outbound delivery completion | Gateway | deployment choice |
| projection completion boundary | Gateway projection state | per route; completed delivery or durable presentation suppression, never transcript content |
| Turn reply correlation | Gateway projection state | active IM-originated Turns only |
| Request route correlation | Gateway projection state | delivered request/destination identity only |
| Proactive delivery submission | Gateway | fingerprints, route snapshots, outcome/receipt only |

Binding updates are atomic from one Conversation's perspective. A stale
expected revision fails explicitly. Project/Thread references are validated
against Application ownership before they are persisted.

Route storage retains routing and per-route delivery fields only. A normal
refresh with no checkpoint preserves an existing boundary. A `put` that
carries a different checkpoint is rejected: only
`advance_projection_checkpoint` may move the boundary, and it uses an expected
checkpoint compare-and-swap so stale calls cannot regress or replace newer
progress. Stable route IDs and their Thread/Conversation endpoints are a
one-to-one identity; either direction of conflicting reuse is rejected by
both repositories. Opaque Agent item IDs are not sortable bridge sequence
numbers. A
Turn reply correlation stores only native identity and its originating IM
target, and is removed on a terminal Turn event or bounded cleanup. Deletion
requires an explicit Thread, Conversation, or retention cutoff selector;
clearing every correlation is not an accidental zero-argument operation.
Insertion is create-only for the Thread/Turn key: an identical repeat is
idempotent and any different immutable value is a
`TurnReplyCorrelationConflict`. SQLite uses no destination-replacing upsert.
Neither state may copy message bodies, Turn status, or native execution state.

ADR 0015 does not add a suppression table or policy payload. O1 uses the same
stable outbound idempotency claim as delivery: it completes that claim before
checkpoint compare-and-swap. An already-completed claim can therefore
converge a lagging route boundary after restart without persisting content,
visibility settings, or a second outcome authority.

O2 adds no outcome-notification, callback, content, cleanup, spool, or outbox
state. Durable delivery destinations and receipts remain the only bridge
record. A replayed record does not fabricate a Coordinator attempt or replay a
process-local observer notification; consumers that require crash-safe cleanup
own their bounded ledger and startup sweep.

A request route correlation stores no prompt, requested permissions, or
response. Its persisted response shape is only routing-validation state, not
native request truth. `RequestRef` scopes the opaque native ID by Application,
including an adapter-generated epoch namespace when transport IDs are reused.
Repository selectors, uniqueness, and transitions use that complete identity.
Its state only controls whether this bridge may route another response.
Repository transitions are request-wide so multi-destination records converge
together from `open` to `responded`, then to native
`resolved` or adapter-proven `stale`. Retention cleanup bounds missing terminal
events and requires an explicit selector. Creating a later destination is
atomic with those transitions: when any existing destination has already
advanced, the new correlation inherits the most advanced request-wide state
and cannot reopen response authority. `stale` is monotonic for one
epoch-scoped `RequestRef`; a later native transport reuse must have a new
namespaced reference rather than reviving the old one.

## Dependencies

Persistence implements repository Ports using Contract values. It does not
depend on Gateway orchestration or concrete integrations. SQLite schema and
row mapping are implementation details behind the Ports.

## Failure and recovery

Storage errors and checkpoint conflicts remain visible to Gateway.
Idempotency claims distinguish acquired, already-completed, and currently
in-flight work so completed delivery can converge a lagging checkpoint without
mistaking active work for success.

An `in_flight` claim is a reclaimable lease so a crash before work begins does
not permanently block inbound or outbound progress. Immediately before a
non-idempotent remote input is dispatched, Gateway durably transitions that
claim to `side_effect_started`. A crash, unknown outcome, or failed terminal
write after that boundary is ambiguous, so elapsed wall-clock time never turns
the protected record back into permission to repeat the operation. Only an
explicit release on a failure proven to precede dispatch makes it claimable
again.

When a configured ADR 0015 I2 presenter consumes a `pre_acceptance` failure,
Gateway explicitly completes the inbound claim before attempting error
delivery. Persistence still applies the same fenced owner-token transition;
the presenter cannot mutate or release the record. Without I2, proven
pre-dispatch failure retains the ordinary explicit-release behavior.
Cancellation releases only while dispatch is still proven absent; cancellation
after the durable dispatch fence preserves `side_effect_started`.

I2 error presentation uses the ordinary outbound-idempotency namespace with a
stable Gateway-derived delivery identity. Only that existing bridge delivery
record is persisted; no exception, rendered content, failure transcript,
durable presentation job, spool, or outbox is added. The presenter cannot
select a claim transition or inspect persistence.

Every acquired lease carries an opaque owner token. Reclaim replaces the
token, and `refresh`, `mark_side_effect_started`, `complete`, and `release`
compare it atomically. Refresh updates only the timestamp of the caller's
`in_flight` lease; it cannot revive or alter a protected side-effect state. A
stale worker therefore cannot hand off prepared media, protect, complete, or
delete the new owner's record.

At process restart, bindings and routes can be reloaded. Authoritative
Application history/catch-up reconciles message content; persistence never
reconstructs it from a local transcript.

Opening a pre-checkpoint SQLite schema adds checkpoint/correlation storage
without losing bindings, routes, or idempotency records. Its legacy
`reply_to_message_id` values are cleared because the old Gateway used that
column for the latest inbound message and it cannot be distinguished safely
from an explicit destination/topic default.

Adding request correlation storage is an additive SQLite migration. Existing
databases retain bindings, routes, Turn correlations, and idempotency records.

Adding proactive delivery storage is also additive. The root table contains an
SDK-origin-and-principal-derived submission ID, caller delivery ID, the
SDK-controlled origin, principal/target/payload fingerprints, and timestamps.
Child rows contain the pinned destination route identity and typed outcome,
including explicit retryable failure and an optional retry-after hint.
Neither table stores text, inline artifact bytes, local paths from message
content, bot credentials, or a replayable job body. An `in_flight` row left by
a crash is truthful ambiguity, not evidence that a resend is safe.

Delivery reservation identity includes the complete order-independent mapping
of destination delivery IDs to route snapshots. Both memory and SQLite reject
stable-ID reuse when that map differs, while ignoring mutable outcome and
receipt fields during an identical replay. SQLite reconstructs the same map
after restart; no additional identity column or schema migration is required.

## Change obligations

Changes to `bindings.py` or `storage.py` require checking schema migration,
restart behavior, revision conflicts, idempotency semantics, projection route
invariants, and the projections/recovery docs when checkpoint shape changes.
