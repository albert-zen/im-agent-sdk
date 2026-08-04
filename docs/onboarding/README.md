# Neutral reference consumer

This guide is the shortest path from the public IM Agent SDK contracts to one
running consumer. The executable sample in
[`examples/reference_consumer`](../../examples/reference_consumer/) is
deliberately neutral: it supplies a deterministic local Channel and Agent
Application so the Gateway composition can be exercised without product
credentials or a native service.

The sample is an onboarding and contract demonstration, not a replacement for
a native Agent Application or a production transport. Its bounded Application
state is authoritative only for the lifetime of that process-local demo
Application; the SDK stores only the bridge bindings, projection routes,
idempotency records, and rebuildable projection checkpoints supplied by its
composition.

The demonstrated stop/start recovery reuses the same Application object and
the same Gateway repository objects. Production recovery requires durable
Application history and durable Gateway repositories; it must not be inferred
from this in-memory example.

Read the guides in this order:

1. [Interaction](interaction.md) — normalize Channel input and compose the
   local command registry.
2. [Gateway](gateway.md) — assemble the graph and select output routing.
3. [Applications](applications.md) — implement the typed native boundary.
4. [Production checklist](production-checklist.md) — replace the local seams
   safely.
5. [Troubleshooting](troubleshooting.md) — inspect routing, recovery, and
   shutdown failures.

The complete flow is runnable with:

```sh
PYTHONPATH=src:. uv run python -m examples.reference_consumer.main
```

Its real test is
[`tests/gateway/test_reference_consumer.py`](../../tests/gateway/test_reference_consumer.py).
That test executes the example through the public composition and checks
command handling, two Conversations on one Thread, foreground switching,
one Application observer per Thread, diagnostics, restart recovery, and
graceful shutdown.
