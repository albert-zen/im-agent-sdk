# App Server artifact materialization design

Component ID: `applications.presentation.artifact-materialization`

Parent: `applications.presentation`

## Purpose and ownership

This separate App Server A1 leaf offers a replay-safe consumer bounded,
untrusted artifact candidates from an already normalized completed item or
terminal Turn. It owns candidate/fact types, `AppServerArtifactMaterializer`,
`ApplicationArtifactMaterialization`, finite output validation, invocation
runtime, and redacted diagnostics.

It does not own bytes, path/URL trust, filesystem access, quota, lease,
ledger, startup sweep, crash-safe cleanup, durable spool/outbox, delivery
checkpoint, or a second native subscription. Those remain consumer-owned;
candidate locators never confer authority.

## Inputs, outputs, dependencies, and exports

The App Server adapter supplies frozen `AppServerCompletedItemFacts` for each
completed native item and one `AppServerTurnTerminalFacts` after a terminal
Turn. Native Thread, Turn, item, candidate identity, kind, phase, and bounded
candidate locator facts are fixed before materializer invocation. Missing
native IDs fail closed; they are never synthesized from text, timestamps, or
locators.

The materializer returns `None` or a finite
`ApplicationArtifactMaterialization` containing validated typed
`AttachmentContent`. It cannot select message/event identity, role, Thread,
Turn, terminal status, recovery/checkpoint behavior, or destination. This leaf
depends on Interaction media, the Application contract/events leaves, the
direct canonical `applications.diagnostics` owner for its typed artifact
diagnostics, and App Server mapping. App Server adapter diagnostics remains a
separate owner of mutable native connection state. Current exact facades are
`imagent.applications` and
`imagent.applications.presentation`, both backed by
`src/imagent/applications/presentation/artifact_materialization.py`.

## State and recovery

The finite async runtime runs off the socket reader in the App Server adapter's
existing ordered live/history lane. Live duplicate suppression is a bounded
process-local window; authoritative history may reinvoke the materializer, so
the consumer must process candidate identity idempotently and preserve the
same association. A live fact, capacity, timeout, cancellation, malformed
output, or consumer failure terminates only that Thread observation with the
fixed materialization recovery gap; history read fails explicitly instead.

Facts, materialized attachments, consumer state, and cleanup work are never
persisted or replayed by SDK. O2 can report one completed logical delivery
attempt so the consumer can update its bounded ledger and release only leases
whose relevant destinations have reached the consumer's terminal rule.
Crash-safe cleanup remains the consumer's ledger/startup sweep responsibility.

## Current and target structure

The owner implementation is
`src/imagent/applications/presentation/artifact_materialization.py`, with
diagnostic facts owned by `src/imagent/applications/diagnostics.py`. Focused tests are
`tests/applications/presentation/test_artifact_materialization.py`, and the
finite `src/imagent/applications/presentation/__init__.py` facade re-exports
the named artifact contracts alongside the live-activity contracts. The
historical `appserver_artifacts.py` module is absent; this move adds no
artifact storage or new Application API semantics.

## Authority

- [Architecture](../../../../ARCHITECTURE.md)
- [Attachment/media design](../../../attachments-and-media/design.md)
- [Application adapter design](../../../application-adapters/design.md)
- [ADR 0003](../../../../decisions/0003-attachment-sources-and-trust.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)
