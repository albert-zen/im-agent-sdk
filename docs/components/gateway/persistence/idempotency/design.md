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

The process-local map has one positive finite `max_records` bound, defaulting
to 4096. Its capacity identity is the existing stable `(scope, key)` record
identity. Under the repository lock, `claim` first resolves an existing
record: completed records still return `already_completed`, and active or
protected records still return `in_flight` even when the map is full. Only an
absent identity at the bound raises the repository-contract-owned
`IdempotencyCapacityError` before mutating the map. It is never represented as
a retryable claim, a false replay, or a new owner.

The current owner can still refresh, protect, complete, or safely release its
existing record at capacity. An owner-checked release is the only transition
that can free a nonterminal record, and Gateway chooses it only when its
higher-level side-effect classification proves repetition safe. Completed
records are retained for the process lifetime: deleting their replay authority
just to admit a new identity could reauthorize a native or Channel side effect.
The implementation has no eviction, timed cleanup, tombstone replacement,
background worker, spool, or outbox.

The private Gateway runtime passes `GatewayLimits.idempotency_max_records` only
when it constructs this default repository. An explicitly supplied repository remains
the deployment's own configuration and is not wrapped or reconfigured. Restart
still discards the complete process-local map, while SQLite keeps its separate
durable retention and stale-lease semantics unchanged.

The map stores only scope, stable key, state, and owner token; it never stores
prepared media, rendered errors, content, a callback, or work to replay.

## Structure

The one implementation lives in `gateway/persistence/idempotency.py`.
`imagent.gateway.persistence.InMemoryIdempotencyRepository` is its exact
public re-export. `gateway.persistence.sqlite` is the separate SQLite
transaction owner and does not retain a compatibility implementation or
export for the process-local repository.

## Authority

- [Vision](../../../../VISION.md)
- [Architecture](../../../../ARCHITECTURE.md)
- [Persistence design](../../../persistence/design.md)
- [ADR 0006](../../../../decisions/0006-core-admission-and-policy-ownership.md)
- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0011](../../../../decisions/0011-durable-inbound-admission-before-media.md)
