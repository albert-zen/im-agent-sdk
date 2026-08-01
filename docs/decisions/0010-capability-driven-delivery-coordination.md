# ADR 0010: Capability-driven delivery planning and bounded coordination

Status: Accepted

## Context

QQ, Telegram, Feishu, Weixin, and future Channels differ in text length units,
Markdown support, attachment sources and grouping, reply behavior, and native
limits. Those differences are real Channel capabilities. Leaving every
adapter to reinterpret one logical `OutboundMessage` independently made
projection and proactive delivery diverge and gave the Gateway no common
place to enforce ordering or bounded admission.

The shared problem is not one product's retry preference. Multiple Channels
need deterministic capability-based representation, and every Agent
Application can produce output for the same destination. Conversely,
platform escaping, credentials, upload APIs, native idempotency, and
rate-limit interpretation remain adapter facts.

## Decision

### Delivery profile and pure planning

Each Channel advertises the v1 flat `ChannelCapabilities` contract and exposes
the same facts as a derived typed `DeliveryProfile`: plain/Markdown support,
text length unit and limit, attachment source/media/grouping limits, and reply
support/scope. The derived view avoids a duplicate capability authority and
preserves existing v1 constructors and documents. `DeliveryPlanner` is a pure function of one logical
`OutboundMessage` and one profile. It:

- preserves source content order;
- performs only declared Markdown fallback;
- splits text according to code points, UTF-16 code units, or UTF-8 bytes;
- groups attachments only as the profile permits;
- derives stable segment IDs from destination, source delivery ID, and index;
- rejects an unrepresentable message before a Channel side effect.

The planner does not know QQ, Codex, IMCodex, credentials, native APIs, or
product UX. Native adapters still validate and encode each planned segment.

### Ordered bounded coordination

`DeliveryCoordinator` is the one process-local execution seam used by both
authoritative Agent-event projection and proactive delivery. It provides:

- FIFO execution per `ConversationRef`;
- concurrency between unrelated Conversations;
- bounded global and per-destination admission;
- admission before planning plus configured per-message source-item and
  segment bounds;
- one bounded preflight API reused by proactive, projected, and interactive
  delivery, with source cardinality checked before validation/fingerprinting;
- a non-blocking `submit` handle and a synchronous `deliver` convenience;
- explicit backpressure as `retryable_failure`;
- explicit per-segment receipts plus aggregation back to source content
  receipts without overwriting an accepted prefix.

Waiting for a retry delay retains destination order but does not occupy a
global send slot. Coordinator queues, locks, and handles are process-local;
they are not a transcript, durable queue, or job scheduler.

An awaited delivery owns its worker lifetime: cancellation cancels and joins
the worker before the caller may release temporary artifacts. Proactive state
records that unconfirmed cancellation as `unknown`. Capacity rejection is
known to have zero Channel side effects, so projection treats it as transient
supervisor pressure and retries from authoritative history rather than
permanently blocking the route. Interactive requests are the exception to the
history mechanism: their consumed event may not replay and pending snapshots
are optional, so the route coordinator manages the bounded presentation from
its first attempt. Resolution, expiry, route deactivation, and shutdown cancel
and join it. A saturated presentation backlog backpressures the current Thread
event while fan-out enters in bounded batches; it never assumes restart can
recover an already consumed request.

### Conservative outcomes and retry

Receipts distinguish accepted, rejected, explicitly retryable, and unknown
outcomes. The default coordinator attempts once. A consumer may configure
additional attempts, but only a Channel receipt explicitly marked
`retryable_failure` can be retried. Exceptions and `unknown` are never
retried because a native side effect may already have happened. If a later
segment fails after an earlier segment was accepted, the aggregate becomes
partial/unknown rather than replaying the whole logical message.
No retryable top-level, item, or segment receipt may contain native acceptance
identity. Retry timing and backpressure configuration must be finite.

Durable proactive submission state records retryable outcomes so the same
pinned identity may be submitted again. It still stores no payload and does
not become a durable job system. Route snapshots never move on retry.

## Ownership classification

- **Core invariant:** deterministic segment identity, per-destination order,
  bounded pre-planning admission and per-plan size, truthful receipt
  aggregation, joined artifact lifetime, no retry of ambiguity.
- **Optional capability:** Markdown, attachment sources/grouping, reply scope,
  native retryable outcome and retry-after hint.
- **Adapter-specific policy:** escaping, upload/API calls, platform response
  mapping, native idempotency and platform-specific defensive validation.
- **Consumer policy:** configured capacity, concurrency, retry count and
  product-facing error/UX behavior.

## Consequences

Every output origin uses one observable execution path without making the SDK
an Agent runtime or durable dispatcher. Adding a Channel requires an honest
profile and conformance tests, not Gateway conditionals. A profile that
overstates support can still cause a native rejection, so adapters remain the
final platform authority.

Crash atomicity is unchanged: a process can still stop after native acceptance
and before durable completion. Stable IDs aid convergence, but `unknown`
remains ambiguous and is never silently resent.
