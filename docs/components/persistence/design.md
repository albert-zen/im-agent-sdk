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
- inbound and outbound idempotency claim/completion state.

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
| projection completion boundary | Gateway projection state | per route; never transcript content |
| Turn reply correlation | Gateway projection state | active IM-originated Turns only |

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
Neither state may copy message bodies, Turn status, or native execution state.

## Dependencies

Persistence implements repository Ports using Contract values. It does not
depend on Gateway orchestration or concrete integrations. SQLite schema and
row mapping are implementation details behind the Ports.

## Failure and recovery

Storage errors and checkpoint conflicts remain visible to Gateway.
Idempotency claims distinguish acquired, already-completed, and currently
in-flight work so completed delivery can converge a lagging checkpoint without
mistaking active work for success.

At process restart, bindings and routes can be reloaded. Authoritative
Application history/catch-up reconciles message content; persistence never
reconstructs it from a local transcript.

Opening a pre-checkpoint SQLite schema adds checkpoint/correlation storage
without losing bindings, routes, or idempotency records. Its legacy
`reply_to_message_id` values are cleared because the old Gateway used that
column for the latest inbound message and it cannot be distinguished safely
from an explicit destination/topic default.

## Change obligations

Changes to `bindings.py` or `storage.py` require checking schema migration,
restart behavior, revision conflicts, idempotency semantics, projection route
invariants, and the projections/recovery docs when checkpoint shape changes.
