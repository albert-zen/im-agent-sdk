# Gateway in-memory persistence design

Component ID: `gateway.persistence.memory`

Parent: `gateway.persistence`

## Purpose

This leaf provides process-local reference implementations of Gateway
repository contracts. They are useful for tests and explicitly ephemeral
deployments; every process starts empty, so they never claim restart
durability.

## Ownership

This leaf owns in-memory implementations for Conversation bindings,
projection routes, request correlations, and proactive delivery submissions.
Each implementation preserves the same conflict, fencing, compare-and-swap,
and immutable-identity rules as its repository contract.

It does not own repository Protocols, state values, routing/delivery policy,
message or artifact content, a retry scheduler, SQLite transactions, or a
durable recovery promise.

## Delivery submission semantics

The first focused extraction moves `InMemoryDeliverySubmissionRepository`
from proactive orchestration into `src/imagent/gateway/persistence/memory.py`.
Reservation validates the full record under one process-local lock. A stable
submission ID enforces immutable root origin, principal, target, and payload
identity plus the order-independent mapping from every destination delivery ID
to its complete route snapshot. An identical repeat returns the existing
record, while any root, destination-ID, or snapshot mismatch raises
`DeliverySubmissionConflict`. Mutable destination state, receipt, error, and
timestamps do not participate in reservation identity.

Destination mutation is atomic under the same lock and requires the expected
prior state. Missing submissions, missing destinations, and stale expected
state remain explicit. The repository stores fingerprints, snapshots,
outcomes, receipts, and timestamps only; it never stores delivery content,
inline bytes, local paths, credentials, or a replayable work item.

## State and recovery

Restart discards all process-local state. The current repository has no finite
record-capacity or retention setting and Gateway uses it by default when a
submission repository is absent. That pre-existing gap violates the target
finite-capacity rule and requires a separate behavior/configuration slice; this
mechanical extraction does not disguise caller-created growth as a bound.
Gateway therefore uses this implementation only when
non-durable bridge state is an explicit deployment choice; SQLite remains the
durable implementation. An `in_flight` or `unknown` outcome is never silently
converted into permission to resend within one process.

## Structure

```text
src/imagent/gateway/persistence/memory.py
tests/gateway/persistence/test_memory.py
```

The other three in-memory repositories remain at their historical owners
until separate mechanical slices move them into the same leaf. No
compatibility implementation is duplicated.

## Authority

- [Vision](../../../../VISION.md)
- [Architecture](../../../../ARCHITECTURE.md)
- [Persistence design](../../../persistence/design.md)
- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0009](../../../../decisions/0009-proactive-delivery-routing.md)
