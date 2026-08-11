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
`src/imagent/gateway/lifecycle.py`. Canonical `Gateway.start()`,
`Gateway.stop()`, and its async context own the store lease and delegate runtime
ordering to the existing orchestration. `ImAgentGateway.start()` and
`ImAgentGateway.stop()` remain physically at the package root during migration.
This leaf does not own
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

Gateway starts each Application exactly once; an Application that raises after
partial startup receives its one cleanup call before common rollback handles
previously started Applications. Gateway starts each Channel exactly once with the completed-message callback
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
A cleanup failure from any one owner is recorded against the primary failure
and does not skip later owners. On rollback the startup failure remains primary;
on normal stop the first cleanup failure is primary and later cleanup failures
are attached as bounded exception notes. Owner, exception type, and sanitized
detail use one fixed-size summary for both inner teardown and the public
wrapper; C0/C1 and Unicode format controls become visible placeholders, and
cleanup logging emits that summary without an arbitrary traceback. The first
cleanup failure itself is projected to a `GatewayLifecycleFailure` carrying a
bounded public message, bounded original type, and bounded surrogate cause.
The public object graph and its serialized form retain no raw owner exception;
raw owner text never becomes public exception detail or serialized evidence.
Projection is unconditional for ordinary exceptions even when their `str()` is
short and benign; only cancellation/system exceptions and the typed lifecycle
sentinels retain their exact objects. Sentinel authority is exact-type based;
subclasses and other impostors are ordinary failures and receive a closed
projection.
Each owner is invoked exactly once;
the wrapper does not retry the inner runtime teardown to manufacture success.
A callback outside the live window fails explicitly or is released; it never
starts Application work during teardown.

The canonical wrapper starts lease renewal immediately after acquisition, not
after potentially slow adapter startup. A renewal failure uses the same
serialized shutdown path: scoped action/effect surfaces are invalidated before
runtime stop, admission and observation close, and the session/store are
released. `wait_closed()` reports that terminal runtime failure. Construction
or startup failure after acquisition also closes every acquired owner before
the failed `start()` returns. During slow startup, the lease supervisor cancels
the startup owner so the existing startup rollback—not a concurrent second
cleanup path—stops partially started adapters.

Public lifecycle transitions are serialized per `Gateway` instance. Concurrent
`start()` calls join one startup transition and acquire one lease; a `stop()`
racing startup cancels and joins that startup owner so its existing rollback
closes partially started adapters before `stop()` returns. Cancellation while
entering the async context follows the same rollback. A losing or waiting
lifecycle caller never closes another transition's session or store state.

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
admission while holding the same narrow commit fence used by new pre-fenced
route actions. Authoritative preflight runs outside that fence. If stop enters
first, a resumed action cannot write its terminal receipt or route; if the
atomic store transaction entered first, stop waits for it before cancelling
workers. A known foreground workflow enters the same fence around only its
terminal binding/route transaction; a shutdown winner leaves its native result
resumable but cannot gain a post-stop route. A concurrent or later scoped route action therefore cannot report success
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

The canonical public lifecycle contract is `Gateway`, with explicit
`start()`/`stop()`, `wait_closed()`, and a preferred async context. Startup
helper and lease-session types remain internal.
`src/imagent/gateway/runtime.py` owns the canonical wrapper;
`ImAgentGateway.start()` and `ImAgentGateway.stop()` remain in the package root
as an explicitly recorded physical migration gap and are not compatibility
aliases for `Gateway`.

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
