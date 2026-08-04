# Gateway SQLite persistence design

Component ID: `gateway.persistence.sqlite`

Parent: `gateway.persistence`

## Purpose

This leaf is the single durable SQLite transaction and migration owner for all
minimal Gateway bridge-state repository protocols.

## Ownership

`SQLiteGatewayState` owns one connection, one process-local lock, schema
initialization and additive migration, explicit transaction boundaries, and
close lifecycle for bindings, projection routes/checkpoints, Turn and request
correlations, idempotency records, and proactive-delivery submissions.

It does not own Gateway policy, Application transcript/request/execution
truth, product JSON, delivery content, artifact bytes or arbitrary paths,
credentials, a durable job queue, spool, outbox, retry worker, or native
side-effect reconciliation.

## Atomic repository behavior

Repository mutations validate typed input before SQL and serialize through the
shared lock. Multi-read/write decisions use one immediate transaction and
roll back on failure. Binding revisions, stable route endpoint identity,
checkpoint compare-and-swap, create-only Turn correlations, request-wide
expected-state-fenced transitions, immutable delivery reservation identity, and expected
destination state are preserved atomically.

The SQLite repository verifies caller-supplied current states atomically
across every destination and independently rejects a target below any current
state in the accepted request-correlation graph. A compare-and-swap input
therefore cannot revive response authority or regress terminal bridge
evidence.

Idempotency rows contain a stable scope/key, state, owner token, and timestamp.
An absent row is acquired. A stale `in_flight` row may atomically replace its
owner token after the configured non-negative lease interval. A
`side_effect_started` row is never reclaimed by age. Owner-checked refresh,
protection, completion, and release keep a stale worker fenced.

Proactive-delivery root and destination rows persist only namespaced identity,
fingerprints, immutable route snapshots, typed outcomes/receipts, and
timestamps. An `in_flight` or unknown row after a crash is truthful ambiguity,
not authorization to resend. Content must be provided again by the caller or
recovered from Application authority; SQLite cannot replay a delivery body.

## Migration and restart

Schema initialization is additive and retains existing bridge state. The
legacy route upgrade adds explicit checkpoint fields and clears the old
`reply_to_message_id` values because their prior meaning cannot be safely
distinguished from durable reply context. Request-correlation and delivery-
submission tables are additive.

The SQLite owner is the only compatibility transformer. It supports the
immediately preceding route shape, applies that documented non-authorizing
upgrade once, and then exposes only current-schema rows to pure decoders.
Malformed current rows are rejected at read time without migration-time repair
or deletion. In particular, an absent binding Application cannot erase a
present Project/Thread scope, and an incomplete route/delivery/request shape
cannot acquire replay, reply, response, or resend authority.

WAL mode and the single connection/lock are implementation details of the
current adapter. Restart reconstructs typed bridge-state values from rows and
preserves terminal/ambiguous evidence. Any schema change requires an explicit
upgrade decision and tests against the immediately supported legacy shape.

## Structure

The complete transaction owner is now
`src/imagent/gateway/persistence/sqlite.py`. It co-locates
`SQLiteGatewayState`, schema initialization and the immediately supported
legacy migration, connection/lock/close behavior, all repository transaction
methods, request-correlation and delivery-submission SQL, and
`merge_projection_route` mutation policy. This remains one owner, not three
independent databases. Pure row conversion lives in
`gateway/persistence/row_mapping.py`; typed request-correlation policy lives
in `gateway/projection/request_correlation.py`; delivery identity and planning
remain in the delivery leaves. No historical SQLite implementation or
compatibility copy remains.

## Authority

- [Vision](../../../../VISION.md)
- [Architecture](../../../../ARCHITECTURE.md)
- [Persistence design](../../../persistence/design.md)
- [ADR 0004](../../../../decisions/0004-event-fanout-and-recovery.md)
- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0009](../../../../decisions/0009-proactive-delivery-routing.md)
- [ADR 0011](../../../../decisions/0011-durable-inbound-admission-before-media.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
