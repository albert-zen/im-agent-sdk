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
| future projection completion checkpoint | Gateway target in #14 | permitted only when it carries no Agent content/truth |
| inbound/outbound idempotency | Gateway | yes |
| Channel reconnect token | Channel integration | yes as native transport state |
| Agent replay cursor | Agent Application | opaque projection checkpoint only |
| Channel credentials | Channel deployment | external configuration |

Deleting rebuildable discovery/content caches must not lose Agent truth:
authoritative history can reconstruct their content. Delivery idempotency,
routes, and future completion checkpoints are owned bridge state, not
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
9. Channel performs native rendering/delivery and returns a receipt.

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

The current Gateway can rebuild observation after restart from routes and
authoritative history, but its first-observe/restart scan is not bounded and
has no projection completion checkpoint. This is a known gap, not current
contract truth.

The target invariant is a live baseline plus bounded recent/active catch-up.
Full archive delivery is an explicit history operation/Controller UX. A
Gateway-owned completion boundary may bound missed-output reconciliation but
must contain no transcript or Turn truth. This work is tracked by
[Issue #14](https://github.com/albert-zen/im-agent-sdk/issues/14).

## Concurrency and worker lifecycle

### Current behavior

- Conversation binding mutations serialize per Conversation.
- Gateway does not manufacture a cross-Conversation input sequence; native
  Application acceptance and execution ordering remains authoritative.
- native event publication fans out without awaiting Channel delivery.
- independent subscriber queues prevent observers from stealing events.
- Gateway stores durable routes and uses delivery idempotency.
- `_ensure_projection` records a newly created worker before its first
  suspension point, so callers on the Gateway event loop cannot interleave
  lookup and registration. The single-worker invariant lacks a direct
  concurrency regression test. Gateway lifecycle entrypoints currently assume
  execution on their owning event loop; a future cross-thread API would need
  an explicit synchronization boundary.

### Target invariants and known gaps

- the current one-worker event-loop invariant should have direct concurrency
  coverage and remain explicit if lifecycle code changes;
- a worker failure should be observable and supervised, but current workers
  exit and wait for another trigger;
- observation should be reclaimed when no route/recovery policy needs it, but
  current route removal does not tear down a worker;
- per-Turn reply correlation must differ from long-lived route/topic context,
  but current route state can be overwritten by the latest inbound message;
- first-observe and restart reconciliation must be bounded, but current code
  can scan the complete archive.

These gaps are tracked by
[Issue #14](https://github.com/albert-zen/im-agent-sdk/issues/14).

Subscriber queues are currently unbounded and projection delivery awaits
Channel send. This protects native notification callbacks from slow IM but
does not provide bounded memory/backpressure. The target Delivery Coordinator
is tracked by [Issue #12](https://github.com/albert-zen/im-agent-sdk/issues/12);
it is not implemented by the current Gateway.

## Failure model

Failures are explicit and scoped: unsupported capability, invalid/stale
reference, access/authentication, missing binding, unavailable Application,
rejected operation, attachment source/trust, delivery outcome, worker health,
gap, and cursor expiration.

Core does not choose consumer retry counts, permission prompts, Full Access,
or hidden self-healing policy. Safe infrastructure may expose health and
idempotent recovery; adapters and consumers retain native/product policy.

## Security and policy boundaries

Keep these separate:

1. IM sender/Conversation access — Channel or consumer policy.
2. Agent Application authentication — Application integration/deployment.
3. native approval/user-input truth — Agent Application.
4. sandbox/Full Access — native Application or consumer policy.
5. media filesystem/network trust — explicit deployment configuration.

An IM allowlist never grants execution permission. Full Access never bypasses
IM admission. Attachments cannot grant shared-filesystem trust.

## Documentation authority

- [Vision](VISION.md): purpose, non-goals, Core admission.
- this document: system map, ownership, dependencies, flows.
- [accepted ADRs](decisions/README.md): reviewed cross-component decisions.
- `components/<name>/`: local design, protocol where needed, and tests.
- [Reuse](REUSE.md): provenance and transfer constraints.
- [Roadmap](ROADMAP.md): future/unresolved work only.
