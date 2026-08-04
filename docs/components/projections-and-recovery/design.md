# Historical projection/recovery aggregate

The authoritative target documentation is now the
[Gateway projection subtree](../gateway/projection/README.md). This aggregate
remains current-code evidence while the focused mechanical moves are pending;
it does not define a second component ownership model.

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

The process-local projection-route and Turn reply-correlation implementation
now lives in `gateway.persistence.memory`. This component consumes only its
repository Port. Stable projection ID derivation, active-route selection,
delivery decisions, checkpoint convergence, worker health, and authoritative
recovery remain here; the mechanical repository move does not transfer any of
those policies into persistence.

The complete recovery owner now lives in
`gateway/projection/recovery.py`: public recovery values, bounded reads,
route-reconciliation orchestration, per-worker attempt state, typed retry and
health inputs, and request-snapshot coordination. Exact public values remain
exposed through `imagent.gateway.projection`. The historical
`imagent.recovery` module is absent; aggregate runtime files retain only their
documented observation, input-acceptance, route-delivery, and request-delivery
coordination responsibilities.

Stable completion delivery-ID derivation and the sole expected-current
checkpoint convergence authority live in `gateway/projection/checkpoints.py`;
the derivation is exactly re-exported through `imagent.gateway.projection`.
The historical `imagent.projections` symbol is absent. Its remaining module
retains only documented projection values, outbound-message construction, and
delivery orchestration, then passes typed completion evidence to the injected
checkpoint authority.

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

Each active Thread subscriber receives an independent bounded stream in
publication order. Native notification callbacks publish without awaiting
consumers. Slow, cancelled, failed, or overflowed observers cannot steal from
another observer or block the native callback. Overflow removes only that
subscriber and raises a typed gap on its next read; queued projection items are
discarded rather than retained as a hidden event journal.

Within one Gateway event loop, `_ensure_projection` performs worker lookup,
creation, and registration without a suspension point. Concurrent callers
therefore observe the registered task rather than creating a second one. This
single-worker behavior has direct concurrent-observer regression coverage.
Gateway lifecycle calls assume one owning event loop; a future cross-thread
entrypoint would require explicit synchronization.

The same runtime bounds distinct active worker `ThreadRef` identities with the
positive `GatewayLimits.projection_max_active_threads` value. Existing or
starting same-Thread tasks, including a same-Thread admission in flight, join
before capacity admission; a distinct new Thread at capacity gets a fixed
redacted failure before subscription, reconciliation/checkpoint, or delivery
effects. An admission retains the identity across task turnover until its
caller completes. Worker health and capacity are process-local and disappear
when a worker terminates or is cancelled, unless a short-lived admission lease
still protects that identity. A dispatch-owned acceptance-ordering gate, event
lock, and buffer are instead retained until the final input owner safely resolves
the correlation and drains the buffer; the established restore boundary clears
that process-local acceptance tracking before recovery. Supervised recovery
remains inside the same worker and retains the slot; durable routes and
checkpoints remain the only restart authority.

`message.completed` is one durable message observation. A Turn can produce
many such messages. Only `turn.completed`, `turn.failed`, or
`turn.interrupted` ends that Turn. Current workers remain active until Gateway
shutdown or policy no longer requires observation. `foreground_only` reclaims
a worker when no bound route remains and restores the persisted bound route
after restart. Remembered/all-observer workers follow their durable routes.
Application subscription/recovery exceptions enter bounded-backoff
resubscription and update worker health.

Both live `message.completed` events and authoritative history carry the same
canonical `AgentMessage`. Projection validates at most 16 scalar Metadata facts
with bounded keys/text/numbers and copies them to `OutboundMessage` as a fresh
immutable mapping. Unsupported nested, oversized, or non-finite values fail
before Channel delivery and enter the ordinary projection failure/recovery
path. Projection assigns no meaning to adapter phase/kind keys. Concrete
adapters own the non-secret allowlist and must not place raw native payloads,
credentials, paths, content copies, or resource identity in this map. Metadata
does not alter stable delivery ID, destination, reply correlation, idempotency,
or completion-checkpoint compare-and-swap.

## Projection routes

Input selection, native activation, and output projection are independent.
Routes contain only Thread/Conversation references, optional destination
reply context, per-destination completion checkpoint, and update times.

Supported policies:

- `foreground_only`: deliver only while the Conversation is bound to Thread;
  the typed Thread-bind operation prepares that Conversation's route before
  binding CAS so a crash cannot expose a selected Thread without its edge;
