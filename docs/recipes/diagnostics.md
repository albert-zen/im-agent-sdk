# Recipe: export bounded diagnostics

Poll the synchronous snapshot and translate its fixed facts into the
consumer's observability system. Do not treat it as authoritative Agent health
or enrich it with identifiers and exception text.

## Prerequisites

- A running `Gateway`; cold or stopped action surfaces intentionally fail.
- Optional Application/Channel diagnostic providers that perform no native
  I/O and return their exact bounded fact types.
- Consumer-owned polling, metrics, labels, alerting, access control, and
  operator presentation.

## Public API path

```python
async with gateway:
    snapshot = gateway.diagnostics()  # synchronous and side-effect free
    assert snapshot.authoritative is False
    assert snapshot.schema_version == 8

    observability.publish(
        projection_active=snapshot.projections.running_count,
        projection_degraded=snapshot.projections.degraded_count,
        startup_depth=snapshot.gateway.startup_queue.depth,
    )
```

Use the typed attributes directly and version the consumer translation against
`schema_version`. Provider absence is meaningful; do not turn a missing
connection model into fabricated unhealthy state.

## Owner and authority

Interaction owns common connection/queue facts, Applications and Channels own
their optional provider facts, and Gateway owns aggregation. The consumer owns
export and operations UX. Native Applications remain authoritative for Thread,
Turn, request, transcript, and execution truth. See
[Gateway diagnostics](../components/gateway/diagnostics/design.md),
[Interaction diagnostics](../components/interaction/diagnostics/design.md),
[Applications diagnostics](../components/applications/diagnostics/design.md),
and [ADR 0014](../decisions/0014-read-only-diagnostics-surface.md).

## Typed failure modes

- Provider absence: omit the optional facts; it is not itself failure.
- Provider exception, invalid shape, or identity mismatch: aggregation fails
  closed to fixed identity-only or empty facts.
- Unknown/oversized projection state or gap: normalize to fixed
  degraded/`other` evidence.
- Queue overflow, reconnect, presentation/materialization, I1/I2/O1/O2, and
  lifecycle failures appear only as fixed enums and bounded counters.
- Calling `diagnostics()` outside a running Gateway raises the public lifecycle
  error; construct/start a fresh Gateway rather than retaining a stopped one.

Never branch on provider error text. It is intentionally excluded and may
change without becoming a public contract.

## Diagnostics

The snapshot is itself the diagnostic surface: synchronous, immutable,
process-local, redacted, and non-authoritative. It contains no Thread,
Conversation, request, route, content, credential, endpoint, path, attachment,
native message identity, or free-form exception. Correlate it only with
consumer-owned deployment identity and coarse time windows.

## Executable evidence

- `PYTHONPATH=src python -m unittest tests.gateway.test_diagnostics -v`
- `PYTHONPATH=src python -m unittest tests.interaction.test_diagnostics -v`
- `PYTHONPATH=src python -m unittest tests.interaction.channels.test_diagnostics -v`
- `PYTHONPATH=src python -m unittest tests.applications.test_diagnostics -v`
