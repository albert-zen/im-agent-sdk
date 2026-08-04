# Gateway SQLite row mapping design

Component ID: `gateway.persistence.row-mapping`

Parent: `gateway.persistence`

## Purpose

This leaf converts SQLite scalar/JSON rows to and from complete typed Gateway
bridge-state values. Mapping is deterministic and side-effect-free so
transaction and policy decisions remain with their owning components.

## Ownership

The leaf owns pure encoding/decoding of stable references, enums, timestamps,
optional values, receipts, response shapes, checkpoints, correlations, and
delivery snapshots. It may apply the state-contract validators to a completed
decoded value.

It does not own a connection or cursor lifecycle, locks, `BEGIN`, commit or
rollback, migrations, current time, revision/CAS policy, claim reclaim,
correlation transitions, route selection, delivery retries, or repair of
invalid persisted data.

## Mapping rules

The target mapper must reconstruct exact typed identity. Application, Project,
Thread, Conversation, request, route, delivery, and correlation scopes must
not be inferred from text or timestamps. Optional values must remain
distinguishable from empty stable identifiers. Enums and structured JSON use
closed typed decoders rather than falling back to a default policy or state.

Every current-schema row is decoded as a complete typed value and then passes
its owning state-contract validator. A nullable binding Application scope is
valid only when its Project and Thread columns are also null; a mapper must
reject, never discard, a partial scope. Checkpoint ID/time is an all-or-nothing
pair. Request shapes and delivery receipts are closed JSON payloads: unknown
kinds, missing required fields, incompatible scalar/container types, invalid
enums, timestamps, identities, or response cardinality reject the row.

## Compatibility decision

The immediately preceding supported SQLite schema is upgraded only by the
SQLite transaction owner before mapping: it adds the checkpoint columns and
clears the legacy `reply_to_message_id`, whose former latest-inbound meaning
cannot safely become durable reply authority. That valid legacy shape remains
supported. After that upgrade, every persisted row is interpreted as the
current schema and must be complete and valid. The mapper does not silently
repair malformed rows, guess a missing Application, default an enum, coerce a
partial route into an explicit Conversation snapshot, or authorize recovery,
replay, retry, or a native side effect. A malformed current row therefore
fails the repository read explicitly and remains operator-visible.

Datetime values round-trip in their declared ISO representation and are used
as facts, not idempotency keys or sortable Application cursors. Delivery
destination ordering in SQL is deterministic for observation, while
reservation identity remains order-independent through the state-contract
comparison.

Mapping never reads external files, resolves local paths, copies content, or
performs native/Channel I/O. It cannot authorize retry or turn a malformed row
into a new repository record.

## Structure

The pure mapper is implemented in
`src/imagent/gateway/persistence/row_mapping.py`. It owns binding, route,
Turn-correlation, request-correlation, delivery-submission, destination, and
receipt conversion, plus the narrow scalar and storage-key helpers needed by
those conversions. Encoders return SQLite scalar/JSON values or parameter
tuples; they never execute SQL. Decoders reconstruct complete typed values and
apply the owning state validator where one exists.

`storage.py`, `request_correlations.py`, and
`gateway/delivery/submissions.py` retain their transaction-owner methods,
schema initialization, SQL selection/insertion/update ordering, and mutation
policy while calling the mapper. `_canonical_metadata` remains owned by
`gateway.delivery.submissions`; it is delivery identity behavior rather than a
row codec. The historical `sqlite_rows.py` module now retains only
`merge_projection_route`, which is SQLite mutation policy and is not part of
the mapper; the later whole-owner move may co-locate it with `SQLiteGatewayState`.

This is a mechanical ownership move: it does not create a public facade,
second persistence path, generic serializer, or new durable state, and it does
not change the validation or compatibility decision above.

## Authority

- [Vision](../../../../VISION.md)
- [Architecture](../../../../ARCHITECTURE.md)
- [Persistence design](../../../persistence/design.md)
- [Repository maintainability](../../../../engineering/repository-maintainability/design.md)
- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0009](../../../../decisions/0009-proactive-delivery-routing.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
