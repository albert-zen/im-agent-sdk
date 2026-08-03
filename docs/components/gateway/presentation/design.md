# Gateway per-destination presentation design

Component ID: `gateway.presentation`

## Purpose and ownership

Presentation is the ADR 0015 O1 position. It applies one replay-safe policy to
one already-routed immutable projection message after Gateway owns its stable
outbound idempotency claim and before delivery planning. It may return bounded
replacement presentation or suppress only that destination.

It owns the typed policy/context/origin contract, finite runtime, output
validation, suppression representation, and fixed diagnostic failures. It
does not own native Application normalization, product/request presentation,
route/delivery identity, attachment trust, planning, delivery retry,
idempotency transitions, or checkpoint mutation.

## Invariants

- destination, delivery ID, reply, creation time, and attachment authority are
  fixed before policy invocation and cannot be changed;
- content/item/text/metadata facts and execution capacity/lifetime are finite;
- a crash before claim completion may reevaluate because no Channel side
  effect occurred;
- suppression completes outbound idempotency before authoritative checkpoint
  CAS; recovery converges a lagging checkpoint without reinvoking O1;
- live-only presentation never advances a completion checkpoint;
- policy failure before Channel effect releases only the owned claim;
- absence preserves the prior projection delivery path exactly.

The implementation lives once at `src/imagent/gateway/presentation.py`; no
historical `imagent.outbound_presentation` implementation/import path remains.
Shared diagnostic values stay in `diagnostics.py` until their focused split.

## Authority

- [Architecture](../../../ARCHITECTURE.md)
- [ADR 0007](../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0015](../../../decisions/0015-typed-extension-seams-and-composition.md)
