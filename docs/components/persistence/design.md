# Persistence component design

## Purpose

Persistence stores only the minimal durable state owned by the IM bridge. It
must be possible to delete rebuildable caches and recover visible Agent output
from native authoritative sources.

## Ownership

Persistence owns implementations for:

- `ConversationBinding` with optimistic revision guards;
- `ThreadProjectionRoute`;
- inbound and outbound idempotency claim/completion state.

The current implementations are in-memory repositories and
`SQLiteGatewayState`.

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
| projection completion boundary | Gateway projection state | planned; never transcript content |

Binding updates are atomic from one Conversation's perspective. A stale
expected revision fails explicitly. Project/Thread references are validated
against Application ownership before they are persisted.

Route storage retains routing and correlation fields only. A future recovery
checkpoint may identify a completed projection boundary, but it cannot copy
message bodies, Turn status, or native execution state.

## Dependencies

Persistence implements repository Ports using Contract values. It does not
depend on Gateway orchestration or concrete integrations. SQLite schema and
row mapping are implementation details behind the Ports.

## Failure and recovery

Storage errors remain visible to Gateway. Idempotency has claim, complete, and
release states so failed work is not silently marked delivered.

At process restart, bindings and routes can be reloaded. Authoritative
Application history/catch-up reconciles message content; persistence never
reconstructs it from a local transcript.

## Change obligations

Changes to `bindings.py` or `storage.py` require checking schema migration,
restart behavior, revision conflicts, idempotency semantics, projection route
invariants, and the projections/recovery docs when checkpoint shape changes.
