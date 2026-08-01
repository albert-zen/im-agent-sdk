# Projections and recovery component design

## Purpose

This component turns authoritative Application events and history into
rebuildable IM output projections. It owns observation mechanics, not Agent
history.

## Ownership

It owns:

- independent subscriber queues for live Thread events;
- `ThreadProjectionRuntime` worker supervision, correlation, and health
  lifecycle;
- `ProjectionRouteCoordinator` bootstrap, authoritative reconciliation, and
  serialized delivery decisions for each destination route;
- stable projection route and delivery ID derivation;
- Thread-scoped output observation;
- projection policies and destination lookup inputs;
- cursor-aware recovery and authoritative fallback selection;
- reconciliation inputs that let Gateway deduplicate completed messages.

It does not own:

- the transcript, Turn lifecycle, request state, or native event journal;
- route persistence implementations;
- Channel delivery policy;
- native Application replay guarantees;
- a synthetic restart-unsafe sequence or SDK event log.

Typed interactive requests share route selection and per-route delivery
serialization with message projection, but they do not advance transcript
checkpoints. A Request Presenter produces one stable outbound delivery per
destination. Only accepted/already-completed destinations receive a response
correlation; one route's failure neither authorizes it nor blocks another.

`request.resolved` transitions the minimal request correlation but never ends
a Turn. AppServer connection reset may emit a typed stale resolution solely
to invalidate an unusable response handle; it does not manufacture native
request history. Pending requests are reconciled only from an authoritative
native snapshot capability.

## Fan-out and lifecycle

### Current behavior

Each active Thread subscriber receives an independent stream in publication
order. Native notification callbacks publish without awaiting consumers.
Slow, cancelled, or failed observers cannot steal from another observer or
block the native callback. Current queues are unbounded, so this is producer
isolation rather than complete backpressure or memory isolation.

Within one Gateway event loop, `_ensure_projection` performs worker lookup,
creation, and registration without a suspension point. Concurrent callers
therefore observe the registered task rather than creating a second one. This
single-worker behavior has direct concurrent-observer regression coverage.
Gateway lifecycle calls assume one owning event loop; a future cross-thread
entrypoint would require explicit synchronization.

`message.completed` is one durable message observation. A Turn can produce
many such messages. Only `turn.completed`, `turn.failed`, or
`turn.interrupted` ends that Turn. Current workers remain active until Gateway
shutdown or policy no longer requires observation. `foreground_only` reclaims
a worker when no bound route remains and restores the persisted bound route
after restart. Remembered/all-observer workers follow their durable routes.
Application subscription/recovery exceptions enter bounded-backoff
resubscription and update worker health.

## Projection routes

Input selection, native activation, and output projection are independent.
Routes contain only Thread/Conversation references, optional destination
reply context, per-destination completion checkpoint, and update times.

Supported policies:

- `foreground_only`: deliver only while the Conversation is bound to Thread;
- `remembered_last_recipient`: retain one last destination per Thread;
- `all_observers`: retain all explicit observers.

Destinations are resolved at delivery time. One inbound message object is not
retained as routing truth.

The completion checkpoint is per route because one destination can succeed
while another fails. It stores only the last successfully delivered stable
Agent item ID and boundary time. Ordinary route refresh preserves an omitted
checkpoint and rejects a conflicting explicit value. Advance uses an expected
checkpoint compare-and-swap; opaque Agent item IDs are never sorted to infer
progress.

Per-Turn reply correlation is separate minimal projection state keyed by
authoritative Thread/Turn/client-message identity. It stores the originating
Conversation and IM reply ID, never message content or Turn status. Gateway
creates it for a `started/create_new` result and removes it on an explicit terminal Turn
event, Thread/route cleanup, or configured retention cutoff. Retention is
enforced at Gateway startup and before accepting later IM input, so missing
terminal events cannot grow correlation state without bound in a long-lived
active process. The reply applies only when the correlation Conversation
matches the destination route. A recovered/external Turn without correlation
does not inherit a route's last inbound message.

A `steered/preserve_existing` dispatch must name an already-correlated Turn
before native mutation. Projection authorizes that policy without replacing
the correlation, even when a second Conversation supplied the steer input.
Repositories accept only the first immutable value for a Thread/Turn key.
Missing correlation fails before dispatch; a native steer response with a
different Turn ID is a post-acceptance degradation and cannot retarget either
Turn.

