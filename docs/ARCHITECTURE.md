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
  Optional delivery ingress ← scoped local tool/Artifact submission
```

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
2. Access policy and duplicate rejection run before media work.
3. Optional Controller translates UX into typed actions.
4. Gateway derives/preserves a stable client message ID.
5. Gateway resolves the Conversation binding.
6. It records/refreshes output observation and establishes live subscription
   before sending input.
7. Application accepts input and emits authoritative user/Agent events.
8. Projection resolves current destinations at delivery time.
9. Delivery planning maps the logical message to deterministic segments.
10. The Coordinator executes them through the Channel's ordered bounded lane.
11. Channel performs native encoding/delivery and returns typed receipts.

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
- `AcceptedTurn` creates minimal per-Turn reply correlation. Only a matching
  destination may use it; external/recovered Turns without a correlation use
  no reply unless the route carries an explicit destination/topic default;
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

## Failure model

Failures are explicit and scoped: unsupported capability, invalid/stale
reference, access/authentication, missing binding, unavailable Application,
rejected operation, attachment source/trust, delivery outcome, worker health,
bounded-admission overflow, gap, and cursor expiration.

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
