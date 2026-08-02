# Accepted architecture decisions

These ADRs record cross-component choices that completed design review.
Component-local behavior belongs in the applicable component design.
Unaccepted directions belong in `docs/ROADMAP.md`.

| ADR | Status | Scope |
|---|---|---|
| [0001](0001-contract-and-resource-foundations.md) | Accepted | language-neutral contracts, project modes, identity, deletion |
| [0002](0002-design-authority-and-control-boundaries.md) | Accepted | design-first authority, typed Operations, optional Controllers |
| [0003](0003-attachment-sources-and-trust.md) | Accepted | explicit attachment source and filesystem/network trust |
| [0004](0004-event-fanout-and-recovery.md) | Accepted | fan-out, Turn lifecycle, event ordering, authoritative recovery |
| [0005](0005-input-activation-and-projection.md) | Accepted | binding, native activation, and output projection separation |
| [0006](0006-core-admission-and-policy-ownership.md) | Accepted | evidence threshold and Core/capability/adapter/consumer classification |
| [0007](0007-projection-lifecycle-and-delivery-boundaries.md) | Accepted | per-route checkpoints, Turn reply correlation, bootstrap order, and failure domains |
| [0008](0008-interactive-request-routing.md) | Accepted | typed interactive requests, destination-safe response correlation, and reconnect honesty |
| [0009](0009-proactive-delivery-routing.md) | Accepted | scoped proactive delivery, pinned route snapshots, and durable idempotent outcomes |
| [0010](0010-capability-driven-delivery-coordination.md) | Accepted | pure capability-driven planning and bounded destination-ordered delivery |
| [0011](0011-durable-inbound-admission-before-media.md) | Accepted | fenced durable inbound admission before Channel media preparation |
| [0012](0012-input-continuation-and-reply-correlation.md) | Accepted | default continuation preference, explicit input results, and immutable Turn reply correlation |
| [0013](0013-bounded-application-event-admission.md) | Accepted | bounded Application event admission, explicit gaps, and authoritative recovery |
| [0014](0014-read-only-diagnostics-surface.md) | Accepted | stable redacted process-local diagnostics and consumer observability boundary |
| [0015](0015-typed-extension-seams-and-composition.md) | Accepted | typed pipeline positions, stage-specific failure/replay semantics, and grouped composition |

Reopening an accepted decision requires updating this index, the ADR, affected
global/component docs, contracts/tests, and AgentKit mapping when paths or
ownership change.
