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
- Channel-native encoding, escaping, rate limits, or credentials;
- Application workspace, model, provider, sandbox, or runtime mode;
- a durable job system or retry policy not proven by consumers.

## Dependencies

Gateway may depend on Contracts/Core, adapter ports, bridge-state
repositories, Controllers, and projection/recovery. None of those components
may import Gateway.

## Composition groups

Gateway construction groups only owner-scoped dependencies that otherwise
grow together:

- immutable `GatewayRepositories` holds bindings, idempotency, projection
  routes, request correlations, and delivery submissions;
- immutable `GatewayLimits` holds every bounded capacity, recovery page/item
  limit, retry delay, and correlation retention value, including the positive
  finite in-memory delivery-submission record bound and active Conversation
  serialization-key bound (both 4096 by default);
- immutable `GatewayExtensions` holds the optional Controller, Request
  Presenter, I1 inbound-content transformer, I2 inbound-failure presenter, and
  O1 destination-presentation policy and is the only group later Gateway-owned
  ADR 0015 seams extend.

Channels, Applications, projection policy, delivery authorization, and the
shared Delivery Coordinator remain explicit top-level composition
dependencies. Application A1 configuration and Channel startup validation do
not enter `GatewayExtensions`. The groups are frozen typed construction values,
not service locators: Gateway internals resolve their fields once, and no
extension receives a group, repository, or Gateway reference. Repository-local
in-memory defaults are constructed from that exact limits group; an explicitly
injected repository remains authoritative and is never wrapped or reconfigured.
Every runtime default/lifecycle ordering otherwise remains the same as before
this refactor. The prior parallel keyword constructor is removed once
repository call sites migrate, so there is one public composition shape.

## Typed extension boundary

Gateway may compose the I1 inbound-content, I2 inbound-failure, O1
destination-presentation, and O2 delivery-outcome positions defined by ADR
0015. Each position has a separate typed protocol and fixed failure semantics;
Gateway does not expose a generic middleware callback, raw event stream, or
mutable processing context.

Composition groups repositories, runtime limits, and consumer extensions by
ownership before adding more seams. An extension receives only its minimum
stage input and cannot access Gateway repositories, bindings, routes,
checkpoints, correlations, continuation selection, or another extension
through the SDK contract. Application-native presentation/materialization and
Channel startup validation remain on their owning concrete adapters.

## Normal input flow

1. A Channel verifies native identity and access policy.
2. Before media preparation it requests a fenced durable admission lease from
   Gateway using the stable Conversation/message identity.
3. A duplicate receives no lease and stops. An admitted Channel prepares media
   and hands one verified `InboundMessage` through the lease.
4. An optional Controller may consume the input through typed actions.
5. An optional I1 `InboundContentTransformer` may replace only unconsumed
   typed content. Gateway awaits it under the configured finite lifetime and
   validates a non-empty, bounded tuple of `TextContent` and
   `AttachmentContent` before continuing.
6. Unconsumed content resolves the current `ConversationBinding`.
7. Gateway establishes or refreshes a `ThreadProjectionRoute`.
8. It starts Thread observation before calling `send_input` with the default
   continuation preference defined by ADR 0012.
9. Any inbound processing or dispatch failure is classified before an
   optional I2 presenter runs; claim state remains Gateway-owned.
10. The Application emits authoritative user and Agent events.
11. Projection resolves destinations at delivery time and may apply O1.
12. Channel sends one coordinated logical `OutboundMessage`; O2 observes only
    the final typed outcome and cannot change it.

O1 receives the fully routed immutable `OutboundMessage` and one bounded typed
origin distinguishing authoritative, history-reproducible projection from
live-only presentation. It runs only after Gateway acquires the stable
destination-scoped outbound idempotency claim and before delivery planning.
It may replace presentation content for that destination or suppress it, but
cannot change the delivery ID, Conversation, reply target, creation time, or
introduce attachment authority. Gateway revalidates its bounded content and
metadata before planning. Request presentation, proactive delivery,
Controller output, and Gateway error delivery do not invoke O1.

Suppression completes the existing outbound claim before projection checkpoint
compare-and-swap. A crash before completion may reevaluate the replay-safe
policy; authoritative recovery after completion observes `already_completed`,
bypasses O1, and converges the checkpoint. Live-only suppression completes its
stable event-scoped claim but never advances a projection checkpoint. Policy
failure, invalid output, timeout, capacity rejection, or cancellation known to
precede Channel side effects releases only the matching owned claim and enters
the existing projection recovery path. An absent policy is the exact identity
behavior.

