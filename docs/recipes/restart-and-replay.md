# Recipe: restart and reconcile missed output

Restart by constructing a fresh object graph over the same durable Gateway
store. There is no public "replay everything" action: Gateway restores bridge
state and reconciles bounded authoritative Application history.

## Prerequisites

- A `SQLiteGatewayStore` on durable storage, with one active owner for each
  `gateway_id`/store namespace.
- Stable Application, Project, Thread, Conversation, item, and action IDs.
- An Application whose authoritative history survives the Gateway process.
- Honest replay/cursor capability, or bounded history and catch-up support.
- A tested shutdown, lease-expiry/takeover, database backup, and restore plan.

## Public API path

```python
from imagent import Gateway, SQLiteGatewayStore


def build_gateway() -> Gateway:
    return Gateway(
        gateway_id="production",
        channels=build_channels(),
        applications=build_applications(),
        store=SQLiteGatewayStore(path="bridge.sqlite3"),
        controller=build_frozen_controller(),
        limits=production_limits,
        projection_policy=projection_policy,
    )


async def run_once() -> None:
    gateway = build_gateway()
    async with gateway:
        await gateway.wait_closed()
```

After a stop or crash, discard the old Gateway, Channel, Controller, and store
objects. Reconstruct them with the same stable configuration and database.
Gateway subscribes before bounded history reconciliation where native replay
is absent, restores routes/checkpoints, and suppresses already completed
stable delivery identities. Do not call `read_history` in a loop to simulate
runtime recovery or persist a second transcript.

## Owner and authority

Gateway owns routes, checkpoints, correlations, idempotency, effect receipts,
and the runtime lease. Applications own transcript, Turn/request state, and
authoritative history. The consumer owns process supervision, backups,
retention, and deployment rollback. See [recovery design](../components/gateway/projection/recovery/design.md),
[checkpoint design](../components/gateway/projection/checkpoints/design.md),
and [ADR 0007](../decisions/0007-projection-lifecycle-and-delivery-boundaries.md).

## Typed failure modes

- `RuntimeLeaseUnavailable` or a lifecycle lease conflict: another runtime is
  active; do not bypass fencing.
- `WorkspaceIdentityConflict`: a stable fixed/flat workspace ID now denotes a
  different canonical root; restore it or deploy a new workspace ID.
- `CursorExpired`, `EventStreamGap`, missing checkpoint, or
  `ProjectionRecoveryUnavailable`: recovery is degraded and bounded, never an
  excuse for an unbounded archive scan.
- `capacity_exhausted`: a bounded worker, queue, idempotency, or receipt owner
  rejected new work.
- `outcome_unknown`: a fenced native or Channel side effect is ambiguous and
  must not be resent automatically.
- `stale_binding`: retained bridge identity no longer resolves to
  authoritative Application ancestry.

## Diagnostics

Inspect `gateway.diagnostics()` for projection state/gap aggregates,
Application/Channel connection facts, startup admission, and lifecycle facts.
Diagnostics reset with the process and are not proof that history is complete.
Verify exact bindings through scoped actions and authoritative resource reads;
use store backups and the Application's own operator surface for their
respective truths.

## Executable evidence

- `PYTHONPATH=src:. python -m unittest tests.gateway.test_reference_consumer -v`
- `PYTHONPATH=src python -m unittest tests.gateway.projection.test_recovery -v`
- `PYTHONPATH=src python -m unittest tests.gateway.persistence.test_sqlite -v`
- `PYTHONPATH=src:. python -m examples.reference_consumer.main`
