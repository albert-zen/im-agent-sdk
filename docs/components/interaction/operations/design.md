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

Stable error codes distinguish invalid, unsupported, missing, conflicting,
adapter-failed, unauthorized-destination, and request-lifecycle outcomes.
Native exception types may be projected into that public vocabulary, but an
error result does not invent support or retry permission.

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

Common operation values are currently spread across
`src/imagent/contracts/__init__.py`, `_validation.py`, `errors.py`, `model.py`,
`operations.py`, and `validators.py`. Several of those files and the operation
schema also contain Gateway- and Application-owned variants. They are declared
split candidates; the filename does not make their contents one component.

The mechanical target is:

```text
src/imagent/interaction/operations.py
tests/interaction/test_operations.py
```

The later move must extract the common vocabulary once, relocate concrete
variants to their owning layer, update formal facades, and leave no parallel
implementation or indefinite internal compatibility path.

## Authority

- [Vision](../../../VISION.md)
- [Architecture](../../../ARCHITECTURE.md)
- [Common protocol](../../contracts/protocol.md#typed-operations)
- [ADR 0001](../../../decisions/0001-contract-and-resource-foundations.md)
- [ADR 0002](../../../decisions/0002-design-authority-and-control-boundaries.md)
