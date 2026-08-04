# Applications diagnostics design

Component ID: `applications.diagnostics`

Parent: `applications`

## Purpose and ownership

This leaf owns the immutable, redacted diagnostic vocabulary emitted by an
Application adapter and its adapter-owned A1 presentation runtimes:

- `ApplicationDiagnosticFacts` and the optional `ApplicationDiagnosticsProvider`
  seam (`DiagnosticsProvider` is its preserved historical alias);
- fixed live-presentation failure/fact types; and
- fixed artifact-materialization failure/fact types.

These are bounded process-local observations. They contain no native
Application client, callback, I/O, Thread/Turn authority, content, path,
locator, attachment, or exception detail. The App Server mutable counters and
connection snapshot state remain owned by
`applications.adapters.appserver.diagnostics`; this leaf only defines the
typed values that an adapter may publish.

It does not own Interaction connection/queue definitions, Gateway snapshot
aggregation, Gateway extension diagnostics, native transport state, or
consumer observability policy.

## Typed boundary and dependency direction

`imagent.applications.diagnostics` imports only the dependency-neutral
Interaction diagnostic contracts and standard-library value machinery. It
does not import `imagent.gateway`, `imagent.diagnostics`, a native SDK, or an
adapter implementation. Application adapters and A1 runtimes import these
canonical values directly; Gateway may consume them through its aggregation
path.

The fixed failure vocabularies and dataclass constructor/validation behavior
are part of the public contract. Absence remains meaningful: an adapter with
no configured presenter or materializer returns `None` for that nested fact
instead of fabricating configured capability.

## Transition surface

`imagent.diagnostics` remains a truthful mixed transition module while the
Gateway definitions and aggregation await #273. It re-exports these exact
Application objects by identity and retains no duplicate or lazy compatibility
implementation. The transition module is still a split candidate because it
also contains Gateway-owned fact types and aggregation.

## State and recovery

The values are snapshots of process-lifetime counters. Application adapters
retain mutable runtime state in their concrete adapter/runtime owners and
publish a fresh immutable value on read. Presentation and materialization
failure categories, bounds, cancellation-overrun counts, redaction, and
absence semantics remain unchanged by this ownership move. Native history,
recovery, checkpoints, artifact trust, and cleanup remain outside this leaf.

## Authority

- [Applications navigation](../README.md)
- [Application live-activity design](../presentation/live-activity/design.md)
- [Artifact materialization design](../presentation/artifact-materialization/design.md)
- [App Server diagnostics design](../adapters/appserver/diagnostics/design.md)
- [Interaction diagnostics design](../../interaction/diagnostics/design.md)
- [ADR 0014](../../../decisions/0014-read-only-diagnostics-surface.md)
- [ADR 0015](../../../decisions/0015-typed-extension-seams-and-composition.md)
