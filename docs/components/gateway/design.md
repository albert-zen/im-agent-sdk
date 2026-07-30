# Gateway component design

## Purpose

The Gateway composes Channel adapters, optional Controllers, Agent Application
adapters, bridge-state repositories, and projection/recovery services. It is
the deterministic IM boundary, not an Agent runtime.

## Ownership

The Gateway owns:

- resolving an inbound Conversation to its selected Application/Project/Thread;
- executing typed Gateway operations atomically per Conversation;
- routing typed Application operations without reinterpreting them;
- inbound idempotency and outbound delivery correlation;
- scoped proactive text/artifact delivery and immutable route snapshots;
- establishing Thread observation before input delivery;
- composing the projection runtime with Application execution and Channel
  send callbacks;
- validating that an interactive response comes from a Conversation that
  actually received the request and routing the typed native response;
- per-Conversation serialization and explicit delivery errors.

It does not own:

- native resource registries, transcript, Turn, request, or execution truth;
- native active/open Thread state;
- Slash grammar, command aliases, or fixed presentation;
- Channel Markdown, chunking, rate limits, or credentials;
- Application workspace, model, provider, sandbox, or runtime mode;
- a durable job system or retry policy not proven by consumers.

## Dependencies

Gateway may depend on Contracts/Core, adapter ports, bridge-state
repositories, Controllers, and projection/recovery. None of those components
may import Gateway.

## Normal input flow

1. A Channel emits a verified `InboundMessage`.
2. Gateway claims the stable inbound idempotency key.
3. An optional Controller may consume the input through typed actions.
4. Unconsumed content resolves the current `ConversationBinding`.
5. Gateway establishes or refreshes a `ThreadProjectionRoute`.
6. It starts Thread observation before calling `send_input`.
7. The Application emits authoritative user and Agent events.
8. Projection resolves destinations at delivery time.
9. Channel sends an `OutboundMessage`; Gateway records correlation outcome.

## Proactive delivery flow

1. A caller submits a typed `DeliveryIntent` and opaque credential.
2. Gateway authenticates a `DeliveryPrincipal` and checks the exact Thread or
   Conversation scope.
3. A Thread target resolves through the same projection policy used by Agent
   output; `all_observers` can produce multiple destinations.
4. Gateway preflights every destination before any Channel side effect.
5. It atomically reserves an SDK-origin-and-principal-namespaced submission
   identity with the caller delivery ID, immutable route snapshots, and
   payload/authority fingerprints. The authorizer cannot select the origin
   namespace.
6. Every destination uses the same outbound execution seam as projection and
   interactive-request presentation.
7. Typed receipts preserve accepted, rejected, partial, and unknown outcomes.

The optional `ProactiveDeliveryJsonHandler` is an ingress adapter, not a web
server. A consumer mounts it in its own authenticated loopback service. It
checks authorization before decoding inline artifacts, uses only a private
configured staging root, and removes staged bytes after the synchronous call.
The reference `imagent-send` client accepts only loopback HTTP(S), reads a
scoped credential from a file or stdin, and never reads Gateway persistence or
Channel credentials.

Thread-targeted public results expose route identity and sanitized outcome
only. They omit the resolved native Conversation and both aggregate and
per-item native message IDs, plus all free-form destination, receipt, and item
details that could contain hidden routing or platform diagnostics. Explicit
Conversation callers may receive only the Conversation identity they already
supplied.

Listing never changes a binding. Binding a Thread does not activate native UI
state. Observing a Thread does not select it for future input.

## Failure and restart

Conversation mutations use revision guards and serialize per Conversation.
Idempotency claims are completed only after the scoped operation succeeds and
are released on retryable failure.

Application and Channel failures remain typed or explicitly reported. Gateway
does not convert unknown delivery into success.

On restart, Gateway rebuilds required Thread projection workers from persisted
routes and reconciles from authoritative Application history/catch-up plus
per-route completion checkpoints. New routes receive only a configured
recent/active baseline. Existing routes scan newest pages toward their
checkpoint under strict configured bounds; a missing checkpoint is explicit
degraded health. Gateway never loads an SDK transcript.

