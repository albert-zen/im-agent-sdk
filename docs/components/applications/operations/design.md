# Application operations design

Component ID: `applications.operations`

Parent: `applications`

## Purpose and ownership

This leaf expresses the common typed control intent and results for one native
Application: Project/Thread reads and lists, managed Project creation from a
bounded CWD, explicit managed Project deletion, Thread creation/deletion,
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
typed success result or an explicit `ApplicationOperationFailed`. Consumers
request these semantics through scoped actions rather than calling an adapter
or submitting a generic operation. Operation identifiers are stable.
This leaf depends on Interaction operation primitives and consumes the canonical
request response shapes owned by `applications.requests`; it never depends on
Gateway implementation.

The canonical public values are exported only from
`imagent.applications.operations`, implemented in
`src/imagent/applications/operations.py`. The package root and
`imagent.contracts` do not expose these Application operation names. The v1
operations schema remains a shared language-neutral contract.

## State, recovery, and structure

`CreateProject`/`ProjectCreated` and `DeleteProject`/`ProjectDeleted` are common
typed variants, but only a managed adapter with evidenced native management
advertises success. Fixed/flat adapters return the ordinary typed unsupported
result and never change their configured workspace. Project deletion is a
primitive native intent and never silently clears a Conversation binding.
`CreateThread` and `ListThreads` always name a Project because there is no
Project-less resource branch. Thread creation bounds the title, context item
count, text, attachment fields, 512-character opaque handle, and canonical
metadata before native execution.
The scoped action layer takes an immutable snapshot of that validated context
before deriving its fingerprint, so later caller mutation cannot change the
native effect associated with a durable receipt.
List queries, cursors, result cardinality, and returned cursors are also
bounded at the operation boundary; a read cannot turn the scoped surface into
an unbounded native enumeration. Every returned `Page.items` value must be an
exact tuple before count or member validation, so a list-backed page cannot be
validated and later mutated beyond its bound.
`RespondRequest` delegates to the canonical bounded request-response admission
validator, preserving Python/schema parity at the native operation boundary.
Operation result validation walks history and catch-up recursively: every
nested `TurnRef` and `AgentMessage` must belong to the requested Thread rather
than merely the same Application, and neither result may contain more entries
than the requested limit.

The private repository-wired Gateway runtime executes only its operation
subset. A `DeleteProject` submission is rejected
as typed unsupported before adapter dispatch; managed deletion enters only
through principal-scoped `ApplicationActions` and B's durable native fence.

Operations do not establish an SDK Agent state machine. A native mutation that
may have begun but has no truthful result remains ambiguous; adapters do not
silently repeat it. `CreateThread` carries only shared control intent—native
option vocabularies and product UX remain a concrete adapter/consumer seam.

Current code is `schemas/v1/operations.schema.json`,
`src/imagent/applications/operations.py`; the historical
`src/imagent/contracts/{operations.py,validators.py}` modules are absent.
Gateway operation variants and validators live in their Gateway routing owner,
and request-owned response values live in `applications.requests`. This leaf
makes the Application operation/result variants and their validators
authoritative at `src/imagent/applications/operations.py`; no compatibility
Application operation exports remain in `imagent.contracts`.
Current tests are `tests/interaction/test_contracts.py`, `tests/conformance/test_adapter_contracts.py`,
and `tests/applications/adapters/appserver/test_input_integration.py`; focused ownership evidence moves to
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
- [ADR 0016](../../../decisions/0016-uniform-workspace-and-consumer-actions.md)
