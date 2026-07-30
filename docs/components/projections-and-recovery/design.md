# Projections and recovery component design

## Purpose

This component turns authoritative Application events and history into
rebuildable IM output projections. It owns observation mechanics, not Agent
history.

## Ownership

It owns:

- independent subscriber queues for live Thread events;
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
single-worker behavior lacks a direct concurrency regression test. Gateway
lifecycle calls currently assume one owning event loop; a future cross-thread
entrypoint would require explicit synchronization.

`message.completed` is one durable message observation. A Turn can produce
many such messages. Only `turn.completed`, `turn.failed`, or
`turn.interrupted` ends that Turn. Current workers remain active until Gateway
shutdown or until an exception ends the worker; route removal does not
currently tear them down.

## Projection routes

Input selection, native activation, and output projection are independent.
Routes contain only Thread/Conversation references, optional reply
correlation, and update time.

Supported policies:

- `foreground_only`: deliver only while the Conversation is bound to Thread;
- `remembered_last_recipient`: retain one last destination per Thread;
- `all_observers`: retain all explicit observers.

Destinations are resolved at delivery time. One inbound message object is not
retained as routing truth.

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

Current Gateway first observation and restart reconciliation can read every
history page. Delivery idempotency can suppress previously completed sends but
does not bound the scan or prevent a new Conversation from receiving old
archive messages.

## Target invariants and known gaps

- The current one-worker event-loop invariant has direct concurrency coverage
  and remains explicit when lifecycle code changes.
- First observation establishes a live baseline plus bounded recent/active
  catch-up. A per-route bootstrap barrier preserves baseline-before-live
  ordering when a message completes during reconciliation. Full history is an
  explicit user operation.
- Restart/foreground reconciliation stops at a Gateway-owned delivery
  completion boundary. Route refresh merges rather than clears this
  checkpoint, and successful delivery advances it atomically. Missing or
  expired boundaries surface an explicit degraded/gap state instead of
  triggering an unbounded scan. The checkpoint is projection state, not
  transcript truth.
- Long-lived destination/topic reply context is not per-Turn truth. Per-Turn
  correlation uses an explicit minimal mapping from authoritative
  Turn/client-message identity to the originating IM reply ID. Projection
  preserves the `AgentEvent.turn_id` envelope; it does not hide correlation in
  Metadata or infer it from the latest route. An external Turn without an IM
  origin uses no reply unless a Channel-specific topic/default policy applies.
- A managed worker is reclaimed when no route or recovery policy still
  requires observation.
- Application subscription/recovery failures are observable and supervised
  with bounded resubscription without requiring new input or restart. A
  per-route Channel delivery failure is isolated from that subscription and
  exposed for later receipt-aware retry by Issue #12; it never triggers an
  archive rescan.
- A minimal worker-health snapshot exposes SDK infrastructure state without
  representing Agent Turn/request truth or pre-empting Issue #13 telemetry.

The first target above describes untested current behavior; the remaining six
guarantees are not fully implemented at the current 9fca3dd runtime baseline.
They are tracked by
[Issue #14](https://github.com/albert-zen/im-agent-sdk/issues/14).

Bounded Channel delivery execution/backpressure/retry is separate
[Issue #12](https://github.com/albert-zen/im-agent-sdk/issues/12) work. Current
subscriber queues remain unbounded.

## Dependencies and change obligations

Projection/recovery depends on Core contracts and Application ports. Gateway
may compose it; it never depends on Gateway implementation.

Changes require the fan-out, recovery, projection routing, restart, and
cross-adapter tests in [testing.md](testing.md).