During `start()`, Channel callbacks are admitted into a short process-local
buffer until durable projection routes have been restored. This prevents a
Channel that immediately produces input from racing restoration; queued input
then drains through the normal Conversation locks.

For IM-originated input, Gateway persists a minimal mapping from the returned
`AcceptedTurn` to the originating Conversation/reply ID. Projection preserves
the event/history Turn envelope and applies the reply only to that same
destination. An external Turn does not inherit a prior IM message.

Current projection delivery awaits the Channel send in a Thread worker.
Application notification callbacks remain non-blocking because they publish
into independent subscriber queues, but those queues are not yet bounded.
Bounded delivery execution, backpressure, and retry belong to the planned
Delivery Coordinator work in
[Issue #12](https://github.com/albert-zen/im-agent-sdk/issues/12); until then
memory pressure from a persistently slow Channel is an explicit limitation.

Projection workers resubscribe after Application subscription/recovery failure
with bounded backoff and expose process-local infrastructure health.
Foreground workers are reclaimed/restored from binding policy; remembered and
all-observer workers follow their durable routes. A per-route Channel failure
is recorded with its route ID and cannot kill or restart the Application
subscription. The current one-worker creation rule remains a tested
single-event-loop, pre-suspension registration invariant.

Gateway's ordered checkpoint decision does not make Channel side effects and
SQLite atomic. Stable delivery IDs make completed work convergent; a crash
between native send and durable completion can still yield an ambiguous
side-effect outcome. Receipt-aware retry/backpressure remains Issue #12.

Proactive submission persistence likewise is not a durable job queue. An
`in_flight` or `unknown` record blocks automatic duplicate delivery after a
crash or ambiguous Channel outcome. Rejected preflight results are persisted
too, so the same delivery ID cannot change destinations and later become a
send merely because routes or capabilities changed.

## Interactive request flow

An Application `request.opened` event is projected only through active output
routes. A configured Request Presenter renders the request; Gateway stores a
minimal per-destination correlation only after that stable delivery is
accepted or already completed.

Slash text and Channel-native actions submit
`conversation.respond_request`. Gateway then:

1. finds all correlations for the application-scoped `RequestRef` under a
   request-scoped lock;
2. rejects an unknown/stale request, an already responded/resolved request, or
   a Conversation that never received it with distinct stable codes;
3. calls Application `request.respond` using the stored
   Application/Thread scope;
4. marks every destination correlation responded only after native success.

The later authoritative `request.resolved` event marks all correlations
resolved or stale. Changing the Conversation's current input binding does not
retarget an earlier delivered request. Sender admission remains Channel or
consumer policy; destination validation is not approval authorization.
If one destination is still completing delivery when another wins the native
response, its later correlation atomically inherits the request-wide state
instead of reopening the request.
Request-scoped serialization uses a waiter-counted keyed lock that removes its
entry after the last current/waiting caller exits; completed request IDs do not
accumulate in Gateway memory.

At process start, Gateway snapshots only the pre-existing open bridge
correlations, installs restored Thread subscriptions, and only then starts
native Application producers. Restored workers hold live events behind their
route bootstrap barriers until Applications are ready for authoritative
recovery. Pending-snapshot reconciliation may stale only the pre-start
snapshot; a new request emitted during `Application.start()` is therefore not
mistaken for unverifiable restart state.

When no Presenter is configured, an unexpected request is an explicit
projection failure and creates no response correlation. A Full Access product
that never receives native requests therefore incurs no prompt or SDK policy.

## Change obligations

Changes to `gateway.py` require checking:

- projection/recovery design when subscription, routing, or recovery changes;
- persistence design when stored bridge state changes;
- Controller design when the inbound extension seam changes;
- Gateway operation and vertical-slice tests;
- all fake, Codex, Zen, T3, and Channel seams affected by orchestration.
