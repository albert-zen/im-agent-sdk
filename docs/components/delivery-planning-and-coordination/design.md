# Delivery planning and coordination component design

## Purpose

This component converts a logical outbound message into deterministic
Channel-compatible segments and executes those segments with bounded,
destination-ordered concurrency.

It is shared Channel infrastructure. It is not a transcript, persistent job
queue, Agent scheduler, or product retry engine.

## DeliveryPlanner

`DeliveryPlanner.plan(message, profile)` is pure and deterministic. The
profile declares text formats and length units, attachment source/media and
grouping constraints, and reply-reference support. `ChannelCapabilities`
keeps its flat v1 contract surface and exposes `delivery` as a derived typed
view, so there is no second capability source of truth. Planning preserves
logical content order and produces immutable `PlannedDeliverySegment` values.

Segment identity is:

```text
sha256(channel instance, native conversation, source delivery ID, segment index)
```

The source ID and index are also recorded as diagnostic metadata. Identity
never depends on temporary paths, rendered text, or timestamps.

Markdown fallback is conservative and happens only when the profile declares
`fallback`. Text splitting never breaks a Unicode code point, while limits can
be measured in code points, UTF-16 code units, or UTF-8 bytes. Adjacent
attachments are grouped only within declared count, total-size, media-family,
and source constraints. Unsupported content fails before execution.

## DeliveryCoordinator

One Coordinator instance is composed into Gateway and shared by projection,
interactive presentation, and proactive delivery. It reserves bounded global
and per-Conversation capacity before planning or work enters a lane. Work for
the same `ConversationRef` is FIFO; independent Conversations can execute
concurrently. A configured source-item limit and segment limit bound one
logical plan as well as the number of admitted logical messages. A capacity
rejection therefore does not allocate a plan or per-item receipt. The
Coordinator's public preflight uses those same limits; proactive delivery
checks source cardinality before generic validation or payload fingerprinting,
then uses that preflight for every pinned destination. Over-limit content
fails before any source-cardinality-proportional validation or hashing walk,
and every plan is bounded before a native side effect.

`submit` returns immediately with a `DeliveryHandle`. `deliver` waits for one
logical result and is used where the caller must keep staged artifacts alive
or persist a receipt before returning. Both use the same planner, lanes,
limits, and receipt rules. Cancelling an awaited `deliver` cancels and joins
its worker before returning cancellation; a proactive destination persists the
unconfirmed outcome as `unknown` before an ingress removes staged artifacts.

Gateway owns the composed Coordinator lifecycle. Stop cancels admitted work
before Channel shutdown; a later clean Gateway start reopens the quiescent
Coordinator rather than retaining stale lanes or capacity.

The global semaphore surrounds only a native send attempt. A retry delay keeps
the destination lane, preserving order, but releases the global slot so other
Conversations can progress. Idle keyed locks are removed.

The default is one attempt. Only an explicit `retryable_failure` receipt may
be attempted again when consumer configuration permits it. Rejection,
exception, invalid receipt, and unknown outcome never trigger a Coordinator
retry. A native retry-after hint is a minimum; if it exceeds the configured
wait ceiling, the Coordinator returns the retryable receipt instead of
retrying too early.

Zero-side-effect capacity rejection is transient infrastructure pressure, not
a permanent route failure. Projection re-enters its bounded supervisor
backoff and authoritative recovery path; the route is not added to the sticky
Channel-failure set. Completed messages recover from authoritative history.
Interactive request presentations are managed from their first attempt by a
separate bounded runtime backlog, because an Application need not expose a
pending-request snapshot or replay an already consumed `REQUEST_OPENED` event.
Native resolution, request expiry, route deactivation, and Gateway stop cancel
and join both queued and active attempts. When that backlog is full, the one
already-consumed event waits for a slot and large fan-out is admitted in bounded
batches; it is not dropped into a recovery path that may lack request replay.
This deliberately applies backpressure to that Thread's event pump at overload
instead of inventing a durable job queue. Stable delivery IDs make duplicate
native events converge.

## Receipt aggregation

Native segment receipts are mapped back to source content indexes. Execution
stops at the first non-accepted segment and marks the unattempted suffix
`skipped`. Every planned segment has an explicit receipt with its stable
delivery ID and source indexes. If one source item spans several segments,
mixed outcomes aggregate to `unknown`, so an accepted prefix is never hidden
by a later failure. A retryable failure before any acceptance remains
retryable. Once a prefix may have been accepted, a later failure is partial or
unknown and cannot authorize replay of the logical message.
At every receipt level, retryable evidence is mutually exclusive with native
acceptance identity; an adapter cannot label a native message ID safe to retry.

## Boundaries

Channel adapters remain responsible for native encoding, upload, credentials,
API calls, final limit validation, and mapping a platform response to a typed
receipt. Application adapters are unaware of Channel planning. Consumers own
capacity and retry appetite. Persistence stores only stable submission
identity, pinned routes, and receipts; Coordinator memory is never restored as
a queue after restart.

See [ADR 0010](../../decisions/0010-capability-driven-delivery-coordination.md).
