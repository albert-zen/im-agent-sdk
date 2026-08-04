# Gateway delivery coordination testing

Focused tests mirror the owner at
`tests/gateway/delivery/test_coordination.py` and prove:

- bounded non-blocking global and per-destination admission;
- capacity reservation before planning and explicit source/segment limits;
- same-destination FIFO with concurrent progress for other destinations;
- retry delay releases global capacity but retains destination ordering;
- only explicit retryable receipts retry, and native retry-after is not
  shortened;
- exception, unknown, rejected, partial, and invalid outcomes do not retry;
- segment/source aggregation preserves accepted prefixes and skipped suffixes;
- awaited cancellation and close cancel/join native sends;
- a quiescent closed Coordinator can start one clean new Gateway lifecycle;
- public values exported by `imagent.gateway.delivery` are the exact owner
  objects; and
- the historical `imagent.delivery_coordination` implementation module is
  absent.

Planning golden tests remain in `test_planning.py`. Projection, proactive,
artifact-lifetime, O2, and Gateway vertical tests continue proving that every
origin consumes this same Coordinator without changing idempotency,
checkpoint, cleanup, or recovery behavior.

The dependency-neutral registry's bound validation, equal-key join,
independent progress, and owner/waiter cancellation cleanup live in
`tests/gateway/test_concurrency.py`. Coordination tests retain destination-key,
FIFO, admission, retry-delay, receipt, and worker-lifetime policy evidence.
They also prove that blocked work at finite `max_pending` cannot create more
active destination registry keys than the already-reserved pending capacity.
