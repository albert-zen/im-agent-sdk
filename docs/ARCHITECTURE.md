# Architecture

## System shape

```text
IM Channels
  ↓ native events / actions
Channel integrations
  ↓ InboundMessage / OutboundMessage / typed interaction
Gateway ─── optional Controllers
  ↓ Python Ports carrying language-neutral Contracts
Agent Application integrations
  ↓ native resource, input, event, history, request APIs
Zen · T3 · Codex · other Agent Applications

Gateway also composes:
  Persistence ← bindings, routes, idempotency
  Projections and recovery ← live fan-out, reconciliation, checkpoints
  Attachments/media ← explicit source and trust boundary
  Delivery planning/coordination ← capability plans, ordered bounded sends
  Diagnostics ← redacted process-local facts, optional adapter providers
  Optional delivery ingress ← scoped local tool/Artifact submission
```

The optional I1 inbound-content transformer is a Gateway-owned, consumer-
implemented typed protocol. It can replace only the `Content` tuple of a
verified, Controller-unconsumed `InboundMessage`; the verified envelope and
all routing, admission, continuation, dispatch, and correlation decisions stay
inside Gateway.

The optional I2 inbound-failure presenter is a separate Gateway-owned,
consumer-implemented typed protocol. It receives only a fixed bounded failure
phase plus Gateway-fixed Conversation, reply, and delivery identities; it
never receives the exception, claim, repositories, or dispatch authority.

A1 non-artifact presentation is configured only on the concrete Application
adapter that owns the native shape. Codex live activity and T3 recoverable
activity use separate typed protocols over bounded normalized facts; neither
receives a raw notification or Application client. Adapters fix native
item/event, Thread, Turn, role, and namespaced metadata identity and accept only
bounded text presentation. Codex live-only output is `message.created` and
never advances a completion checkpoint. T3 output is `message.completed` only
because the same association is reproduced through authoritative history.

App Server artifact materialization is a separate concrete A1 protocol. The
adapter derives frozen completed-item and terminal facts plus stable untrusted
artifact-candidate identities in its existing ordered live/history path. A
consumer may return only bounded typed `AttachmentContent`; the adapter fixes
message, Thread, Turn, role, event, and recovery identity. Candidate locators
confer no filesystem or URL trust, and the SDK acquires no bytes, spool,
quota, lease, cleanup ledger, or second native subscription.

IM is another access surface over the native Agent Application. It is not a
separate Agent state tier.

## Product components

The approved runtime architecture has three ownership layers. Existing broad
documentation groups remain navigation evidence while focused leaf docs and
modules are migrated; they are not a fourth ownership model.

