# Gateway logical delivery outcome observation design

Component ID: `gateway.delivery.outcome-observation`

## Purpose and ownership

Outcome observation is the ADR 0015 O2 position. It offers one bounded typed
notification after one logical Coordinator destination attempt has released
its lane/capacity and produced either a final aggregate `DeliveryReceipt` or a
fixed execution error.

It owns the `DeliveryOutcomeObserver` protocol, immutable context/outcome
values, fixed error vocabulary, bounded fact copying, finite detached runtime,
and redacted observer diagnostics. It does not own delivery result mutation,
segment retry, durable replay, checkpoint/idempotency state, cleanup order,
content storage, or a durable observer/outbox.

## Invariants

- one actual logical Coordinator attempt offers at most one notification;
- segments and internal Coordinator retries never create extra observer calls;
- later explicitly resumed destination attempts may notify again;
- preflight/authorization rejection, O1 suppression, durable replay, and
  completed projection recovery fabricate no attempt notification;
- original message/receipt facts are immutable, typed, and bounded by item and
  shared string budgets before consumer code runs;
- observer capacity, lifetime, cancellation, and overrun tracking are finite;
- observer work is detached from the resolved delivery caller and cannot
  rewrite receipt, persistence, retry, shutdown, or staged-resource cleanup;
- failure records only fixed diagnostic categories and bounded counters;
- observation is best-effort process-local and never durably replayed.

The implementation lives once at
`src/imagent/gateway/delivery/outcome_observation.py`. The Gateway delivery
package re-exports the exact public protocol and values; no historical
`imagent.delivery_outcomes` implementation/import path remains. The O2
diagnostic enum and fact values are owned by
`src/imagent/gateway/diagnostics.py` and imported directly by this runtime.

## Authority

- [Architecture](../../../../ARCHITECTURE.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)
- [Delivery navigation](../README.md)
