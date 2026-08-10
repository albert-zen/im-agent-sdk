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
This leaf depends on Interaction operation primitives and shared Application
resource identity, not Gateway implementation. Request IDs are scoped by
`ApplicationRef`; a reused native ID after transport reset must be epoch-scoped
by the adapter rather than merged by SDK inference.

`ApplicationRef` and `TurnRef` are owned by the Applications contract leaf.
Every opened request and resolution carries the exact `TurnRef`; validators
require its Project ancestry to belong to the request's Application. This
owner imports those identities only for typed validation/signatures. The
canonical request values and validators are exported only from
`imagent.applications.requests`, implemented in
`src/imagent/applications/requests.py`. The package root and
`imagent.contracts` do not expose this request family; Gateway route
correlation remains a separate Gateway-owned contract.

## State, recovery, and structure

The values themselves are immutable and non-authoritative. A stale result is
valid only when native response authority is provably unusable; a reset with no
authoritative pending-request snapshot must not manufacture an open request.
Gateway may retain a bounded delivered response shape, but never prompt text,
answers, permission policy, or a second request state machine.

User-input response admission is finite and matches the delivered request
shape vocabulary: at most 32 question IDs, at most 64 answers per question,
and at most 4,096 characters in one answer. A delivered question cannot claim
more than 64 answers. These limits are enforced before Gateway copies,
fingerprints, reserves, or routes the response and are identical in the Python
validator and v1 schemas. Minimum and maximum answer cardinalities are exact
non-Boolean integers in Python, matching the schema integer contract.

The implementation is the request portions of
`schemas/v1/{events,operations,resources}.schema.json` plus
`src/imagent/applications/requests.py`; historical aggregate model and
validation modules no longer define this request contract. Gateway route
correlation and Gateway operations retain their own owners. Current tests are
`tests/interaction/test_contracts.py` and
`tests/applications/adapters/appserver/test_requests.py`; the retained Gateway
request-correlation/presenter integration remains in
`tests/applications/adapters/appserver/test_gateway_request_integration.py`. Focused ownership evidence moves to
`tests/applications/test_requests.py` while other affected integration tests
remain in place. The versioned schemas remain deliberate cross-owner
contracts.

The request surface preserves discriminants, public aliases, bounded
choice/question and text validation, exact Turn scope, and first-writer/stale
error behavior. It does not move Gateway request-correlation
records or policy, persistence, presentation, adapters, product commands, raw
native events, or execution/recovery behavior.

## Authority

- [Architecture](../../../ARCHITECTURE.md)
- [Application adapter design](../../application-adapters/design.md)
- [ADR 0008](../../../decisions/0008-interactive-request-routing.md)
- [ADR 0016](../../../decisions/0016-uniform-workspace-and-consumer-actions.md)
