# Application capabilities design

Component ID: `applications.capabilities`

Parent: `applications`

## Purpose and ownership

This leaf declares immutable, evidenced native support. It owns
`ApplicationCapabilities`, `ProjectCapabilities`, `ThreadCapabilities`,
`RuntimeCapabilities`, `SupportLevel`, `ThreadDeletionCapability`, and
`validate_application_capabilities`.

It does not own a fallback, product configuration, a synthetic capability, or
native behavior. A consumer must not convert an absent capability into a
pretended native operation; unsupported behavior remains an explicit outcome.

## Inputs, outputs, and dependencies

Concrete adapter evidence supplies facts; this leaf outputs one bounded,
immutable capability declaration. It depends only on typed Interaction media
source kinds, because attachment support must state the native trust/encoding
boundary honestly. It has no Gateway dependency.

Current public values are exported from `imagent.contracts`; the target owner
is `imagent.applications.capabilities`. The target code is
`src/imagent/applications/capabilities.py`, while the v1 capabilities schema
remains language-neutral.

## State, recovery, and structure

Capabilities are deployment/static declarations and must remain truthful after
restart. They are not a mutable record of an Application runtime, replay
cursor, provider setting, or product retry policy.

Current code is `schemas/v1/capabilities.schema.json` plus
`src/imagent/contracts/{model.py,validators.py}`. Current tests are
`tests/test_contracts.py` and `tests/test_adapter_contracts.py`; the target is
`tests/applications/test_capabilities.py`. The declared gap is that capability
values remain embedded in the cross-owner contract model during the mechanical
rollout.

## Authority

- [Vision](../../../VISION.md)
- [Architecture](../../../ARCHITECTURE.md)
- [Common protocol](../../contracts/protocol.md)
- [ADR 0001](../../../decisions/0001-contract-and-resource-foundations.md)
- [ADR 0003](../../../decisions/0003-attachment-sources-and-trust.md)
