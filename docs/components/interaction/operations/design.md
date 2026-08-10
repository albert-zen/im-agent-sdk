# Interaction operations design

Component ID: `interaction.operations`

Parent: `interaction`

## Purpose

This leaf preserves the product-neutral separation between content-bearing
Messages and typed control intent. It provides the common result, validation,
and error vocabulary used by owner-specific Application and Gateway
Operations without owning either mutation implementation.

## Ownership

This leaf owns:

- `OperationResultStatus`;
- `ContractError` and `ContractViolation`;
- the stable `OperationErrorCode` vocabulary;
- `operation_error` and shared identifier validation such as
  `require_identifier`;
- the common status and error primitives used by owner-specific validators.

It does not own:

- Application operation variants or native mutation behavior;
- Gateway binding, projection-route, request-routing, or orchestration
  variants;
- Controller command grammar, registration, presentation, or product
  handlers;
- retry policy, side-effect fencing, or an operation journal;
- product-only commands such as provider credits, workspace UX, or native
  calls without cross-Application evidence.

Application operation variants belong to `applications.operations`. Gateway
operation variants belong to the applicable `gateway.routing` leaves. Those
components depend on this common vocabulary; this leaf does not depend on
their implementations.

## Inputs and outputs

The input is an owner-specific typed operation carrying a stable
`operationId`, its discriminant, and behavior-specific arguments. The output
is an owner-specific typed success or failure result carrying the matching
identity and discriminant. This leaf supplies only the shared status, stable
error projection, and validation primitives. The owning Application or
Gateway operation leaf validates its concrete identity/discriminant pairing.

A Controller may translate a bounded command invocation into a typed action.
That composition does not turn command text into a common operation and does
not grant the Controller access to Gateway or Application implementations.

## Failure and replay boundary

Stable error codes distinguish invalid, unsupported, missing resource,
missing ordinary-input binding, stale ordinary-input binding, conflicting,
adapter-failed, capacity-exhausted, unauthorized-destination, and
request-lifecycle outcomes. `capacity_exhausted` is retryable only because it
is emitted before the owning operation's side-effect boundary. Native
exception types may be projected into that public vocabulary, but an error
result does not otherwise invent support or retry permission.

Owner-specific exceptions may carry one stable `OperationErrorCode` through a
private common mapped-error base. The base contains no Application or Gateway
state and exists only so `operation_error` can preserve the code without a
reverse import. Applications-owned request exceptions and the Gateway-owned
`MissingBindingError`/`StaleBindingError` depend on that common base;
`interaction.operations` never imports those exceptions or interprets request
lifecycle or binding truth.

`operationId` is correlation identity, not a universal exactly-once claim.
The owner of each concrete operation defines whether it is read-only,
idempotent, fenced before side effects, or outcome-unknown. A consumer must
not repeat a mutation merely because it received a common failure status.
Product Controller handlers separately document replay, cancellation,
known-pre-side-effect failure, and ambiguous outcome behavior.

This leaf is stateless. It persists no operation, claim, receipt, or native
result.

## Contract and dependency boundary

The relevant language-neutral values currently live in
`schemas/v1/common.schema.json` and `schemas/v1/operations.schema.json`.
Identifiers are non-empty and at most 512 characters. Free-form Metadata is
not used for common operation arguments or success values.

The leaf may depend on the foundational Interaction message/reference value
vocabulary only. It imports no Controller, Gateway, Application adapter,
repository, or native client.

## Current and target structure

The common operation values now have one implementation in
`src/imagent/interaction/operations.py`. Mixed contract modules import the
owning values, while concrete Gateway/Application variants and their
validators remain declared split candidates. The language-neutral operation
schema remains a deliberate versioned union across those owners.

The mechanical target is:

```text
src/imagent/interaction/operations.py
tests/interaction/test_operations.py
```

The deliberate `imagent.contracts` public facade re-exports the exact target
objects; internal runtime imports use the owning leaf. Concrete operation
variants remain in their current files until their owning layer moves. No
parallel implementation or indefinite internal compatibility path remains.

## Authority

- [Vision](../../../VISION.md)
- [Architecture](../../../ARCHITECTURE.md)
- [Common protocol](../../contracts/protocol.md#typed-operations)
- [ADR 0001](../../../decisions/0001-contract-and-resource-foundations.md)
- [ADR 0002](../../../decisions/0002-design-authority-and-control-boundaries.md)
