# Gateway delivery coordination design

Component ID: `gateway.delivery.coordination`

## Purpose and ownership

Coordination executes one bounded deterministic `DeliveryPlan` through the
selected Channel. It owns finite global and per-destination admission,
per-`ConversationRef` FIFO lanes, conservative retry execution, cancellation
and join, and aggregation of native segment receipts into one logical
`DeliveryReceipt`.

It does not own O1 presentation invocation or decisions, outbound claim
transitions, planning, native encoding or API calls, durable scheduling,
content storage, idempotency/checkpoints, route selection, product retry
appetite, or outcome observation. It receives only the already-presented
message selected by the O1/idempotency path; suppression and pre-Channel policy
failure never enter Coordinator planning or execution.

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

Coordinator selects `ConversationRef` as the destination key and owns FIFO
lane policy. It consumes the dependency-neutral `KeyedLockRegistry` mechanics
from `gateway.concurrency`; folding that shared primitive into Coordinator
would give delivery the wrong ownership because Gateway operation, request,
and proactive-ingress serialization also use it.

The registry does not need a second independent key bound because Coordinator
reserves one of its finite `max_pending` logical-delivery slots before planning
or entering the destination lane and retains that reservation through lane
exit. Therefore active destination keys can never exceed admitted pending
work. This is an enclosing finite admission proof, not an unbounded-capacity
exception.

## Authority

- [Architecture](../../../../ARCHITECTURE.md)
- [ADR 0010](../../../../decisions/0010-capability-driven-delivery-coordination.md)
- [Historical aggregate delivery design](../../../delivery-planning-and-coordination/design.md)
