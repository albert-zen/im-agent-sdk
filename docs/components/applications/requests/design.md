# Application requests design

Component ID: `applications.requests`

Parent: `applications`

## Purpose and ownership

This leaf normalizes native interactive request, response, and resolution facts
without taking request authority. It owns `RequestRef`, approval/user-input
request and response shapes, resolution values, bounded choice/question limits,
request errors, and validators/shape derivation.

It does not own Channel/Controller presentation, destination authorization,
approval or Full Access policy, request persistence, or a fallback native
response. The native Application is first-writer authority; Gateway holds only
minimal per-destination routing correlation after successful delivery.

## Inputs, outputs, and dependencies

Adapters turn evidenced native request facts into immutable canonical values
and receive a validated typed response for the one common native mutation.
This leaf depends on Interaction operation primitives and Application event
identity, not Gateway implementation. Request IDs are scoped by
`ApplicationRef`; a reused native ID after transport reset must be epoch-scoped
by the adapter rather than merged by SDK inference.

Current public contracts are in `imagent.contracts`; target exports are
`imagent.applications.requests`, implemented in
`src/imagent/applications/requests.py`.

## State, recovery, and structure

The values themselves are immutable and non-authoritative. A stale result is
valid only when native response authority is provably unusable; a reset with no
authoritative pending-request snapshot must not manufacture an open request.
Gateway may retain a bounded delivered response shape, but never prompt text,
answers, permission policy, or a second request state machine.

Current code is split across `schemas/v1/{events,operations,resources}.schema.json`,
`src/imagent/contracts/{errors.py,model.py,request_validation.py,operations.py}`.
Current tests are `tests/test_contracts.py` and `tests/test_appserver_requests.py`;
the target is `tests/applications/test_requests.py`. That spread is the
declared structural gap.

## Authority

- [Architecture](../../../ARCHITECTURE.md)
- [Application adapter design](../../application-adapters/design.md)
- [ADR 0008](../../../decisions/0008-interactive-request-routing.md)
