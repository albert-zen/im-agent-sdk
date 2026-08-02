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
- applying an optional product presentation policy only after a concrete
  destination is known and before delivery planning;
- validating that an interactive response comes from a Conversation that
  actually received the request and routing the typed native response;
- per-Conversation serialization and explicit delivery errors.

It does not own:

- native resource registries, transcript, Turn, request, or execution truth;
- native active/open Thread state;
- Slash grammar, command aliases, or fixed presentation;
- Channel-native encoding, escaping, rate limits, or credentials;
- Application workspace, model, provider, sandbox, or runtime mode;
- a durable job system or retry policy not proven by consumers.

## Dependencies

Gateway may depend on Contracts/Core, adapter ports, bridge-state
repositories, Controllers, and projection/recovery. None of those components
may import Gateway.

## Normal input flow

1. A Channel verifies native identity and access policy.
2. Before media preparation it requests a fenced durable admission lease from
   Gateway using the stable Conversation/message identity.
3. A duplicate receives no lease and stops. An admitted Channel prepares media
   and hands one verified `InboundMessage` through the lease.
4. An optional Controller may consume the input through typed actions.
5. Unconsumed content resolves the current `ConversationBinding`.
6. Gateway establishes or refreshes a `ThreadProjectionRoute`.
7. It starts Thread observation before calling `send_input`.
8. The Application emits authoritative user and Agent events.
9. Projection resolves destinations at delivery time.
10. An optional destination presentation policy transforms or suppresses that
    route's `OutboundMessage` without changing its delivery identity.
11. Channel sends an `OutboundMessage`; Gateway records correlation outcome.

A suppressed message is a completed presentation decision. Gateway completes
its outbound idempotency claim, allowing an authoritative projection
checkpoint to advance instead of replaying deliberately hidden activity on
every recovery. A policy exception releases a known-pre-side-effect claim.
Policies cannot change the stable delivery ID or destination, and default
behavior is unchanged when no policy is configured. This hook is product UX;
it must not infer or persist Agent execution truth.
Projected Agent output also exposes two transient reserved metadata fields to
the policy: `imagent_projection_origin` is `live` or `authoritative`, and
`imagent_projection_checkpoint` states whether successful presentation may
advance the route checkpoint. Native/Application metadata cannot forge these
Gateway facts. SDK strips both before durable delivery planning and Channel
send, so they do not change the stable submission fingerprint. Controller,
request, and proactive output omit them.

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
6. Every destination uses the same pure planner and ordered/bounded
   Coordinator as projection and interactive-request presentation.
7. Typed receipts preserve accepted, rejected, partial, and unknown outcomes.

The optional `ProactiveDeliveryJsonHandler` is an ingress adapter, not a web
server. A consumer mounts it in its own authenticated loopback service. It
checks authorization before decoding inline artifacts, uses only a private
configured staging root, and removes staged bytes after the synchronous call.
If the ingress request is cancelled, its awaited Coordinator worker is first
cancelled and joined, the destination becomes `unknown`, and only then are the
staged paths removed.
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
are released on failure known to precede a native side effect. The adapter's
typed pre-dispatch hook protects the inbound claim immediately before native
mutation and authorizes its correlation policy. Once an
Application returns `AcceptedTurn`, the inbound claim becomes terminal even if
reply-correlation persistence or buffered projection draining then fails. The
post-acceptance failure remains observable, but Channel redelivery cannot
silently create a second native Turn.
If transport dispatch begins but the Application acceptance response is lost,
the claim remains in the non-expiring `side_effect_started` state; unknown is
not converted to either success or permission to retry. A failed terminal
idempotency write likewise leaves the protected claim sticky across restart.
Ordinary `in_flight` leases remain reclaimable, including outbound projection
claims whose durable submission record can safely converge a retried worker.
Inbound admission refreshes and verifies fenced lease ownership immediately
before handoff and again after startup buffering and Conversation-lock waiting,
before Controller or binding work. A stale media-preparation worker cannot use
or release a replacement claim. During startup, Gateway buffers the prepared
message together with its owned claim and releases safely unprocessed claims if
startup fails. The inbound lifecycle gate closes before rollback awaits; a
racing callback cannot start Application work after `start()` has failed.
Startup buffering also remains active while queued input drains, so a drain
failure cannot expose a temporary live-processing window before rollback.

