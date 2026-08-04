# Gateway per-destination presentation design

Component ID: `gateway.presentation`

## Purpose and ownership

Presentation is the ADR 0015 O1 position. It applies one replay-safe policy to
one already-routed immutable projection message after Gateway owns its stable
outbound idempotency claim and before delivery planning. It translates the
projection checkpointability fact into the fixed origin vocabulary and returns
one typed post-claim decision: bounded replacement presentation, suppression,
or policy failure for that destination.

It owns the typed policy/context/origin contract, checkpointability-to-origin
mapping, finite runtime, output validation, post-claim decision representation,
and fixed diagnostic failures. It does not own native Application
normalization, product/request presentation, route/delivery identity,
attachment trust, planning, delivery retry, idempotency claim/complete/release
transitions, or checkpoint mutation.

## Invariants

- destination, delivery ID, reply, creation time, and attachment authority are
  fixed before policy invocation and cannot be changed;
- content/item/text/metadata facts and execution capacity/lifetime are finite;
- the owner accepts no repository, claim owner token, or completion/release
  callback: its narrow post-claim result is presented, suppressed, or failed;
- Gateway's idempotency owner completes a suppressed claim and releases a
  failed/cancelled pre-Channel claim from that typed decision; the presentation
  owner never performs those transitions;
- a crash before claim completion may reevaluate because no Channel side
  effect occurred;
- after the idempotency owner completes suppression, the injected checkpoint
  authority performs its expected-current CAS; bounded recovery can converge a
  lagging checkpoint without reinvoking O1;
- live-only presentation never advances a completion checkpoint;
- policy failure before Channel effect releases only the owned claim;
- absence preserves the prior projection delivery path exactly.

The implementation lives once at `src/imagent/gateway/presentation.py`; no
historical `imagent.outbound_presentation` implementation/import path remains.
The O1 diagnostic enum and fact values are owned by
`src/imagent/gateway/diagnostics.py` and imported directly by this runtime.

## Authority

- [Architecture](../../../ARCHITECTURE.md)
- [ADR 0007](../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0015](../../../decisions/0015-typed-extension-seams-and-composition.md)
