# Application capabilities design

Component ID: `applications.capabilities`

Parent: `applications`

## Purpose and ownership

This leaf declares immutable, evidenced native support. It owns
`ApplicationCapabilities`, `ProjectCapabilities`, `ThreadCapabilities`,
`RuntimeCapabilities`, `SupportLevel`, `ProjectMode`,
`ThreadDeletionCapability`, `EventSequenceScope`, and
`validate_application_capabilities`.

It does not own a fallback, product configuration, a synthetic capability, or
native behavior. A consumer must not convert an absent capability into a
pretended native operation; unsupported behavior remains an explicit outcome.

All three Project modes support discovery and reading of the public workspace
resource tree. Managed mode reports the evidenced native level. Fixed/flat
mode reports a declared adapter projection for its one stable workspace
Project, while creation, deletion, and native switching remain unsupported.
This distinction exposes real execution scope without advertising native
Project management.

## Inputs, outputs, and dependencies

Concrete adapter evidence supplies facts; this leaf outputs one bounded,
immutable capability declaration. It depends only on typed Interaction media
source kinds and the common contract error value used for explicit validation,
because attachment support must state the native trust/encoding boundary
honestly. `ProjectMode` and `EventSequenceScope` are nested capability
discriminants, so this leaf owns their nominal Python identities rather than
depending on the legacy aggregate contract module. It has no Gateway
dependency.

The canonical public values are exported only from
`imagent.applications.capabilities`; the v1 capabilities schema remains
language-neutral. `imagent.contracts` and the package root do not expose this
Applications capability family, and the owner module does not load concrete
adapters or Gateway implementation as an import side effect.

## State, recovery, and structure

Capabilities are deployment/static declarations and must remain truthful after
restart. They are not a mutable record of an Application runtime, replay
cursor, provider setting, or product retry policy.

The implementation is `schemas/v1/capabilities.schema.json` plus
`src/imagent/applications/capabilities.py`. Focused ownership evidence is
`tests/applications/test_capabilities.py`, alongside adapter conformance and
negative clean-process facade/import-order checks. The schema and capability
semantics are unchanged by this mechanical move.

## Authority

- [Vision](../../../VISION.md)
- [Architecture](../../../ARCHITECTURE.md)
- [Common protocol](../../contracts/protocol.md)
- [ADR 0001](../../../decisions/0001-contract-and-resource-foundations.md)
- [ADR 0003](../../../decisions/0003-attachment-sources-and-trust.md)
- [ADR 0016](../../../decisions/0016-uniform-workspace-and-consumer-actions.md)
