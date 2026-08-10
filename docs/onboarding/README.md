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

The default run starts from an explicit caller-managed temporary CWD and a fresh
in-memory Gateway store. It does not infer a default workspace, auto-create on
first input, or claim crash-recovery evidence. Durable restart coverage belongs
to the separate SQLite acceptance path in the v1 executable specification.

Read the guides in this order:

1. [Applications](applications.md) — implement the typed managed-resource and
   native execution boundary.
2. [Interaction](interaction.md) — normalize Channel input and compose the
   local command registry.
3. [Gateway](gateway.md) — assemble one store-backed graph and use scoped
   public actions.
4. [Production checklist](production-checklist.md) — replace the local seams
   safely.
5. [Troubleshooting](troubleshooting.md) — inspect routing, recovery, and
   shutdown failures.

The complete flow is runnable with:

```sh
PYTHONPATH=src:. uv run python -m examples.reference_consumer.main
```

The same module is included in the base wheel, so a clean-installed consumer
uses `python -m examples.reference_consumer.main` without a repository
`PYTHONPATH`.

Its real test is
[`tests/gateway/test_reference_consumer.py`](../../tests/gateway/test_reference_consumer.py).
That test executes the example through the public composition and checks
explicit Project/Thread creation, ordinary input, two Conversations on one
Thread, foreground switch and switch-back, one Application observer per
Thread, bounded redacted diagnostics, and deterministic shutdown.