O2 is the independent post-outcome extension in `GatewayExtensions`. Gateway
composes its bounded runtime into the shared delivery service, but the observer
receives no Gateway, Coordinator, Channel, repository, retry, or checkpoint
authority. Each actual per-destination Coordinator attempt offers one detached
notification after Coordinator cleanup and the destination persistence attempt.
The same rule covers projection, interactive-request, and proactive delivery;
authorization/preflight failures, durable replay, O1 suppression, and completed
idempotency recovery did not make an attempt and are not observed. Absence is
behaviorally identical to the existing delivery path.

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
   The default process-local repository admits only its configured finite
   number of root records. At capacity it rejects a new identity before
   mutation or Channel work, while existing replay/CAS remains available; it
   never evicts idempotency evidence to manufacture capacity.
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

Under `foreground_only`, `BindConversationToThread` also prepares the desired
`(Conversation, Thread)` projection route before the binding compare-and-swap.
The route write is additive and preserves any existing checkpoint; it never
replaces another Conversation's route for the same Thread. A crash after route
preparation but before binding leaves an inactive route because foreground
resolution still requires the matching binding. Route bootstrap is fenced
before durable visibility and remains fenced through binding convergence, so
an existing Thread worker cannot deliver live output ahead of reconciliation.
A newly created route receives the normal bounded baseline without a false
missing-checkpoint gap. A retry that observes the same desired binding and
route converges to that postcondition without advancing the binding revision
only when its guard names the current or immediately preceding revision;
an existing route still uses checkpoint-directed recovery and reports a
missing checkpoint rather than silently downgrading to a new-route baseline.
A checkpoint-free route surviving a pre-CAS process crash is therefore allowed
to converge with an explicit bounded-recovery gap: it is not safely
distinguishable from an older checkpoint-free historical route after restart.
A baseline/recovery failure after binding keeps the prepared route fenced; a
newly started worker is stopped rather than allowing live output to advance the
checkpoint past unseen history. A same-target retry repeats recovery and opens
the route only after reconciliation succeeds. An unknown binding write outcome
also stays fenced when the repository cannot verify whether the CAS committed.
Invalid same-target revision guards are rejected before route preparation, so
they cannot release a fence retained by an earlier recovery failure.
A later different binding is never overwritten by the stale retry. Other
projection policies retain explicit `ObserveThread` route semantics.

## Failure and restart

Conversation mutations use revision guards and serialize per Conversation.
Foreground Thread binding prepares its inactive-until-bound route before the
binding CAS, so restart never exposes a completed binding with a missing
delivery edge. A same-target retry may converge a binding already advanced by
that operation; a different current target remains a conflict.
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
ADR 0015 adds one explicit opt-in exception to ordinary pre-dispatch release:
when an I2 inbound-failure presenter is configured, Gateway completes a
`pre_acceptance` claim before attempting the stable error delivery. That
consumer-selected presentation is terminal, so presenter or Channel failure
cannot turn a previously presented error into permission for later native
input. Without I2, the existing safe release-and-raise behavior is unchanged.
An `outcome_unknown` claim remains protected as `side_effect_started`, and a
`post_acceptance` claim remains terminal regardless of presentation outcome.
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

I2 receives `InboundFailurePhase` plus the original Conversation and reply
identity and a Gateway-derived stable delivery ID. It receives no exception
object, message content, free-form error text, claim handle, repository, or
dispatch callback. It returns one typed `OutboundMessage`; Gateway rejects a
changed Conversation, reply, or delivery identity before Channel side effects
and sends valid output through the common Coordinator/outbound-idempotency
path. Error wording, branding, retry guidance, and product UX remain consumer
policy over the fixed phase vocabulary.

Claim ordering is phase-specific. For `pre_acceptance`, configuring I2 makes
the failure terminal: Gateway completes the fenced claim before invoking the
presenter. Without I2 it retains the existing release-and-raise behavior. For
`outcome_unknown`, the pre-dispatch hook has already made the claim sticky as
`side_effect_started`; I2 does not complete or release it. For
`post_acceptance`, Gateway completes the claim before presentation as it did
before I2. Presenter validation, timeout, capacity, cancellation, or Channel
delivery failure is propagated but never changes these transitions or grants
permission for native input. Cancellation of the original pre-acceptance work
remains cancellation: Gateway releases the matching fenced claim and does not
invent user-visible failure delivery. If cancellation races after the durable
dispatch fence, Gateway preserves `side_effect_started` rather than treating
the work as proven pre-acceptance.

