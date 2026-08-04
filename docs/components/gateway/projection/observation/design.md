# Gateway projection observation design

Component ID: `gateway.projection.observation`

Parent: `gateway.projection`

## Purpose and ownership

This leaf maintains exactly one Gateway-owned Application observation worker
for an active Thread. The worker consumes the Application's normalized live
stream once, preserves its declared order, and makes that canonical stream
available to active projection routes. It owns worker lifecycle, bounded
fan-out consumption, bootstrap barriers, and bounded health facts.

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
backoff and authoritative reconciliation. A per-route Channel failure is not
an observation failure and neither restarts the worker nor interrupts other
destinations.

Accepted IM input may have live output queued before its Turn reply correlation
is durable. The worker holds only a finite per-Thread acceptance buffer until
the correlation is recorded. Its overflow keeps the accepted inbound claim
terminal, records an explicit gap, and recovers from authoritative history;
it never creates permission to dispatch the input again.

Subscription/recovery exceptions remain inside the supervised worker and keep
its existing slot while bounded backoff and authoritative recovery run. A
terminal return, cancellation, or start task failure removes worker-owned
health and releases its identity once no admission lease remains. A caller
cancelled while waiting for a same-Thread starter does not cancel or release
that shared worker. If input acceptance is pending, its correlation fence,
ready event, event lock, and buffered events are acceptance-owned: terminal
worker cleanup must not clear or signal them. The final `send_input` owner
persists or validates the correlation, signals the fence, drains the ordered
buffer, then removes those entries. Gateway stop/rollback applies the same
split worker cleanup; the established `restore()` boundary clears all
process-local acceptance tracking before durable routes and checkpoints rebuild
their existing recovery authority.

## Current structure and authority

The current worker, input-acceptance ordering, and some recovery supervision
share `projection_runtime.py` and the Gateway package root. That is an
explained split candidate, not a license for duplicate workers. The target is
`gateway/projection/observation.py`.

- [ADR 0004](../../../../decisions/0004-event-fanout-and-recovery.md)
- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0013](../../../../decisions/0013-bounded-application-event-admission.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)
