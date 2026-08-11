# Gateway projection observation design

Component ID: `gateway.projection.observation`

Parent: `gateway.projection`

## Purpose and ownership

This leaf maintains exactly one Gateway-owned Application observation worker
for an active Thread. The worker consumes the Application's normalized live
stream once, preserves its declared order, and makes that canonical stream
available to active projection routes. It owns worker lifecycle, bounded
fan-out consumption, bootstrap ordering, and bounded health facts. Its one
private route coordinator implements the sole per-route bootstrap barrier and
ordered route delivery boundary; there is no second barrier or owner.

It does not own a native producer, raw native callback, transcript, Agent
event journal, a second subscription per Conversation, destination selection,
Channel delivery, or native Turn/request/execution state. Applications keep
their own subscription and history truth; Gateway consumes their typed
normalized boundary only.

## Lifecycle and capacity

Worker identity is the stable Application/Thread reference, never a message
body or timestamp. Within the owning Gateway event loop, first-observer lookup,
creation, and registration contain no suspension point, so concurrent route
activation observes the same registered task. A future cross-thread entry
point requires explicit synchronization rather than relying on this invariant.

`GatewayLimits.projection_max_active_threads` is one positive, process-local
bound for distinct active worker identities. The runtime evaluates an existing
or starting worker, or an in-flight admission, for the same stable `ThreadRef`
first, so same-Thread routes and waiters join it even at the final slot. An
in-flight admission retains a counted identity across an existing worker's
terminal transition until its caller reaches `finally`; a distinct Thread
therefore cannot occupy the final slot between route preparation and
`_ensure_projection`. Only a distinct new Thread at the limit fails with a
fixed redacted capacity error, before Application subscription, authoritative
recovery/checkpoint, presentation, or Channel delivery. Admission and task
registration still have no suspension point. The bound neither persists a
worker registry nor changes durable route, checkpoint, or Application
authority.

The Application publishes into independent finite subscriber queues without
awaiting a Gateway or Channel consumer. A slow, cancelled, failed, or
overflowed subscriber loses only its own live position, reports a typed gap,
and enters recovery; it cannot steal another observer's event or block an
Application socket-read callback. Gateway does not turn discarded live items
into a hidden replay queue. The bootstrap barrier orders bounded baseline
reconciliation before this route drains live observations, while the live data
remains only in the existing bounded Application subscriber queue.

`ThreadProjectionRuntime.reconcile_action_route(route_id)` is the explicit
composition seam after a scoped Conversation action has durably changed
binding/route state or replayed its terminal success. It reads current active
routes through this owner's route authority, stops workers no longer authorized,
and for a still-active returned route installs the bootstrap barrier, ensures
the one bounded worker, and performs bounded authoritative reconciliation. It
never creates or restores a route, writes a binding, consults a consumer
surface, or repeats the durable action. A replay whose route was later removed
therefore performs only current-state convergence. Capacity or activation
failure propagates to the action adapter for typed partial classification;
success is not reported merely because route persistence succeeded. Once a
route barrier is installed, baseline failure or cancellation leaves it closed;
only successful reconciliation or later explicit replay opens live delivery.
Action-route admission is tied to one process-local projection lifecycle
generation. Stop closes that admission before cancelling workers, and startup
reopens it only after durable routes have rebuilt their workers and delivery is
ready. Reconciliation
validates the same generation and a live registered worker before, throughout,
and after its bounded baseline. Shutdown or worker termination therefore
surfaces the existing typed `stale_runtime` activation failure instead of false
success; a pre-write rejection changes no route, while a durable success
remains fenced and can converge through terminal replay after restart.
An authoritative destination decision that is already sticky or becomes
ambiguous during that action baseline is propagated to the action seam rather
than swallowed as worker-local isolation; its barrier stays closed and the
Channel effect is not retried.
For scoped actions whose route ID is known before persistence,
`begin_action_route`/`complete_action_route` expose that same coordinator barrier
to composition so durable visibility cannot precede baseline fencing. Begin
returns an opaque, coordinator-owned generation lease used only to serialize
same-route action lifecycles and to associate completion with the exact barrier
generation it began. Reconciliation and completion carry that lease back to the
same owner; stale completion cannot open, retain, or release a later generation.
When a route has been durably removed, reconciliation retires its barrier,
wakes blocked delivery, and cancels its route-scoped retry before returning.
Delivery rechecks the current barrier while holding the route lock, so a waiter
that observed an older open generation cannot cross a newly closed one. These
methods never grant store, route-authority, or runtime access to Controller
consumers, and the opaque lease is not a public consumer surface.

