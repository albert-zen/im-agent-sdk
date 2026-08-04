# Gateway idempotency persistence design

Component ID: `gateway.persistence.idempotency`

Parent: `gateway.persistence`

## Purpose

This leaf implements the process-local reference idempotency repository for
stable inbound and outbound claim keys. It enforces explicit acquisition and
owner-token fencing around the boundary where repeating a native side effect
becomes unsafe.

## Ownership

The leaf owns `InMemoryIdempotencyRepository`, its process-local claim map,
and atomic owner-checked transitions among `in_flight`,
`side_effect_started`, and `completed`.

It does not own idempotency key derivation, native idempotency guarantees,
Gateway claim policy, timeout/retry scheduling, message or artifact content,
durable recovery, or SQLite's lease timestamps.

## Claim and fencing semantics

`claim(scope, key, owner_token)` returns `acquired` only for an absent record.
An existing completed record returns `already_completed`; an active or
protected record returns `in_flight`. Every mutating operation compares the
opaque owner token while holding the repository lock, so a stale worker cannot
refresh, protect, complete, or delete another owner's claim.

`refresh` is valid only for the current `in_flight` owner. It does not change
state. `mark_side_effect_started` is the durable-policy fence expressed by the
common Port: after Gateway crosses it, elapsed time must not independently
authorize a repeated native side effect. `complete` records terminal
idempotency evidence. `release` is an owner-checked primitive for Gateway to
use only when higher-level classification proves that repeating is safe; the
repository does not make that safety decision.

The in-memory reference has no clock and never reclaims a live-process record
by age. Restart discards its complete map. Timed stale-`in_flight` reclaim is a
durable SQLite implementation behavior, not a promise manufactured by this
leaf. In both implementations `side_effect_started` is excluded from timed
reclaim.

## Capacity and state

The implementation is intentionally process-local and currently has no
independent finite record limit. It is therefore a structural gap for
long-lived production use, not a durable alternative to SQLite. It stores
only scope, stable key, state, and owner token; it never stores prepared media,
rendered errors, content, a callback, or work to replay.

## Structure

The one implementation lives in `gateway/persistence/idempotency.py`.
`imagent.gateway.persistence.InMemoryIdempotencyRepository` is its exact
public re-export. `storage.py` remains the separate SQLite transaction owner
and does not retain a compatibility implementation or export for the
process-local repository.

## Authority

- [Vision](../../../../VISION.md)
- [Architecture](../../../../ARCHITECTURE.md)
- [Persistence design](../../../persistence/design.md)
- [ADR 0006](../../../../decisions/0006-core-admission-and-policy-ownership.md)
- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0011](../../../../decisions/0011-durable-inbound-admission-before-media.md)
