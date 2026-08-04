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

Restart discards all process-local state. The delivery-submission repository
requires a positive finite `max_records`; Gateway supplies the immutable
`GatewayLimits.delivery_submission_max_records` value when it composes the
default. Both construction paths default to 4096 root records. Once that many
distinct root identities exist, a new reservation
raises `DeliverySubmissionCapacityError` under the repository lock before any
mutation or Channel side effect. Existing identities can still replay and
update their destination state at capacity.

Records are retained for the complete process lifetime. The repository does
not evict even terminal records: deleting accepted/rejected/partial/unknown
evidence would let the same stable delivery ID acquire again and could resend
a native side effect. `in_flight`, `retryable`, and `unknown` ambiguity is
therefore never silently discarded. This explicit fail-closed policy is the
only safe finite process-local contract without adding a second tombstone or
durable job store. Deployments that must accept a long-lived stream use the
durable SQLite repository and their own storage operations policy.

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
