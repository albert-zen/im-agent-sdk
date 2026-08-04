# Gateway repository contracts design

Component ID: `gateway.persistence.repository-contracts`

Parent: `gateway.persistence`

## Purpose

This leaf defines the typed repository Ports and explicit conflict outcomes
used by Gateway bridge-state implementations. Repository contracts describe
atomic mutation and fencing requirements without selecting memory, SQLite, or
another deployment implementation.

## Ownership

This leaf owns the complete repository Port and conflict family:

- `BindingRepository` and `ProjectionRouteRepository` for Conversation
  bindings, projection routes, checkpoints, and Turn reply correlations;
- `IdempotencyRepository` and `IdempotencyClaimStatus` for fenced claim
  acquisition and terminal transitions;
- `RequestCorrelationRepository` and its explicit transition conflict;
- `DeliverySubmissionRepository` and its typed capacity/conflict outcomes; and
- `ProjectionCheckpointConflict`, `ProjectionRouteConflict`,
  `RequestCorrelationConflict`, `TurnReplyCorrelationConflict`,
  `DeliverySubmissionConflict`, and `DeliverySubmissionCapacityError`.

`BindingConflict` remains the existing conflict owner in this leaf. The
historical `imagent.adapters` module is only an exact compatibility facade for
these names; it contains no second Protocol, enum, or exception definition.

It does not own passive record values, SQLite schema or transactions,
process-local storage, Gateway routing policy, message content, transcript
truth, or retry work.

## Binding contract

`BindingConflict` is the common explicit outcome when an implementation cannot
satisfy an expected Conversation binding revision. Both process-local and
SQLite implementations raise the exact same type. The conflict carries no
authority to retry, replace a later binding, or infer Application state.

The `BindingRepository` Port now lives beside `BindingConflict` in this leaf.
The move is mechanical: every method signature, annotation, default, and
runtime type-hint result remains unchanged. Memory and SQLite implementations
continue to raise the same conflict object and enforce the same expected
revision fence.

## Dependencies and recovery

Repository contracts depend only on typed Interaction/Application references
and passive Gateway state. Implementations preserve stable identity, expected
revision/owner fencing, create-only correlations, and checkpoint compare-and-
swap. Storage failures and conflicts remain visible to Gateway; the contract
does not manufacture recovery or native side-effect authority.

Request-correlation transitions combine two independent guards. The caller's
`expected_states` remains the atomic compare-and-swap fence, while the
repository also enforces the accepted monotonic state graph:

```text
open      -> responded | stale | resolved
responded -> stale | resolved
stale     -> resolved
resolved  -> (terminal)
```

Repeating the current state is idempotent. Naming a current state in
`expected_states` never authorizes a backward target, so a direct repository
caller cannot revive bridge response authority after it became stale or
resolved.

## Structure

```text
src/imagent/gateway/persistence/repository_contracts.py
tests/gateway/persistence/test_repository_contracts.py
```

The focused test module proves clean-process importability, exact
`imagent.adapters` and `imagent.gateway.persistence` object identity, absence
of moved definitions in the compatibility facade, Protocol signatures and
runtime `get_type_hints`, enum values, and conflict identity. Memory, SQLite,
and reusable conformance tests continue to prove the behavior consumed by the
Ports.

## Authority

- [Vision](../../../../VISION.md)
- [Architecture](../../../../ARCHITECTURE.md)
- [Persistence design](../../../persistence/design.md)
- [Ports design](../../../ports/design.md)
- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0009](../../../../decisions/0009-proactive-delivery-routing.md)
- [ADR 0011](../../../../decisions/0011-durable-inbound-admission-before-media.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
