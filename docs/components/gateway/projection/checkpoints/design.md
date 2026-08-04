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

## Physical boundary

Checkpoint values contain only a stable item ID and time, not content,
transcript copies, native cursor invention, or unbounded history. Existing
routes scan only bounded authoritative pages toward their opaque boundary;
missing/expired boundaries become explicit degraded recovery, not a full
archive scan.

The canonical implementation is
`src/imagent/gateway/projection/checkpoints.py`. In addition to stable
destination-scoped projection delivery-ID derivation, it owns the one narrow
checkpoint authority. That authority is constructed with only the typed
`ProjectionRouteRepository`; it receives a current route, opaque Agent item
identity, whether the observation is checkpointable, the completed-idempotency
outcome, and whether the evidence came from bounded authoritative recovery.

It rejects an `in_flight` outcome, leaves live-only output unchanged, and
never derives progress by comparing opaque item IDs. A fresh completed outcome
uses the route's current checkpoint as the expected value for one repository
CAS. An `already_completed` outcome converges a lagging checkpoint only when
ordered authoritative recovery supplied the evidence; a live duplicate does
not guess a new boundary. A same-item repeat needs no CAS, and a competing or
stale expected value remains the repository's explicit conflict.

`imagent.projections` remains an observation-side cross-owner consumer: it
retains projected-message construction, reply correlation lookup, and the call
into the injected checkpoint authority after the existing delivery/O1 path
returns its typed outcome. That forwarding does not make a checkpoint decision
or perform a CAS.
`projection_routes.py` retains route bootstrap, bounded recovery orchestration,
and route ordering, but no checkpoint decision. O1 presentation returns a
typed presented/suppressed/failed decision after a stable outbound claim
exists; the idempotency owner completes a suppression before this authority
can advance a route, or releases a failed/cancelled pre-Channel claim. Recovery
supplies bounded ordered evidence and does not call route persistence directly.
Persistence continues to own the passive route record and atomic repository
operations.

The finite `imagent.gateway.projection` facade remains exact re-exports only;
it exposes the stable delivery-ID function and does not become a second
checkpoint implementation or service locator.

- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)
