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

| Component | Owns | Does not own | Detail |
|---|---|---|---|
| Contracts | language-neutral resources, messages, operations, capabilities, events, validation | Python runtime implementations | [design](components/contracts/design.md) |
| Python Ports | runtime interfaces for integrations and repositories | semantic or native behavior | [design](components/ports/design.md) |
| Gateway | deterministic input routing and composition | Agent or Channel truth | [design](components/gateway/design.md) |
| Projections and recovery | live observation, routes, authoritative reconciliation mechanics | transcript/event journal | [design](components/projections-and-recovery/design.md) |
| Persistence | minimal Gateway-owned state | Agent transcript/Turn/request state | [design](components/persistence/design.md) |
| Attachments and media | explicit source/trust/materialization boundary | universal blob store or product media policy | [design](components/attachments-and-media/design.md) |
| Delivery planning and coordination | pure capability plans, per-destination FIFO, bounded execution and honest receipts | native platform encoding, durable jobs, or product retry policy | [design](components/delivery-planning-and-coordination/design.md) |
| Controllers | optional common UX over typed Operations | Core semantics or binding authority | [design](components/controllers/design.md) |
| Channel integrations | native admission, rendering, media, delivery, reconnect | Agent resources/execution | [design](components/channel-adapters/design.md) |
| Application integrations | native resource/input/event/history/request translation | native resource/execution ownership | [design](components/application-adapters/design.md) |
| Diagnostics | stable redacted process-local infrastructure facts | Agent health authority, exporter, endpoint, or operator UX | [design](components/diagnostics/design.md) |
| Testing and conformance | reusable fakes and honesty suites | production runtime | [design](components/testing-and-conformance/design.md) |

Repository maintainability is an operational AgentKit component, not runtime
architecture. See
[its design](components/repository-maintainability/design.md).

## Dependency direction

Contracts are the semantic bottom. Python Ports depend on Contracts.
Persistence, Controllers, media helpers, and eventing are lower services.
Concrete integrations depend inward. Gateway is the high-level composition
root. Test helpers depend on public Contracts/Ports, never the reverse.

The precise current import graph and allowed edges are documented in
[dependency rules](architecture/dependency-rules.md) and enforced by
`python scripts/agentkit.py lint-architecture`.

Components describe responsibility; lint layers describe imports. They need
not have identical names.

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
`thread.activate_native` optionally changes native UI state. No one mutation
implies another.

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
  overflow is an explicit per-Thread gap that enters authoritative recovery.
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
- `components/<name>/`: local design, protocol where needed, and tests.
- [Reuse](REUSE.md): provenance and transfer constraints.
- [Roadmap](ROADMAP.md): future/unresolved work only.
