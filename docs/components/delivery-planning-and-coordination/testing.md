# Delivery planning and coordination tests

## Required coverage

Planner tests prove:

- deterministic segment IDs and stable plans;
- native Markdown and declared plain-text fallback golden output;
- code-point, UTF-16, and UTF-8 length handling;
- source content order across text and attachments;
- attachment source, media, count, per-item and group-size constraints;
- none, same-media-family, and mixed grouping;
- first-segment and every-segment reply scope;
- complete preflight rejection before Channel side effects.

Coordinator tests prove:

- FIFO behavior for one Conversation;
- concurrency between different Conversations;
- bounded global and per-destination admission without blocking `submit`;
- capacity is reserved before planning, and one plan has explicit source-item
  and segment bounds; proactive validation/fingerprinting cannot bypass those
  bounds, and capacity rejection has constant-size evidence;
- retry waits do not occupy global execution capacity;
- retry-after hints are never shortened below the platform minimum;
- the default one-attempt policy;
- retries only for explicit retryable receipts;
- unknown results and exceptions are never retried;
- execution stops after the first failed segment, reports every segment, and
  marks a mixed source-item outcome unknown rather than hiding an accepted
  prefix;
- closing the Coordinator cancels both submitted and awaited deliveries before
  Channel shutdown;
- cancelling an awaited inline-artifact delivery joins the Channel send,
  records `unknown`, and only then releases staging;
- a clean Gateway restart opens a fresh Coordinator lifecycle;
- projection and proactive origins use the same injected Coordinator.
- an ADR 0015 O2 observer runs once after the aggregate logical outcome rather
  than per segment/retry, cannot rewrite result or cleanup order, and is not
  replayed as a durable notification after restart;
- retry timing configuration rejects NaN and infinity.

Projection integration tests must fill Coordinator capacity, then prove a
retryable projection is re-admitted through bounded recovery, reaches its
checkpoint after capacity is released, and is never sticky-blocked as a
permanent Channel failure.
O1 integration additionally proves independent per-destination transformation
and suppression, bounded/typed output revalidation before planning, unchanged
behavior when absent, and no invocation for request, proactive, Controller, or
Gateway-error delivery. Crash windows cover reevaluation before suppression
claim completion and authoritative checkpoint convergence without reinvocation
after completion. Exceptions, timeout, capacity rejection, and cancellation
release only a claim known to precede Channel effects; live-only output never
advances a checkpoint. Diagnostics retain only fixed categories and counters.
Interactive request integration must separately prove that a consumed
`REQUEST_OPENED` survives transient capacity pressure and creates its route
correlation without relying on pending-request snapshots. It must also cover
native resolution and expiry while an attempt is admitted but queued, inactive
foreground routes, stop/join behavior, cross-Thread cancellation isolation,
bounded backlog pressure, fan-out larger than the backlog batch, and surfaced
active-route lookup failures.

Integration and conformance tests exercise at least two different Channel
profiles. Native adapter tests remain responsible for platform escaping,
upload calls, receipt mapping, and defensive rejection when a profile is
incorrect.

## Verification

Run the repository verification commands in `AGENTS.md`. Schema validation
must cover the flat v1 `ChannelCapabilities` shape, its derived
`DeliveryProfile`, retryable receipt/state values, optional segment receipts,
optional retry-after hints, v1 positional constructor order, and rejection of
native acceptance IDs on retryable evidence. Clean-wheel smoke tests must import this
component without pulling optional Channel or Application dependencies.