| Layer | Owns | Does not own | Detail |
|---|---|---|---|
| Interaction | message/content/operation contracts, Controller contracts and command composition, Channel ingress and delivery | Gateway orchestration or native Agent truth | [component tree](components/README.md#interaction) |
| Gateway | composition, admission, binding/routing, input dispatch, projection/recovery, delivery, bridge persistence, diagnostics | product commands, Channel transport truth, Agent transcript/runtime | [component tree](components/README.md#gateway) |
| Applications | Application contracts/capabilities/events/operations/requests, native presentation, and concrete Agent adapters | Conversation binding, IM delivery, product policy | [component tree](components/README.md#applications) |

Engineering support owns conformance, language-neutral schema validation/navigation,
repository maintainability, AgentKit, and release mechanics outside the runtime dependency graph. The complete leaf
inventory, current/target paths, public exports, tests, decisions, and known
split candidates live in the machine-readable
[component map](components/component-map.yml).

## Dependency direction

Versioned language-neutral schemas bind the applicable owning leaves and remain
the semantic source of truth. Interaction message/operation contracts are the
lowest runtime surface. Applications implement their typed native boundary without importing Gateway. ControllerActions
and SDK common commands may consume only the exact public typed Application/Gateway
contracts and passive ConversationBinding value recorded in the component map. A Controller request
presenter may consume the public typed Application request contract. Those exact
contract exceptions are recorded in the component map; they do not allow
Controller or Channel code to import an Application or Gateway implementation.
Channel implementations stay inside Interaction
and import neither Gateway nor concrete Applications. Gateway is the
composition root and may call both lower-facing contract surfaces; neither
lower layer calls Gateway implementation. Test helpers depend on public
contracts, never the reverse.

The precise current import-resolution rules and allowed component edges are documented in
[dependency rules](architecture/dependency-rules.md) and enforced by
`python scripts/agentkit.py lint-architecture`.

The component map is the ownership authority for both navigation and the
target import graph. During the mechanical rollout, lint resolves current
imports through the owners and public exports recorded by that map. Split
candidates provide candidate owner pairings, never an automatic exemption;
any cross-layer exception remains exact and explicit.

Gateway composition values have one current owner:
`imagent.gateway.composition`. The `imagent.gateway` package facade re-exports
those exact objects while it still combines its public facade with
`ImAgentGateway` runtime orchestration; that remaining package-root split is
tracked explicitly in the component map and creates no duplicate composition
implementation.

## Authority and persistence

| State | Authority | SDK persistence |
|---|---|---|
| Application registry | deployment/consumer | configuration |
| Project/Thread registry | Agent Application | cache only |
| transcript and completed messages | Agent Application | rebuildable projection only |
| Turn/request/execution/status | Agent Application | rebuildable projection only |
| Conversation input binding | Gateway | yes |
| Thread projection route | Gateway | routing fields only |
| projection completion checkpoint | Gateway | per destination route; stable Agent item ID and time only |
| Turn-to-IM reply correlation | Gateway | minimal active-Turn bridge identity with bounded retention |
| inbound/outbound idempotency | Gateway | yes |
| proactive route snapshot/outcome | Gateway | identity and receipt only; never content |
| Channel reconnect token | Channel integration | yes as native transport state |
| Agent replay cursor | Agent Application | opaque projection checkpoint only |
| Channel credentials | Channel deployment | external configuration |

Deleting rebuildable discovery/content caches must not lose Agent truth:
authoritative history can reconstruct their content. Delivery idempotency,
routes, completion checkpoints, and reply correlations are owned bridge state, not
disposable transcript caches; losing them may require explicit degraded
recovery rather than pretending the prior delivery boundary is known.

Inbound admission uses the same Gateway-owned idempotency state before Channel
media preparation. Its fenced `in_flight` lease stores identity and ownership
only; media content, paths, and sender data remain outside persistence.
I1 runs only after that lease is refreshed and the optional Controller has
declined the input. Its result is process-local and is never persisted: a
confirmed pre-dispatch release or restart may invoke it again for the same
stable inbound identity, so consumer implementations must be replay-safe.
With I2 configured, a known pre-acceptance failure is completed before stable
error presentation. An unknown dispatch outcome stays `side_effect_started`
and a post-acceptance failure stays terminal. Presenter or Channel failure
cannot reopen any of those claims. Without I2, the prior pre-dispatch release-
and-raise behavior is unchanged.

## Input selection and output routing

The default binding key is:

```text
(channelInstanceId, conversationId)
```

The `ConversationBinding` answers where the next input goes. The
`ThreadProjectionRoute` answers where completed output may be delivered.
`thread.activate_native` optionally changes native UI state. No mutation
activates another. The narrow `foreground_only` consistency exception is that
Thread binding prepares the additive Conversation route before binding CAS;
the route has no output authority before that matching binding or after a
later switch. Other projection policies keep binding and observation as
explicitly composed mutations.

Projection policies:

- `foreground_only`;
- `remembered_last_recipient`;
- `all_observers`.

Per-user group selection is a future consumer policy, not current Core.

## Input flow

1. Channel verifies/authenticates and normalizes native input.
2. Access policy runs before media work.
3. Channel requests a fenced Gateway-owned durable admission lease from the
   stable Conversation/message identity; duplicates stop before media work.
4. Channel stages permitted media, then hands the completed message and lease
   to Gateway. Preparation failure releases only the owned pre-side-effect
   lease.
5. Optional Controller translates UX into typed actions.
6. Gateway derives/preserves a stable client message ID.
7. Gateway resolves the Conversation binding.
8. It records/refreshes output observation and establishes live subscription
   before sending input.
9. Gateway supplies the default `prefer_active_turn` input preference. The
   adapter declares `started/create_new` or `steered/preserve_existing`
   immediately before native dispatch; Gateway authorizes the correlation
   policy, then the Application accepts input and emits authoritative events.
10. Projection resolves current destinations at delivery time.
11. Delivery planning maps the logical message to deterministic segments.
12. The Coordinator executes them through the Channel's ordered bounded lane.
13. Channel performs native encoding/delivery and returns typed receipts.

## Typed extension positions

Consumer and adapter policy may extend the single bridge path only at the
typed positions governed by ADR 0015:

```text
verified/admitted input
  -> optional Controller
  -> inbound content transform
  -> binding + prefer-active-Turn dispatch
  -> classified inbound failure presentation

native Application event/history
  -> adapter-owned normalized presentation/materialization
  -> canonical projection
  -> destination-specific presentation
  -> common logical delivery
  -> post-outcome observation
```

The positions are separate effect contracts, not one middleware chain.
Extensions cannot change binding, delivery/destination identity, input
continuation, Turn/request authority, checkpoints outside their documented
decision, or native retry safety. They receive bounded typed values and never
raw protocol envelopes or a mutable Gateway context. Missing extensions leave
the existing path unchanged.

Side-effect-free Channel startup validation and read-only diagnostic providers
are adapter capabilities outside this message path. Core fidelity fixes, such
as preserving normalized message Metadata, are not modeled as consumer hooks.

## Proactive output flow

```text
Agent task / operator tool
  → scoped credential + stable delivery ID + Thread target
  → optional loopback JSON ingress
  → Gateway authorization and projection-policy route resolution
  → immutable destination snapshot + pure capability plan
  → common ordered/bounded Delivery Coordinator
  → native Channel segment send
  → typed destination and per-artifact receipts
```

The first submission pins its destination set. Repository identity is
namespaced by an SDK-controlled external/internal origin and trusted principal,
so callers cannot poison another principal or impersonate Gateway projection.
Retries cannot follow a moved route or turn an ambiguous outcome into a silent
resend. The reference ingress stages inline files only after authorization and
does not disclose resolved native Conversation or message IDs.

## Recovery flow

With native replay:

```text
subscribe after opaque cursor
→ consume ordered native events
→ surface explicit gap/expiration
→ reconcile authoritative history when required
```

Without replay, the current recovery primitive establishes a live subscription
and reads authoritative history:

```text
subscribe first
→ read authoritative catch-up/history
→ reconcile stable IDs
→ consume live events
```

Gateway rebuilds observation after restart from routes and authoritative
history. A new route reads a configured recent/active baseline rather than a
complete archive. An existing route scans newest pages toward its
per-destination completion checkpoint under configured page and item bounds.
Missing/expired checkpoints remain visible as degraded worker health. Full
archive reading is an explicit history operation/Controller UX; the checkpoint
contains no transcript or Turn truth.

## Concurrency and worker lifecycle

### Current behavior

- Conversation binding mutations serialize per Conversation.
- Gateway does not manufacture a cross-Conversation input sequence; native
  Application acceptance and execution ordering remains authoritative.
- The default input preference is to continue an active Turn. Every
  Application adapter reports whether the native result was actually
  `started` or `steered`; Codex may use native steer, while T3 and Zen
  currently start because their evidenced protocols do not provide that
  mutation.
- native event publication fans out without awaiting Channel delivery.
- independent subscriber queues prevent observers from stealing events.
- Gateway stores durable routes and uses delivery idempotency.
- projection, interactive presentation, and proactive output share one
  capability-driven Delivery Coordinator;
- work is FIFO per Conversation, concurrent across unrelated Conversations,
  and admitted under bounded global/per-destination capacity;
- `_ensure_projection` records a newly created worker before its first
  suspension point, so callers on the Gateway event loop cannot interleave
  lookup and registration. Concurrent-observer coverage protects this
  one-worker invariant. Gateway lifecycle entrypoints currently assume
  execution on their owning event loop; a future cross-thread API would need
  an explicit synchronization boundary.
- active Application observation workers have one positive, process-local
  `GatewayLimits.projection_max_active_threads` bound keyed by stable
  `ThreadRef`. A running, starting, or in-flight same-Thread admission joins
  before capacity is considered; a distinct Thread at the limit fails
  explicitly before subscription, authoritative reconciliation/checkpoint,
  presentation, or Channel delivery. The in-flight admission lease preserves
  that identity across task turnover until its caller reaches its `finally`.
  Terminal/cancelled workers discard process-local health and normally release
  their slot; an accepted-input acceptance-ordering gate, its event lock, and
  buffered events remain until the final input owner durably resolves the
  correlation and drains them. The distinct native side-effect fence is entered
  only by the Application immediately before native mutation. Supervised
  resubscription keeps its existing slot and
  recovery authority.
- subscription/recovery failure enters bounded-backoff resubscription and is
  visible in process-local worker health;
- `foreground_only` reclaims workers with no active binding route and restores
  the bound route after restart; remembered/all-observer policies retain
  workers while their routes exist;
- a `started` acceptance creates minimal per-Turn reply correlation, while a
  `steered` acceptance preserves the correlation authorized before dispatch.
  Reusing the Thread/Turn key can never replace its original Conversation or
  reply ID. Only a matching destination may use it; external/recovered Turns
  without a correlation use no reply unless the route carries an explicit
  destination/topic default;
- per-route bootstrap barriers serialize bounded baseline before queued live
  delivery decisions without blocking native event publication;
- one route's Channel failure blocks that route's later delivery decisions,
  records its route ID in worker health, and does not restart the Application
  subscription.
- Application event subscribers, Gateway startup admission, and per-Thread
  Turn-acceptance buffering have independently configurable finite limits;
  overflow or a failed ordered drain is an explicit per-Thread gap that enters
  authoritative recovery without reopening accepted input.
- The Gateway startup-admission FIFO and its explicit overflow/not-running
  failures are implemented by the `gateway.lifecycle` leaf. The
  `ImAgentGateway.start()`/`stop()` orchestration remains at the
  `imagent.gateway` package root as an explicit physical migration gap; its
  ordering is unchanged.
- native pending-request snapshots reconcile request events after a gap when
  supported; otherwise worker health truthfully retains an interactive-request
  recovery degradation instead of manufacturing request state.
- Gateway exposes a synchronous non-authoritative diagnostics snapshot. It
  aggregates projection facts without Thread/route/error identities and reads
  optional adapter providers without native I/O. App Server reports its
  connection epoch and bounded dispatch lanes; T3 truthfully reports no
  long-lived connection model.

## Failure model

Failures are explicit and scoped: unsupported capability, invalid/stale
reference, access/authentication, missing binding, unavailable Application,
rejected operation, attachment source/trust, delivery outcome, worker health,
bounded-admission overflow, gap, and cursor expiration.

Diagnostics preserve only fixed classifications and aggregate counters.
OpenTelemetry export, `health.json`, polling, labels, alerting, and product
degraded-state UX are consumer policy.

Core does not choose product retry counts, permission prompts, or Full Access.
Safe Application resubscription is bounded infrastructure recovery. Delivery
defaults to one attempt; only a Channel's explicit retryable receipt can be
retried when consumer configuration opts in. Unknown outcomes are never
retried.

## Security and policy boundaries

Keep these separate:

1. IM sender/Conversation access — Channel or consumer policy.
2. Agent Application authentication — Application integration/deployment.
3. native approval/user-input truth — Agent Application.
4. sandbox/Full Access — native Application or consumer policy.
5. media filesystem/network trust — explicit deployment configuration.
6. proactive-delivery authority — opaque capability scoped to explicit
   Threads/Conversations, independent from IM admission and Agent sandbox.

An IM allowlist never grants execution permission. Full Access never bypasses
IM admission. Attachments cannot grant shared-filesystem trust.

## Documentation authority

- [Vision](VISION.md): purpose, non-goals, Core admission.
- this document: system map, ownership, dependencies, flows.
- [accepted ADRs](decisions/README.md): reviewed cross-component decisions.
- `components/<layer>/<leaf>/`: local design, testing, and focused supporting
  documents where needed.
- `engineering/<leaf>/`: repository-support design and testing for conformance,
  schemas, maintainability, AgentKit, and release mechanics.
- `components/component-map.yml`: machine-readable ownership, current/target
  paths, exports, tests, ADRs, and structural gaps.
- [Reuse](REUSE.md): provenance and transfer constraints.
- [Roadmap](ROADMAP.md): future/unresolved work only.
