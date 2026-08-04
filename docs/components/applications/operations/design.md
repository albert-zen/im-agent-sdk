# Application operations design

Component ID: `applications.operations`

Parent: `applications`

## Purpose and ownership

This leaf expresses the common typed control intent and results for one native
Application: Project/Thread reads and lists, Thread creation/deletion,
history/catch-up/status reads, interruption, optional native activation, and
the native `request.respond` mutation. It owns `ApplicationOperation`,
`ApplicationOperationResult`, their closed variants and validators.

It does not own Conversation binding, projection observation, native wire
mapping, product command grammar, or one-consumer product controls. A product
command such as `/credits`, profile selection, or provider configuration is
not an `ApplicationOperation` without the cross-Application evidence required
by ADR 0006.

## Inputs, outputs, and dependencies

Gateway calls an adapter with a typed common operation; it returns the matching
typed success result or an explicit `ApplicationOperationFailed`. Consumer
Controller handlers request public operations through `ControllerActions`
rather than calling an adapter directly. Operation identifiers are stable.
This leaf depends on Interaction operation primitives and consumes the canonical
request response shapes owned by `applications.requests`; it never depends on
Gateway implementation.

The canonical public values are exported only from
`imagent.applications.operations`, implemented in
`src/imagent/applications/operations.py`. The package root and
`imagent.contracts` do not expose these Application operation names. The v1
operations schema remains a shared language-neutral contract.

## State, recovery, and structure

Operations do not establish an SDK Agent state machine. A native mutation that
may have begun but has no truthful result remains ambiguous; adapters do not
silently repeat it. `CreateThread` carries only shared control intent—native
option vocabularies and product UX remain a concrete adapter/consumer seam.

Current code is `schemas/v1/operations.schema.json`,
`src/imagent/applications/operations.py`, and historical
`src/imagent/contracts/{operations.py,validators.py}` for Gateway values only.
The historical modules retain Gateway variants and validators; request-owned
response values live in `applications.requests`. This leaf makes the
Application operation/result variants and their validators authoritative at
`src/imagent/applications/operations.py`; no compatibility Application
operation exports remain in `imagent.contracts`.
Current tests are `tests/test_contracts.py`, `tests/conformance/test_adapter_contracts.py`,
and `tests/test_appserver_input.py`; focused ownership evidence moves to
`tests/applications/test_operations.py` while affected integration tests remain
in place. The language-neutral schema remains a deliberate cross-owner union.

The mechanical move preserves every dataclass field, discriminant, validation
bound, and error projection. It does not move request response-shape
definitions, Gateway operations, native wire mapping, or any operation
execution behavior.

## Authority

- [Vision](../../../VISION.md)
- [Architecture](../../../ARCHITECTURE.md)
- [Common protocol](../../contracts/protocol.md)
- [ADR 0002](../../../decisions/0002-design-authority-and-control-boundaries.md)
- [ADR 0006](../../../decisions/0006-core-admission-and-policy-ownership.md)
- [ADR 0012](../../../decisions/0012-input-continuation-and-reply-correlation.md)
