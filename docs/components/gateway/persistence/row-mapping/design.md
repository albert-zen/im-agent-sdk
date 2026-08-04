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

The current helpers do not yet meet that complete target. In particular,
`binding_from_row` silently collapses project/thread columns when the
Application column is null, and route/checkpoint/Turn decoders do not all run
the complete state-contract validator after construction. This is an explicit
validation gap. The later row-mapping move must preserve current behavior as a
mechanical slice; tightening malformed-row behavior requires its own focused
behavior issue and migration compatibility decision.

Datetime values round-trip in their declared ISO representation and are used
as facts, not idempotency keys or sortable Application cursors. Delivery
destination ordering in SQL is deterministic for observation, while
reservation identity remains order-independent through the state-contract
comparison.

Mapping never reads external files, resolves local paths, copies content, or
performs native/Channel I/O. It cannot authorize retry or turn a malformed row
into a new repository record.

## Structure

Binding, route, and Turn-correlation helpers currently live in
`sqlite_rows.py`. Request-correlation and delivery-submission row helpers
remain mixed into their transaction mixins, and a few SQL argument mappings
remain in `storage.py`. These are explained split candidates. Although it is
currently colocated in `sqlite_rows.py`, `merge_projection_route` is not a
mapper: it enforces endpoint conflicts and preserves a prior checkpoint during
a route write. That mutation policy belongs to the SQLite owner and must move
with it. The target mechanical slice consolidates only pure conversion in
`gateway/persistence/row_mapping.py`, without moving SQL mutation order or
creating a public API, and without silently folding the separate validation
gap into a file move.

## Authority

- [Vision](../../../../VISION.md)
- [Architecture](../../../../ARCHITECTURE.md)
- [Persistence design](../../../persistence/design.md)
- [Repository maintainability](../../../repository-maintainability/design.md)
- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0009](../../../../decisions/0009-proactive-delivery-routing.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
