# ADR 0007: Projection lifecycle and delivery boundaries

Status: Accepted

## Context

Live Application events, authoritative history, and IM delivery complete at
different times. A single Thread can project to multiple destinations whose
delivery progress differs. An IM-originated Turn may have a native reply
target, while a Turn started elsewhere does not. Subscription failure and one
Channel send failure are also different failure domains.

The SDK needs enough bridge state to recover honestly without copying Agent
transcript, Turn, request, or execution truth.

## Decision

### Completion checkpoints are per destination route

Each `ThreadProjectionRoute` may carry the stable Agent item ID of its last
successfully delivered completed message and the time that boundary advanced.
An ordinary repository route refresh with no checkpoint preserves these
fields. A `put` carrying a checkpoint must match the stored value; it cannot
silently advance, clear, or replace it. Successful delivery advances one route
with an expected-checkpoint compare-and-swap. A stale caller fails explicitly,
and progress for another Conversation never overwrites it. Agent item IDs are
opaque and are never compared to guess ordering.

The idempotency claim distinguishes newly acquired work, an already completed
delivery, and work currently in flight. A fresh successful send advances its
route checkpoint. During authoritative ordered recovery, an
already-completed stable delivery ID may converge a lagging checkpoint; an
in-flight claim cannot. Live duplicates never use opaque Agent item IDs to
guess order. Native Channel send, durable idempotency completion, and
checkpoint advance are not one transaction. The SDK guarantees one ordered
delivery decision per stable ID, not strict exactly-once external side effects
across a process crash.

New routes read only a bounded recent baseline plus active Turn catch-up.
Existing routes scan newest authoritative pages toward their checkpoint with a
strict page/item limit. A missing or expired boundary yields explicit degraded
projection health rather than an unbounded scan or false complete result.
Message bodies and Turn state are never stored in the checkpoint.

### Baseline and live delivery share a per-route order boundary

Gateway establishes the live Application subscription before authoritative
baseline reading. A per-route bootstrap barrier holds that route's projection
consumer until the bounded baseline finishes. Native producers still publish
to independent subscriber queues without awaiting Gateway or Channel work.

ADR 0012 bounds Application subscriber and Turn-acceptance event admission;
the bootstrap barrier does not copy events into a second content queue. ADR
0010 separately adds bounded Channel-delivery admission, ordered execution,
receipt-aware retry, and explicit backpressure after projection.

### Reply correlation is explicit minimal bridge state

For IM-originated input, Gateway records only:

- authoritative Application/Thread/Turn and client-message identity;
- originating Conversation and IM reply ID;
- correlation creation time.

The mapping is created from `AcceptedTurn`, never Metadata or a route's latest
inbound message. Live projection preserves `AgentEvent.turn_id`; recovery uses
native history Turn envelopes. An explicit terminal Turn event removes the
correlation. Configurable time retention and route/Thread cleanup bound stale
entries when a terminal event is missing. A correlation applies only when its
Conversation matches the destination route. If recovery has no matching
correlation, output uses no reply unless the route carries an explicit Channel
topic/default context.

### Observation and delivery failures are separate

One supervised worker owns one Application Thread subscription in a Gateway.
Subscription/recovery failure updates infrastructure health and retries with
bounded backoff. A Channel send failure is isolated per destination, updates
delivery health, and does not restart observation or trigger history replay.

`foreground_only` requires observation only while a matching Conversation
binding selects the Thread, including after restart. `remembered_last_recipient`
and `all_observers` retain observation while their durable routes exist.

## Classification

- **Core invariant:** per-route progress, explicit Turn correlation,
  baseline-before-live order, one-worker lifecycle, failure-domain isolation.
- **Optional capability:** native replay/cursor and native reply/topic support.
- **Adapter-specific policy:** translating optional `reply_to` and native
  cursor/history behavior.
- **Consumer policy:** choosing projection policy, explicit history UX, retry
  tuning beyond safe infrastructure resubscription, and permissions.

## Cross-product evidence

Codex/Zen App Server and T3 both return `AcceptedTurn` and preserve native Turn
identity on events/history. Reply-capable and flat/no-reply Channel profiles
both consume the same optional outbound reply field; native rendering remains
adapter policy. No Codex phase Metadata becomes a public correlation
contract.

## Consequences

Projection state remains small and rebuildable from authoritative Application
content. A destination can recover independently without replaying a complete
archive. Delivery retry/backpressure follows ADR 0010 without coupling
Application subscription health to Channel availability.
