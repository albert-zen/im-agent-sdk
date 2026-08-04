# Gateway projection checkpoints design

Component ID: `gateway.projection.checkpoints`

Parent: `gateway.projection`

## Purpose and ownership

This leaf owns the per-route boundary that says a stable authoritative Agent
item has durably completed one destination's projection decision. It owns
stable projection delivery-ID derivation, checkpoint compare-and-swap, and
ordered convergence after completed idempotency evidence.

It does not own Agent sequence ordering, transcript/history, route selection,
Channel sends, the native receipt, delivery retry policy, or live-only
completion. Opaque Agent item IDs are never sorted or compared to manufacture
progress.

## Durable decision ordering

Each `ThreadProjectionRoute` keeps its own opaque completed item ID and
boundary time because one destination may succeed, suppress, or fail while
another takes a different path. A normal route refresh preserves an omitted
checkpoint and rejects a conflicting supplied one. After the stable
destination-scoped outbound idempotency claim is completed, checkpoint advance
uses an expected-current compare-and-swap. `in_flight` evidence never
advances a boundary.

An accepted or already-completed Channel delivery is a completed decision. A
typed O1 suppression is also completed only after its outbound idempotency
claim completes; it is not a Channel receipt. If a crash occurs after durable
idempotency completion but before checkpoint CAS, ordered authoritative
recovery may advance the lagging checkpoint using that same stable delivery
ID. If it occurs before completion, O1 may be reevaluated because no Channel
side effect began. An already-completed live duplicate never guesses a
different checkpoint from opaque identity.

Live-only A1 output has an event-scoped delivery ID and may use outbound
idempotency for that attempt, but it never advances a completed-item
checkpoint or appears in authoritative completion recovery.

## Bounds and current structure

Checkpoint values contain only a stable item ID and time, not content,
transcript copies, native cursor invention, or unbounded history. Existing
routes scan only bounded authoritative pages toward their opaque boundary;
missing/expired boundaries become explicit degraded recovery, not a full
archive scan.

Stable delivery-ID derivation lives in `gateway/projection/checkpoints.py`.
The `imagent.gateway.projection` facade re-exports the exact owner function;
`imagent.projections` does not retain a compatibility symbol. Checkpoint CAS,
route interaction, and delivery orchestration remain split between
`projections.py` and `projection_routes.py`. Persistence continues to own the
passive route record and its atomic repository implementation.

- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)