Application acceptance is also the inbound idempotency side-effect boundary.
If reply-correlation persistence or buffered-event draining fails after an
`AcceptedTurn` is returned, the runtime reports a distinct post-acceptance
failure so Gateway can keep the inbound key terminal while still exposing the
projection degradation. Only a failure known to occur before acceptance may
authorize automatic input redelivery.
Dispatch with an unknown acceptance outcome is reported separately by the
Application Port and keeps the side-effect-started claim non-expiring. The
claim is protected through the adapter's typed hook immediately before the
native mutation; a definitive pre-dispatch failure explicitly releases it.
When both primary post-acceptance
processing and buffered-event draining fail, the primary error remains
authoritative and the drain failure is attached as secondary diagnostic
context.

## Bootstrap ordering

Each route has a bootstrap barrier and serial delivery boundary. Gateway
installs the barrier and restored subscription before starting the native
Application producer, then reads a bounded authoritative baseline once the
Application is ready. Live events arriving during Application startup or
baseline recovery remain in the projection subscriber's independent queue
until that route completes baseline delivery. Recovery delivery also waits for
any in-flight `AcceptedTurn` result, so history cannot outrun creation of its
per-Turn reply correlation. The native producer never awaits these barriers.

Both pages/Turns requested from the Application and flattened Agent items
selected for one reconciliation are bounded. A restart route without a
checkpoint is explicitly degraded, while a new route establishes a bounded
recent baseline. Temporary authoritative read failure enters the same bounded
supervisor retry loop as subscription failure.

This bootstrap queue is ordering isolation, not the Delivery Coordinator's
admission queue. Channel planning/execution is bounded after projection;
Application event subscriber and bootstrap queues remain unbounded.

## Recovery

### Current behavior

Replay-capable flow:

```text
subscribe after opaque cursor
→ consume native ordered events
→ surface explicit expiration/gap
→ reconcile from authoritative history when required
```

No-replay flow:

```text
establish live subscription first
→ read authoritative history/catch-up pages
→ reconcile stable item IDs
→ drain live events
```

Streaming deltas may be dropped and reconstructed from completed messages.
Sequence/cursor fields are emitted only when the producer preserves their
declared scope across the recovery window.

New routes read one configured recent history page plus bounded active
catch-up. Existing routes scan newest pages toward their per-route checkpoint
under a configured page bound. Missing/expired checkpoints produce an
explicit gap in worker health while still allowing a bounded recent
projection; no path falls back to scanning the complete archive.

Completed-idempotency and checkpoint state converge during authoritative
ordered recovery. An `already_completed` stable delivery may advance a lagging
checkpoint there; `in_flight` never advances it. Live duplicate events do not
rewrite a different checkpoint because opaque item IDs provide no ordering.

One route's permanent or ambiguous Channel failure blocks that route's later
ordered decisions and records the route ID, without terminating/restarting the
Application subscription or preventing other routes from succeeding. A
zero-side-effect Coordinator capacity rejection is different: it re-enters
bounded supervisor backoff and authoritative recovery, and never enters the
sticky blocked-route set. The health snapshot is SDK infrastructure state,
never Agent Turn/request truth.

Interactive requests cannot assume that recovery history contains the prompt
or that every Application supports a pending-request snapshot. The route
coordinator therefore owns a bounded, process-local presentation task from the
first attempt, including attempts already admitted but queued behind Channel
work. Resolution, expiry, route deactivation, and stop cancel and join it. A
full presentation backlog holds the one consumed event until capacity returns,
and fan-out is admitted in bounded batches, so overload is explicit backpressure
rather than silent request loss or an unbounded hidden queue.

The accepted state and failure-domain design is
[ADR 0007](../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md).
Interactive request routing is defined by
[ADR 0008](../../decisions/0008-interactive-request-routing.md).

Bounded Channel delivery execution and conservative receipt-aware retry use
the common Delivery Coordinator. Application subscriber queues remain
unbounded and are recovered from authoritative history when gaps occur.

## Dependencies and change obligations

Projection/recovery depends on Core contracts, Application/repository Ports,
and bridge state values. Gateway composes it through typed callbacks; the
runtime never imports Gateway implementation.

Changes require the fan-out, recovery, projection routing, restart, and
cross-adapter tests in [testing.md](testing.md).