Application and Channel failures remain typed or explicitly reported. Gateway
does not convert unknown delivery into success.

On restart, Gateway rebuilds required Thread projection workers from persisted
routes and reconciles from authoritative Application history/catch-up plus
per-route completion checkpoints. New routes receive only a configured
recent/active baseline. Existing routes scan newest pages toward their
checkpoint under strict configured bounds; a missing checkpoint is explicit
degraded health. Gateway never loads an SDK transcript.

Gateway also exposes a synchronous diagnostics snapshot of its process-local
infrastructure. The stable surface aggregates optional Application and Channel
facts, projection health, and startup admission facts without Thread,
Conversation, route, error-text, or message identities. It performs no
repository/native I/O and remains explicitly non-authoritative; consumer health
rendering and export are outside Gateway.

During `start()`, Channel callbacks are admitted into one bounded,
process-local FIFO shared by messages and typed operations until durable
projection routes have been restored. This prevents a Channel that immediately
produces input from racing restoration while preserving cross-kind arrival
order. Overflow fails startup explicitly and normal teardown cancels/joins
owned component work; no inbound mutation is silently discarded.
`startup_buffer_max_pending` configures this shared bound.
If startup fails, or once shutdown begins, the live admission gate rejects
later Channel callbacks until another start completes successfully.

For IM-originated input, Gateway persists a minimal mapping from the returned
`started/create_new` result to the originating Conversation/reply ID. A
`steered/preserve_existing` result is authorized only when that exact native
Turn already has a mapping, and it never replaces the destination. Projection preserves
the event/history Turn envelope and applies the reply only to that same
destination. An external Turn does not inherit a prior IM message.

Projection delivery awaits one logical Coordinator result in a Thread worker.
Application notification callbacks remain non-blocking because they publish
into independent bounded subscriber queues. Filling one queue terminates only
that observation and enters bounded resubscription plus native-authoritative
reconciliation.
`turn_acceptance_event_max_pending` independently bounds events consumed while
one Thread still awaits native input acceptance and reply-correlation write.

Projection workers resubscribe after Application subscription/recovery failure
with bounded backoff and expose process-local infrastructure health.
Known zero-side-effect Coordinator backpressure follows that same bounded
backoff and authoritative recovery path; it does not sticky-block a route.
Foreground workers are reclaimed/restored from binding policy; remembered and
all-observer workers follow their durable routes. A per-route Channel failure
is recorded with its route ID and cannot kill or restart the Application
subscription. The current one-worker creation rule remains a tested
single-event-loop, pre-suspension registration invariant.

Gateway's ordered checkpoint decision does not make Channel side effects and
SQLite atomic. Stable delivery IDs make completed work convergent; a crash
between native send and durable completion can still yield an ambiguous
side-effect outcome. The Coordinator never retries that unknown outcome.

Proactive submission persistence likewise is not a durable job queue. An
`in_flight` or `unknown` record blocks automatic duplicate delivery after a
crash or ambiguous Channel outcome. An explicit `retryable` outcome may resume
the same pinned destination identity. Rejected preflight results are persisted
too, so the same delivery ID cannot change destinations and later become a
send merely because routes or capabilities changed.

## Interactive request flow

An Application `request.opened` event is projected only through active output
routes. A configured Request Presenter renders the request; Gateway stores a
minimal per-destination correlation only after that stable delivery is
accepted or already completed.

Presentation work is bounded separately from ordinary projection. It starts as
a managed, cancellable task before entering Channel coordination; cancellation
propagates through queued Coordinator work. Native resolution, expiry,
foreground-route deactivation, and Gateway stop cancel and join those tasks.
When all presentation slots are occupied, the current consumed request event
waits instead of relying on optional native replay; fan-out larger than the
configured backlog is delivered in bounded batches.

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
