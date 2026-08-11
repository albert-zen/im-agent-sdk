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

A configured Controller must have coherent scoped-action wiring before any
runtime owner starts. Once accepted, its optional lifecycle follows the
existing owner order: validation before startup, bounded `close()` on rollback
after Channels and on normal shutdown before Applications. Controller policy
cannot acquire/release the store session or alter admission ownership.

Gateway starts each Channel exactly once with the completed-message callback
and that Channel's admission handler. It performs no signature compatibility
inspection and never retries `start` with one argument. A call-binding failure
from a legacy implementation or a `TypeError` raised inside a valid
two-argument body is one startup failure. The existing Channel failure boundary
stops the current potentially partial Channel once, and common rollback stops
each previously started Channel once in reverse order; no inbound callback can
reach Controller or Application work after the failed startup is closed.

After the current Channel failure boundary returns, common rollback closes
inbound admission before its first common cleanup await, releases only buffered
pre-side-effect claims owned by that startup, stops projection and bounded
extension/delivery runtimes, stops started Channels in reverse order, closes
the Controller, and stops started Applications in reverse order. Normal
shutdown closes the gate first and then performs the same bounded owner cleanup.
A callback outside the live window fails explicitly or is released; it never
starts Application work during teardown.

## Bounds, state, and recovery

`GatewayLimits.startup_buffer_max_pending` fixes FIFO capacity. Overflow is
sticky for that startup attempt, increments fixed redacted diagnostics, fails
startup, and cannot evict an earlier claim. The FIFO and start/stop flags are
process-local and contain no message after successful drain or rollback. The
durable idempotency repository, not this queue, provides restart evidence.

Lifecycle creates no SDK daemon or second runtime. Application and Channel
instances remain their own lifecycle owners; Gateway only sequences their
contract methods and waits for bounded cleanup.

Stopping or rolling back a Gateway also cancels and joins every active Thread
observation worker. It first closes process-local action-route activation
admission, so a concurrent or later scoped route action cannot report success
from a worker that shutdown has cancelled. In-flight bounded baseline work
validates the same lifecycle generation and live worker at every authoritative
delivery suspension; durable route success becomes typed partial and remains
replayable after restart rather than opening a delivery gap. Worker-owned
capacity slots and health facts are
discarded; an active accepted-input fence retains its event lock and buffer
until its final input owner resolves it, rather than authorizing replay early.
The established `restore()` boundary then resets all process-local acceptance
tracking before rebuilding durable route/checkpoint authority. No stopped-worker
registry or durable capacity state survives lifecycle reset.

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
- [ADR 0011](../../../decisions/0011-durable-inbound-admission-before-media.md)
- [ADR 0013](../../../decisions/0013-bounded-application-event-admission.md)
