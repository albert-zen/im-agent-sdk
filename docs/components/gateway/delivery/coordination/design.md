# Gateway delivery coordination design

Component ID: `gateway.delivery.coordination`

## Purpose and ownership

Coordination executes one bounded deterministic `DeliveryPlan` through the
selected Channel. It owns finite global and per-destination admission,
per-`ConversationRef` FIFO lanes, conservative retry execution, cancellation
and join, and aggregation of native segment receipts into one logical
`DeliveryReceipt`.

It does not own planning, native encoding or API calls, durable scheduling,
content storage, idempotency/checkpoints, route selection, product retry
appetite, or outcome observation.

## Execution invariants

- global and per-destination capacity is reserved before planning;
- one logical plan has finite source-item and segment limits;
- a destination lane stays ordered across retry delay, while the global send
  slot is released so independent destinations can progress;
- only an explicit retryable receipt may be retried when configured;
- rejection, exception, invalid receipt, partial acceptance, and unknown
  outcome never authorize replay;
- native retry-after is a minimum and is never shortened;
- cancellation and Gateway stop join admitted work before staged resources or
  Channel lifecycle are released;
- idle keyed lanes and all reserved capacity are released explicitly.

Receipt aggregation retains every planned segment and source-content index.
Execution stops after the first non-accepted segment and marks the untouched
suffix skipped. Mixed evidence for one split source item becomes unknown, so
an accepted prefix cannot be hidden by a later failure.

The implementation lives once at
`src/imagent/gateway/delivery/coordination.py`. The Gateway delivery package
re-exports the exact coordinator values. The package root may retain a stable
`delivery_coordination` module attribute as an exact alias, but the historical
internal module is not retained as an import path or implementation.

`KeyedLockRegistry` remains in its current shared utility module during this
slice because Gateway request serialization and proactive delivery ingress
also use it. Folding that shared primitive into Coordinator would create the
wrong ownership. The component map records this as a structural gap for a
later focused ownership split.

## Authority

- [Architecture](../../../../ARCHITECTURE.md)
- [ADR 0010](../../../../decisions/0010-capability-driven-delivery-coordination.md)
- [Historical aggregate delivery design](../../../delivery-planning-and-coordination/design.md)