I2 presenter rendering is async and bounded by
`GatewayLimits.inbound_failure_present_timeout_seconds` and
`GatewayLimits.inbound_failure_present_max_concurrency`. Presenter output is
text-only with finite item and total-character limits; arbitrary metadata and
attachment sources are rejected before delivery. Active render tasks,
including cancellation overruns, retain finite capacity and receive bounded
shutdown cleanup. Fixed process-lifetime diagnostics count invocations,
success, failure, timeout, cancellation, cancellation overrun, and capacity
rejection without retaining exception text, identities, content, output, or
paths. Stable outbound idempotency handles duplicate presentation attempts;
the SDK adds no error transcript, durable presentation job, spool, or outbox.
With no presenter, no I2 task or diagnostics are fabricated and all prior
behavior is unchanged.

I1 receives the original frozen `InboundMessage`, not a context bag or Gateway
reference. Its return value becomes only `AgentInput.content`; Gateway still
derives the binding, route, client message ID, sender, reply correlation, and
default `prefer_active_turn` dispatch from the original envelope. A consumed
Controller input and a durable duplicate do not invoke I1. Invalid output,
exception, timeout, or cancellation happens before Application dispatch and
uses the existing fenced pre-side-effect release path. A later reclaim or
process restart can therefore invoke I1 again for the same stable inbound
identity; transformer implementations must be replay-safe and cannot treat an
invocation as an external side-effect or one-time signal. No transformed
content or invocation result is persisted.

I1 executes in the existing Channel inbound/admission task rather than a native
socket callback, and is bounded by
`GatewayLimits.inbound_content_transform_timeout_seconds` and
`GatewayLimits.inbound_content_transform_max_items`, with active transformer
tasks capped by `GatewayLimits.inbound_content_transform_max_concurrency`.
Capacity exhaustion fails explicitly before dispatch. Gateway cancellation
propagates into the transformer. Deadline cleanup gets one equally bounded
cancellation grace; a transformer that still ignores cancellation cannot keep
the Conversation lock or claim, is cancelled again, and its eventual result is
discarded. Overrun tasks remain tracked against that finite capacity and get
another bounded cancellation pass during Gateway shutdown. Its process-
lifetime diagnostics contain only fixed invocation, success, failure, timeout,
cancellation, cancellation-overrun, and capacity-rejection counters plus a
fixed last-failure code; they retain no exception text, inbound identity,
content, path, or return value. With no transformer, Gateway neither invokes
nor times this position and all prior behavior is unchanged.

On restart, Gateway rebuilds required Thread projection workers from persisted
routes and reconciles from authoritative Application history/catch-up plus
per-route completion checkpoints. New routes receive only a configured
recent/active baseline. Existing routes scan newest pages toward their
checkpoint under strict configured bounds; a missing checkpoint is explicit
degraded health. Gateway never loads an SDK transcript.

Gateway also exposes a synchronous diagnostics snapshot of its process-local
infrastructure. The stable surface aggregates configured Application and
Channel identity/kind, optional bounded connection/queue facts, projection
health, startup admission, and configured I1 execution facts without native resource, Thread,
Conversation, route, error-text, or message identities. Configured registry
identity wins over optional provider output, and an absent, raising, invalid,
or mismatched provider degrades to identity-only facts. Collection performs no
repository/native I/O and remains explicitly non-authoritative; consumer
health rendering and export are outside Gateway.

During `start()`, claimed inbound messages are admitted into one bounded,
process-local FIFO until durable projection routes have been restored. Typed
operations enter only through Controller actions or the public Gateway surface
and are not Channel startup callbacks. The FIFO prevents a Channel that
immediately produces input from racing restoration while preserving message
arrival order. Overflow fails startup explicitly and normal teardown
cancels/joins owned component work; no inbound mutation is silently discarded.
`GatewayLimits.startup_buffer_max_pending` configures this bound.
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
`GatewayLimits.turn_acceptance_event_max_pending` independently bounds events consumed while
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
`conversation.respond_request` through a consumer Controller's typed actions
or the public typed Gateway execution surface, never through Channel startup.
Gateway then:

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

## Package placement

The stable `imagent.gateway` public surface is the target package at
`src/imagent/gateway/__init__.py`. The initial package-establishment slice moves
the existing `ImAgentGateway` implementation there unchanged so later focused
PRs can extract one documented leaf at a time. The package initializer is
temporarily the same explicit composition/runtime split candidate; it is not a
facade over a second implementation. Public constructors, exported object
identity, defaults, ordering, claims, checkpoints, and shutdown semantics do
not change merely because the module became a package.

## Change obligations

Changes to the Gateway package root require checking:

- projection/recovery design when subscription, routing, or recovery changes;
- persistence design when stored bridge state changes;
- Controller design when the inbound extension seam changes;
- Gateway operation and vertical-slice tests;
- all fake, Codex, Zen, T3, and Channel seams affected by orchestration.
