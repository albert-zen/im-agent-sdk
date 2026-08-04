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

It does not own repository Protocols or conflict types, state values, routing/delivery policy,
message or artifact content, a retry scheduler, SQLite transactions, or a
durable recovery promise.

## Conversation binding semantics

`InMemoryBindingRepository` stores at most one current binding for each stable
`ConversationRef`. `put` validates the complete passive binding value while
holding one process-local lock, compares an optional expected revision, and
stores a freshly timestamped record whose revision is exactly one greater than
the current value. `delete` performs the same expected-revision comparison
before removal. A stale comparison raises the repository-contract-owned
`BindingConflict` without changing state.

The implementation does not coordinate projection routes, start or observe an
Application Thread, infer a retry, or retain history. Gateway remains the sole
owner of the `foreground_only` prepare-route/binding-CAS orchestration.

## Projection route and reply-correlation semantics

`InMemoryProjectionRouteRepository` implements the process-local projection
route Port under one lock. Route identity is the immutable combination of
stable route ID, Thread, and Conversation: reusing either endpoint mapping
with a conflicting stable ID fails explicitly. Additive put and per-Thread
replacement both preserve an existing omitted checkpoint and reject a caller
that attempts to replace an explicit checkpoint outside the dedicated
compare-and-swap operation.

Checkpoint advance matches the stable route ID and expected opaque Agent item
ID before storing the new item ID and boundary time. It never sorts item IDs
or treats time as replay identity. A missing route and a stale checkpoint are
distinct explicit failures.

Turn reply correlations are create-only for the `(ThreadRef, turn_id)` key.
An identical repeat is idempotent; a different Conversation, client message,
reply ID, or other immutable fact raises the repository-contract conflict.
Deletion either names one exact key or supplies at least one explicit bounded
selector. The repository stores no message content or Turn status.

The repository does not select `foreground_only`, resolve active destinations,
run an Application observation worker, deliver to a Channel, or advance an
idempotency claim. Those semantics remain in the Gateway routing/projection
owners and consume this passive repository Port.

## Request-correlation semantics

`InMemoryRequestCorrelationRepository` is the process-local implementation of
the request-correlation repository Port. It stores only the bounded typed
bridge record for each delivered destination: stable Application/request,
Thread/Turn, Conversation, delivery, supported response shape, state, and
timestamps. It never stores a prompt, answer, permission, transcript item, or
native request authority.

Writes validate the complete record while holding one process-local lock.
Stable correlation and destination identity are immutable; a conflicting
reuse fails with `RequestCorrelationConflict`. A repeated write preserves the
accepted monotonic request state, and a late destination inherits an existing
request-wide terminal state instead of reopening `open`. State transitions
apply to every correlation for one request under the same lock, require the
caller-supplied expected states, reject regressions, and treat an identical
state as idempotent. Deletion requires at least one explicit request, Thread,
Conversation, or retention selector.

The implementation does not reconcile native request truth, execute a
response, select a destination, or create a retry job. Those semantics remain
in the Gateway request-correlation and delivery owners. Durable SQLite
transactions and schema are owned by `gateway.persistence.sqlite`; this leaf
shares only pure request policy from
`gateway.projection.request-correlation`.

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

The request-correlation implementation lives in
`src/imagent/gateway/persistence/memory.py`. Pure request identity and
monotonic-transition policy is shared from
`src/imagent/gateway/projection/request_correlation.py`; SQLite SQL, schema,
and row conversion are owned by their focused persistence leaves. No
historical mixed module or compatibility implementation remains.

## Authority

- [Vision](../../../../VISION.md)
- [Architecture](../../../../ARCHITECTURE.md)
- [Persistence design](../../../persistence/design.md)
- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0009](../../../../decisions/0009-proactive-delivery-routing.md)
