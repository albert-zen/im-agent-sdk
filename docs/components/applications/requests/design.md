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

During the physical rollout, `ApplicationRef` and `ThreadRef` still live in the
historical Application-contract portion of `contracts/model.py`. That broader
contract also consumes request types through the Application Port, so declaring
both target component edges now would create a cycle and would misrepresent the
unfinished split. The component map records this resource-identity reference as
an explicit structural gap until the Application contract is separated at its
accepted target boundary; it is not a second request owner.

Current public contracts are in `imagent.contracts`; target exports are
`imagent.applications.requests`, implemented in
`src/imagent/applications/requests.py`.

## State, recovery, and structure

The values themselves are immutable and non-authoritative. A stale result is
valid only when native response authority is provably unusable; a reset with no
authoritative pending-request snapshot must not manufacture an open request.
Gateway may retain a bounded delivered response shape, but never prompt text,
answers, permission policy, or a second request state machine.

Before this slice, the implementation was split across
`schemas/v1/{events,operations,resources}.schema.json` and
`src/imagent/contracts/{errors.py,model.py,request_validation.py,operations.py}`.
This slice makes the request-owned values, errors, response values, and
validators authoritative at `src/imagent/applications/requests.py` while the
historical modules retain only their remaining owners: Application input
outcome errors, shared non-request model values, Gateway route correlation, and
Gateway operations. Current tests are `tests/test_contracts.py` and
`tests/applications/adapters/appserver/test_requests.py`; the retained Gateway
request-correlation/presenter integration remains in
`tests/test_appserver_requests.py`. Focused ownership evidence moves to
`tests/applications/test_requests.py` while other affected integration tests
remain in place. The versioned schemas remain deliberate cross-owner contracts, and
the temporary resource-identity import remains visible in the component gap
above rather than being disguised as the final dependency graph.

The mechanical move preserves dataclass fields, discriminants, public aliases,
bounded choice/question and text validation, request application scope, and
first-writer/stale error behavior. It does not move Gateway request-correlation
records or policy, persistence, presentation, adapters, product commands, raw
native events, or execution/recovery behavior.

## Authority

- [Architecture](../../../ARCHITECTURE.md)
- [Application adapter design](../../application-adapters/design.md)
- [ADR 0008](../../../decisions/0008-interactive-request-routing.md)
