# Interaction diagnostics contracts design

## Responsibility

This leaf owns the dependency-neutral, redacted diagnostic vocabulary shared
by the Interaction Channel edge and higher runtime layers. Its sole Python
implementation owner is `src/imagent/interaction/diagnostics.py`.

The owner defines exactly:

- `ConnectionDiagnosticState`;
- `DiagnosticFailureCode`;
- `QueueDiagnosticName`;
- `QueueDiagnosticFacts`; and
- `ConnectionDiagnosticFacts`.

These values describe bounded process-local observations only. They contain
no Thread, Conversation, request, route, native message, content, exception
text, credential, endpoint, path, or attachment identity. They are frozen,
slotted values with the existing fixed vocabularies, bounds, and validation
messages. This issue changes physical ownership and import paths only; it does
not change signatures, positional construction, validation behavior, schema
versioning, aggregation, or recovery semantics.

## Dependency direction

`imagent.interaction.diagnostics` imports only Python standard-library value
machinery. It imports neither Applications, Gateway, Channels, native
adapters, callbacks, persistence, or I/O. `interaction.channels.diagnostics`
may depend on this leaf because it adds the Channel-scoped value and provider
protocol; no lower Interaction leaf depends on a higher runtime layer.

The native implementation at
`src/imagent/interaction/channels/adapters/diagnostics.py` remains the owner
of native adapter state/collection helpers. It consumes these canonical
contracts but does not redefine them.

## Transition facade

`imagent.diagnostics` remains an explicit stable transition facade after the
Application and Gateway owner moves. It imports and re-exports the exact five
canonical objects from this
leaf and the exact Channel objects from
`imagent.interaction.channels.diagnostics`; it contains no duplicate
definitions for those moved objects and no lazy `__getattr__`. The Gateway
fact types and aggregation are canonical in `imagent.gateway.diagnostics` and
the facade is not a second Interaction owner.

The accepted [ADR 0014](../../../decisions/0014-read-only-diagnostics-surface.md)
and [ADR 0015](../../../decisions/0015-typed-extension-seams-and-composition.md)
remain authoritative.
