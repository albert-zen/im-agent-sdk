# Gateway delivery planning design

Component ID: `gateway.delivery.planning`

## Purpose and ownership

Planning is a pure deterministic conversion from one logical
`OutboundMessage` plus a declared Channel `DeliveryProfile` into a bounded
immutable `DeliveryPlan`. It owns capability preflight, content grouping,
stable segment indexes/identities, text measurement and split selection, and
the exact source-content indexes carried by each segment.

It does not own Channel encoding or API calls, Coordinator capacity/ordering,
retry appetite, native receipts, idempotency persistence, checkpoints,
proactive route selection, content bytes, or a durable job/outbox.

## Invariants

- the same validated message/profile produces the same ordered plan;
- every segment identity derives from the stable source delivery identity and
  index, never text or time;
- unsupported source/media/grouping/count/size fails before Channel side
  effects;
- source content order and indexes remain exact across splitting/grouping;
- all declared limits are finite and invalid/unmeasurable profiles fail
  explicitly.

The implementation lives once at
`src/imagent/gateway/delivery/planning.py`. The Gateway delivery package
re-exports the exact planner values. The finite Gateway package root does not
retain a `delivery_planning` module alias, and the historical internal module
is absent.

## Authority

- [Architecture](../../../../ARCHITECTURE.md)
- [ADR 0010](../../../../decisions/0010-capability-driven-delivery-coordination.md)
- [Interaction outbound delivery](../../../interaction/channels/outbound-delivery/design.md)