- `remembered_last_recipient`: retain one last destination per Thread;
- `all_observers`: retain all explicit observers.

Destinations are resolved at delivery time. One inbound message object is not
retained as routing truth.

A foreground route prepared before a failed or conflicting bind remains
inactive and harmless. Historical routes may retain independent checkpoint
progress after a Conversation switches away, but binding equality is the only
delivery authority. Its bootstrap barrier is installed before the route write
and released only after binding convergence or failure, preserving baseline/
live ordering when another Conversation already keeps the Thread worker alive.
Multiple Conversations may bind the same Thread and keep independent routes
while one Application subscription worker fans out to all active destinations.

The completion checkpoint is per route because one destination can deliver,
suppress, or fail independently. It stores only the last stable Agent item ID
whose per-destination projection decision durably completed, plus boundary
time. Completion means accepted/already-completed Channel delivery or an ADR
0015 O1 suppression whose outbound idempotency claim completed first. Ordinary
route refresh preserves an omitted checkpoint and rejects a conflicting
explicit value. Advance uses an expected checkpoint compare-and-swap; opaque
Agent item IDs are never sorted to infer progress. Live-only presentation is
never a completion boundary.

An adapter-emitted `message.created` A1 presentation uses the same active-route
lookup, bootstrap barrier, route ordering, outbound idempotency, and common
Coordinator as a completion, but its delivery identity is namespaced by the
stable event ID. It is distinct from a later `message.completed` for the same
native item and returns without checkpoint compare-and-swap. It is not added
to authoritative reconciliation; reconnect/overflow may truthfully lose it.

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
When a typed ordered-event applier fails or is cancelled mid-drain, the
dispatch-owned gate reports the stable per-Thread ordering gap to the existing
authoritative recovery path before it releases its FIFO state. A live worker
recovers through its existing task; a terminal worker records the gap and
starts recovery without retaining a stale cancellation marker. Neither path
creates permission for native input redelivery.
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

This bootstrap barrier is ordering isolation, not a second content queue: live
events remain in the bounded Application subscription. Events consumed while
one or more inputs await `AcceptedTurn` are held in a separately bounded
per-Thread buffer so reply correlation is recorded first. Its overflow records
an explicit gap and enters authoritative recovery; it does not itself change
an inbound claim phase. A known pre-native-side-effect-fence failure may still
release, while an unknown native outcome or a valid accepted input remains non-redeliverable by
the separate dispatch-side-effect rules.

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

Completed-idempotency evidence and checkpoint state converge through the
checkpoint authority during authoritative ordered recovery. An
`already_completed` stable delivery may advance a lagging checkpoint only from
that bounded evidence; `in_flight` never advances it. Live duplicate events do
not rewrite a different checkpoint because opaque item IDs provide no
ordering. The same convergence applies to a durably suppressed O1 decision. A
crash before suppression completes may reevaluate the destination policy
without a Channel side effect; after completion, recovery passes the lagging
checkpoint evidence without invoking O1 again.

One route's typed permanent, ambiguous, or safely deferred Channel outcome
remains inside that destination's route boundary: it blocks later ordered
decisions for that route and records the route ID without
terminating/restarting the Application subscription or preventing other routes
from succeeding. The boundary catches only the typed destination decision;
correlation reads, delivery infrastructure, and checkpoint CAS failures remain
explicit and enter the affected Thread's existing authoritative recovery loop.
Recovery supervision never consumes a Channel retry hint or turns destination
availability into history replay. The health snapshot is SDK infrastructure
state, never Agent Turn/request truth.

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
the common Delivery Coordinator. Application subscriber overflow, App Server
connection reset, and any other unexpected observation restart recover
completed messages from bounded authoritative history. If the Application has
an authoritative pending-request snapshot, it is reconciled after the same gap
for only the affected Thread; otherwise process-local worker health stays
explicitly degraded for interactive-request recovery. Gap recovery never pulls
an unrelated Thread's pending request around that Thread's own
acceptance-order boundary.

Health stores only bounded counters, stable gap codes, and route identities.
It never stores event bodies, prompts, local paths, credentials, or Agent Turn
truth.

## Dependencies and change obligations

Projection/recovery depends on Core contracts, Application/repository Ports,
and bridge state values. Gateway composes it through typed callbacks; the
runtime never imports Gateway implementation.

Changes require the fan-out, recovery, projection routing, restart, and
cross-adapter tests in [testing.md](testing.md).
