# Gateway lifecycle design

Component ID: `gateway.lifecycle`

Parent: `gateway`

## Purpose

Lifecycle starts and stops the one composed Gateway graph while admitting
Channel callbacks through a finite process-local startup buffer. It owns
ordering and rollback, not native transport supervision or durable work.

## Ownership and flow

This leaf owns the `GatewayStartupAdmission` FIFO and the
`GatewayStartupOverflow` and `GatewayNotRunning` failures, implemented in
`src/imagent/gateway/lifecycle.py`. `ImAgentGateway.start()` and
`ImAgentGateway.stop()` remain the lifecycle behavior owned by this leaf, but
their orchestration is still physically implemented at the `imagent.gateway`
package root. That explicit package-root gap is retained in this slice so the
formal facade and lifecycle ordering do not change. This leaf does not own
Application/Channel internal reconnect loops, projection recovery policy,
idempotency state, Controller product behavior, or a durable queue.

Startup validates Controller registration, starts delivery support, opens the
bounded inbound gate, cleans stale correlations, restores projection
observation, starts Applications and Channels, enables projection delivery,
reconciles pending requests, and drains claimed inbound FIFO. The gate remains
in startup mode through the entire drain, so a callback racing a drain failure
joins rollback rather than entering live processing.

Rollback closes inbound admission before its first cleanup await, releases
only buffered pre-side-effect claims owned by that startup, stops projection
and bounded extension/delivery runtimes, stops started Channels in reverse
order, closes the Controller, and stops started Applications in reverse order.
Normal shutdown closes the gate first and then performs the same bounded owner
cleanup. A callback outside the live window fails explicitly or is released;
it never starts Application work during teardown.

## Bounds, state, and recovery

`GatewayLimits.startup_buffer_max_pending` fixes FIFO capacity. Overflow is
sticky for that startup attempt, increments fixed redacted diagnostics, fails
startup, and cannot evict an earlier claim. The FIFO and start/stop flags are
process-local and contain no message after successful drain or rollback. The
durable idempotency repository, not this queue, provides restart evidence.

Lifecycle creates no SDK daemon or second runtime. Application and Channel
instances remain their own lifecycle owners; Gateway only sequences their
contract methods and waits for bounded cleanup.

## Contracts and structure

The stable public lifecycle contract is `ImAgentGateway`; startup helper types
are internal implementation facts. `src/imagent/gateway/lifecycle.py` is the
one helper implementation and `imagent.gateway` remains the formal facade.
`ImAgentGateway.start()` and `ImAgentGateway.stop()` remain in
`src/imagent/gateway/__init__.py` as the explicitly recorded physical
orchestration gap; this slice does not move, redesign, or reorder them.

Dependencies are `gateway.composition`, `gateway.admission`, and
`gateway.projection.observation`, plus the lifecycle contracts of configured
Interaction Controllers, Channels, and Applications.

## Authority

- [Vision](../../../VISION.md)
- [Architecture](../../../ARCHITECTURE.md)
- [Gateway aggregate](../design.md)
- [ADR 0004](../../../decisions/0004-event-fanout-and-recovery.md)
- [ADR 0006](../../../decisions/0006-core-admission-and-policy-ownership.md)
- [ADR 0013](../../../decisions/0013-bounded-application-event-admission.md)
