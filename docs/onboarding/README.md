# Neutral reference consumer

This guide is the shortest path from the public IM Agent SDK contracts to one
running consumer. The executable sample in
[`examples/reference_consumer`](../../examples/reference_consumer/) is
deliberately neutral: it supplies a deterministic local Channel and managed
Agent Application so the installed public Gateway composition can be exercised
without product credentials or a native service.

The sample is an onboarding and contract demonstration, not a replacement for
a native Agent Application or a production transport. Its bounded Application
state is authoritative only for the lifetime of that process-local demo
Application; the SDK stores only the bridge bindings, projection routes,
idempotency records, and rebuildable projection checkpoints supplied by its
composition.

The run starts from an explicit caller-managed temporary CWD and an explicit
SQLite store, then closes the first graph and reconstructs fresh Gateway,
Channel, registry, and SQLite store objects over the same database to prove
restart recovery. It reuses only the Application that owns Project, Thread,
and authoritative history truth. It does not infer a default workspace or
auto-create on first ordinary input; all Project/Thread creation uses scoped
public actions.

## Fast path

For a 10–20 minute installed-release walkthrough, start with the
[Quickstart](quickstart.md). It uses the one packaged reference consumer and
explains the POSIX full-vertical boundary plus Windows install/import
limitation.

Read the guides in this order:

1. [Quickstart](quickstart.md) — install the release and run the canonical
   inbound → Agent → outbound vertical.
2. [Applications](applications.md) — implement the typed managed-resource and
   native execution boundary.
3. [Interaction](interaction.md) — normalize Channel input and compose the
   local command registry.
4. [Gateway](gateway.md) — assemble one store-backed graph and use scoped
   public actions.
5. [Production checklist](production-checklist.md) — replace the local seams
   safely.
6. [Troubleshooting](troubleshooting.md) — inspect routing, recovery, and
   shutdown failures.
7. [Capability ownership matrix](capability-matrix.md) — confirm whether a
   behavior is common, adapter-specific, deliberately unsupported, or owned by
   the downstream consumer.
8. [Upgrade and rollback](upgrade-and-rollback.md) — verify the exact GitHub
   release provenance and preserve bridge-store compatibility.

## Operational runbooks

Use these focused recipes after the quickstart. They link back to the canonical
component authority and executable evidence rather than restating design:

- [Bindings](../recipes/bindings.md)
- [Restart and replay](../recipes/restart-and-replay.md)
- [Interactive requests](../recipes/interactive-requests.md)
- [Media and artifacts](../recipes/media-and-artifacts.md)
- [Proactive delivery](../recipes/proactive-delivery.md)
- [Diagnostics](../recipes/diagnostics.md)

The [Quickstart](quickstart.md) contains the authenticated GitHub Release
download/checksum commands and the canonical installed command,
`python -m examples.reference_consumer.main`. The complete vertical runs on
POSIX platforms with the required safe-descriptor flags; Windows can verify
and import the wheel, while the canonical consumer remains explicitly
unsupported there until those flags are available.

Its real test is
[`tests/gateway/test_reference_consumer.py`](../../tests/gateway/test_reference_consumer.py).
That test executes the example through the public composition and checks
explicit Project/Thread creation, ordinary input, two Conversations on one
Thread, foreground switch and switch-back, one Application observer per
Thread, bounded redacted diagnostics, SQLite restart reconstruction without
duplicate native dispatch/delivery, and deterministic shutdown.
