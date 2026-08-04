# Gateway concurrency design

Component ID: `gateway.concurrency`

Parent: `gateway`

## Purpose and ownership

`gateway.concurrency` owns the dependency-neutral process-local mechanics for
serializing work by a stable key. It provides one waiter-safe
`KeyedLockRegistry` implementation and the explicit
`KeyedLockCapacityError` raised when a new distinct key cannot be admitted.

This leaf owns:

- one `asyncio.Lock` entry per active key;
- owner-and-waiter usage accounting;
- optional finite active-key admission;
- release and exact-entry cleanup after success, failure, or cancellation; and
- the capacity and active-key count inspection facts used by focused tests and
  composing runtimes.

It does not own which key a caller chooses, what that key means, the bound a
consumer configures, routing or delivery policy, retry or backpressure policy,
error presentation, persistence, idempotency, a durable queue, or a global
registry. The primitive does not inspect, stringify, trim, namespace, or
otherwise normalize keys.

## Mechanics and bounds

`max_active_keys` is optional. When present it must be a positive integer and
must reject booleans. An already-active equal key joins its existing entry even
at capacity, so serialization never splits. A new distinct key at capacity
raises before waiting for a lock or entering caller work; no entry is evicted
to manufacture capacity.

`None` means only that this primitive adds no independent bound. It is not
permission for unbounded system state: a consumer using `None` must already be
inside a finite admission boundary that caps the number of simultaneously
active distinct keys. The current Coordinator reserves its finite
`max_pending` capacity before entering a destination lane, and request-response
serialization runs inside the finite active-Conversation operation registry.
Operations and proactive ingress instead configure an explicit registry bound.

The registry uses Python hash/equality semantics for the caller-supplied key.
It increments entry usage before the first wait, so the current owner and every
waiter protect the same entry. Cancellation while waiting decrements usage
exactly once. An acquired owner releases the lock before decrementing usage,
and only the final owner or waiter removes the same entry object. Independent
keys may progress concurrently.

All entries and counts are process-local and restart empty. They are neither
durable operation identity nor evidence that work succeeded. The registry
does not spawn a worker, retain completed keys, authorize replay, or convert an
unknown outcome into permission to retry.

## Consumer boundary

Gateway consumers retain their behavior-specific policy:

- `gateway.routing.gateway-operations` selects `ConversationRef` and owns the
  finite per-Conversation operation lane plus its retryable capacity error;
- `gateway.projection.request-correlation` selects `RequestRef` and owns local
  response-contender serialization inside the already-admitted finite
  Conversation-operation lane;
- `gateway.delivery.coordination` selects `ConversationRef` and owns FIFO
  destination execution, including the finite pending reservation that bounds
  its otherwise independently unbounded registry, retry, cancellation, and
  receipts; and
- `gateway.delivery.proactive-delivery` selects the exact already-validated
  JSON delivery ID and owns its finite ingress response and staging order.

Those consumers construct separate registries. This leaf does not combine
their namespaces or create a process-global capacity authority.

## Physical owner and import boundary

The target implementation is `src/imagent/gateway/concurrency.py`. It imports
only the Python standard library and is an internal Gateway seam, not a public
SDK facade. `imagent.gateway` does not re-export its names. The historical
`imagent.keyed_locks` module is removed without an alias or compatibility shim,
so one implementation and one canonical internal import path remain.

## Authority

- [Architecture](../../../ARCHITECTURE.md)
- [ADR 0006](../../../decisions/0006-core-admission-and-policy-ownership.md)
- [ADR 0008](../../../decisions/0008-interactive-request-routing.md)
- [ADR 0010](../../../decisions/0010-capability-driven-delivery-coordination.md)