`message.completed` is a durable completed-item observation, not a Turn
terminal event. Only explicit `turn.completed`, `turn.failed`, or
`turn.interrupted` closes the corresponding Turn. A live-only A1
`message.created` presentation uses this same route ordering and delivery path
but is explicitly non-recoverable and never advances a completion checkpoint.

## State and restart

The worker's queue depths, retry state, and health counters are process-local
and bounded. Durable routes and Conversation bindings decide which Thread
workers are restored; `foreground_only` retains observation only while a
matching binding authorizes a route, while remembered/all-observer policies
follow their durable routes. Subscription or recovery failure gets bounded
backoff and authoritative reconciliation. A typed per-route Channel decision
failure is not an observation failure and neither restarts the worker nor
interrupts other destinations. Repository reads and checkpoint persistence
are not inside that destination catch; a transient failure restarts only the
affected Thread's same worker and converges through authoritative recovery.

Accepted IM input may have live output queued before its Turn reply correlation
is durable. The input-dispatch leaf owns the finite per-Thread
acceptance-ordering gate and buffer. The worker forwards the normalized event
once through that typed gate until correlation is recorded; it does not own or duplicate the
buffer. Overflow or a mid-drain ordered-event application failure records an
explicit gap and recovers from authoritative history; it never creates
permission to dispatch the input again, a second concurrent worker, or an
inbound-claim transition. A known pre-native-side-effect-fence dispatch failure still
releases its matching claim; only the native side-effect fence/unknown outcome
or a valid `AcceptedTurn` determines that claim's non-redelivery status.

Subscription/recovery exceptions remain inside the supervised worker and keep
its existing slot while bounded backoff and authoritative recovery run. A
terminal return, cancellation, or start task failure removes worker-owned
health and releases its identity once no admission lease remains. A caller
cancelled while waiting for a same-Thread starter does not cancel or release
that shared worker. If input acceptance is pending, its dispatch-owned
acceptance-ordering gate, ready event, event lock, and buffered events must not
be cleared or signalled by terminal worker cleanup. On every final
pending-dispatch exit, the dispatcher performs any required correlation work,
signals the ordering gate, drains the ordered buffer, then removes those
entries. Gateway stop/rollback
applies the same split worker cleanup; the established `restore()` boundary
invokes the dispatch-owned reset before durable routes and checkpoints rebuild
their existing recovery authority.

## Physical structure and authority

`gateway/projection/observation.py` is the sole implementation of the Thread
worker/runtime, active-worker reservation, health/state values, live
normalization/delivery helpers, and private per-route bootstrap/delivery
coordinator. The typed projected-message and authoritative-slice facts remain
with recovery.
`imagent.gateway.projection` re-exports the exact
`ThreadProjectionRuntime` and `ProjectionWorkerHealth` public contracts.
Capacity errors and worker state remain canonical implementation values, not
facade exports. The historical `imagent.projection_runtime`,
`imagent.projections`, and `imagent.projection_routes` modules are absent
rather than compatibility shims.

Dispatch acceptance ordering lives in `gateway/input/dispatch.py`; observation
consumes its narrow typed gate rather than owning that state. Recovery-specific
attempt state, error classification, retry inputs, authoritative
reconciliation, and request-snapshot coordination are delegated to the
canonical private supervisor in `gateway/projection/recovery.py`. Observation
retains the sole task/subscription loop and normalized event consumption;
neither collaboration permits a duplicate worker or second subscriber.

- [ADR 0004](../../../../decisions/0004-event-fanout-and-recovery.md)
- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0013](../../../../decisions/0013-bounded-application-event-admission.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)
