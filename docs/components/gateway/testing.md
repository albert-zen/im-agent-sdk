# Gateway testing

## Critical scenarios

- the target `imagent.gateway` package preserves the exact public export set
  and one implementation identity while the historical single-file module is
  absent;
- repository, limit, and extension composition groups are immutable, typed,
  and preserve all former defaults while the removed flat keywords fail
  explicitly;
- default delivery-submission persistence receives the finite positive
  `GatewayLimits` record bound; invalid configuration fails during construction,
  capacity failure precedes Channel work and releases any outer idempotency
  claim, while an injected repository is unchanged;
- default idempotency persistence receives its finite positive `GatewayLimits`
  record bound; a new identity fails explicitly before inbound media/native
  dispatch or outbound planning/Channel work, while an existing stable identity
  retains replay/join and owner-fenced transitions at the bound without
  eviction or retry permission;
- Conversation serialization receives its finite positive `GatewayLimits`
  active-key bound, retains no completed key, permits same-key waiters at
  capacity, rejects a new key before side effects, and releases cancelled
  owners/waiters exactly once;
- the dependency-neutral keyed registry has one canonical internal owner,
  rejects invalid bounds, preserves equal-key joins and independent-key
  progress, cleans owner/waiter cancellation exactly once, and leaves no
  historical root import or policy-specific dependency;
- registries without an independent key limit remain inside a proved finite
  enclosing admission: Coordinator destination keys cannot exceed reserved
  `max_pending` work, and request keys cannot exceed admitted Conversation
  operation lanes;
- active Thread observation receives its finite positive `GatewayLimits` bound,
  joins same-Thread starters and waiters at the final slot, rejects only a
  distinct Thread before Application subscription/recovery/checkpoint or
  delivery work, retains a same-Thread admission across task turnover, and
  releases terminal/cancelled/start-failed worker slots/health without clearing
  a pending acceptance-ordering gate before its final owner drains it; restart retains
  its existing acceptance-buffer reset boundary; a full foreground switch does
  not mutate the existing binding or persist the rejected candidate route;
- grouping does not change binding, idempotency, projection recovery,
  request-correlation, Controller/Presenter, Coordinator, proactive-delivery,
  startup, or shutdown identity and ordering;
- grouped Gateway composition preserves every existing no-extension behavior;
- each configured extension runs only at its ADR 0015 position with stable
  identity, bounded lifetime, explicit cancellation, and fixed diagnostics;
- inbound content transformation cannot change envelope, admission, binding,
  client-message identity, prefer-active-Turn dispatch, or correlation policy;
- I1 accepts bounded text/image/file content, rejects non-tuple, empty,
  unsupported, and oversized output, and releases its fenced claim on invalid
  output, exception, timeout, or cancellation before any Application dispatch;
- I1 is bypassed for Controller-consumed and durable-duplicate input, may run
  again after a confirmed pre-dispatch reclaim/restart, and records only fixed
  redacted process-lifetime diagnostics;
- inbound failure presentation cannot reopen a pre-dispatch, unknown, or
  post-acceptance input claim;
- configured I2 completes a fenced `pre_acceptance` claim before presentation,
  while absent I2 releases and re-raises; original cancellation also releases
  without presentation;
- `outcome_unknown` remains `side_effect_started` and `post_acceptance` remains
  terminal across presenter validation/failure/timeout/cancellation, Channel
  failure, duplicate delivery, and restart;
- I1 failures reach I2 as bounded `pre_acceptance` facts; presenter output
  cannot change Conversation, reply, or stable delivery identity and fixed
  diagnostics retain no exception text or callback output;
- I2 rejects attachment authority, arbitrary metadata, empty text, and output
  exceeding its finite item or total-character bounds before Channel delivery;
- cancellation before the native dispatch fence releases the claim, while
  cancellation after the fence preserves `side_effect_started` across restart;
- destination presentation is projection-only, receives a bounded typed
  authoritative/live-only origin, cannot change delivery, destination, reply,
  time, or attachment authority, and its owner returns bounded revalidated
  presented/suppressed/failed output before planning;
- per-destination O1 decisions are independent; the idempotency owner completes
  durable suppression before checkpoint CAS, completed recovery bypasses O1,
  live-only suppression never checkpoints, and that owner releases only the
  matching pre-side-effect failure claim;
- a post-outcome observer runs once per logical attempt rather than per segment
  or internal retry, observes fixed typed receipt/error outcomes only after
  Coordinator cleanup and destination persistence, and cannot rewrite a
  receipt, retry, persistence, shutdown, or cleanup ordering;
- O2 capacity, lifetime, cancellation, and diagnostics are bounded; durable
  replay, preflight failure, completed recovery, and O1 suppression do not
  fabricate attempts, and absence preserves every delivery origin;
- no extension callback runs on a Channel/Application socket read path or
  creates a second native event subscription;
- resource listing never mutates Conversation binding;
- binding a Thread never activates native Application state;
- a Controller and a native interaction use the same typed action surface;
- Slash and native-action request responses create the same Application
  `request.respond` operation;
- request responses require an actual successful destination correlation and
  distinguish unauthorized destination, duplicate/responded, resolved, and
  stale outcomes;
- request-scoped concurrency converges on the native first writer across
  multiple destinations;
- a slow destination completing after the first writer inherits `responded`
  instead of creating a new `open` route;
- a no-snapshot Application request emitted during `start()` is observed
  after restored subscriptions are installed and remains answerable;
- binding selection changes never retarget a previously delivered request;
- live observation is established before synchronous native notifications;
- duplicate inbound messages and duplicate outbound items are idempotent;
- durable inbound duplicates are rejected before Channel attachment
  preparation, including after SQLite restart;
- preparation failure and startup failure release only the matching fenced
  pre-side-effect claim, cancellation during pre-handoff fencing also
  releases, and stale owners cannot enter Gateway processing;
- startup rollback closes admission before its first await, so an event racing
  buffered-claim release cannot reach Controller or Application work;
- startup buffering stays active through queued-message drain, so an event
  racing a drain failure joins rollback instead of live processing;
- correlation or projection-drain failure after `AcceptedTurn` keeps inbound
  idempotency terminal, while a failure before native acceptance remains
  retryable;
- cancellation or response loss after native input dispatch and failure of a
  post-acceptance terminal write remain sticky across redelivery/restart;
- stale outbound leases are reclaimed, while an already-accepted durable
  submission converges without sending a second Channel message;
- proactive delivery rejects unscoped targets before staging or routing;
- explicit Conversation and policy-resolved Thread targets both work;
- route snapshots remain pinned across route movement and restart;
- concurrent reuse of one delivery ID sends once, while mismatched reuse is a
  conflict;
- concurrent new delivery IDs at the last default-memory record slot have one
  winner and one explicit capacity failure without evicting replay evidence;
- an external principal named like the Gateway-internal principal still has a
  distinct SDK-controlled submission namespace;
- Thread-targeted results redact resolved Conversation IDs and aggregate or
  per-item native message IDs;
- public proactive `LocalPath` input requires a stable SHA-256 digest;
- unsupported attachment source/count/size rejects every destination before
  side effects;
- per-artifact failures and multi-destination failures remain typed partial
  results, and ambiguous outcomes are never resent automatically;
- the optional JSON ingress removes staged bytes and the reference CLI refuses
  non-loopback endpoints;
- slow Channel delivery does not await/block the native event producer;
- startup claimed-message admission is FIFO and bounded, overflow fails
  startup, and teardown leaves no owned work running;
- Gateway passes every SDK-owned Channel its exact admission handler once;
  one-argument adapters fail before their body, internal startup `TypeError`
  is not retried, and rollback/restart preserve exact stop counts without
  inbound or Application work;
- callbacks racing failed startup or shutdown are explicitly rejected rather
  than reaching a stopping Application;
- Application event overflow is visible in bounded health and recovers only
  the affected Thread without restarting unrelated Applications;
- stable diagnostics aggregate Application/Channel, worker, and startup
  admission facts without exposing native resource, Thread, route, or error
  identities or mutating runtime state; absent, raising, malformed, or
  identity-mismatched Channel providers fail closed;
- concurrent Threads and Turns do not steal events or routes;
- restart rebuilds projection from routes plus bounded authoritative history;
- started/steered reply correlation is per Turn and destination-safe across
  two Conversations, persistence, and restart;
- foreground restart/switch reclaims and restores the correct worker;
- foreground bind prepares an additive route before CAS, converges a
  same-target crash retry, leaves a pre-CAS route inactive, preserves two
  Conversations on one Thread, fences live output until baseline completion,
  avoids false missing-checkpoint degradation for a new route, and never
  overwrites a later different binding;
- failed foreground baseline keeps live output fenced and cannot advance a
  checkpoint until a same-target recovery retry succeeds;
- an unverified binding-write outcome fails closed with its route fenced, and
  an invalid same-target revision cannot release a retained recovery fence;
- Application subscription failure self-recovers while one destination
  failure remains isolated and visible;
- attachment and delivery failures stay explicit.

Run:

```sh
uv run python -m unittest discover -s tests -p "test_gateway*.py" -v
uv run python -m unittest discover -s tests -p "test_projection*.py" -v
uv run python -m unittest tests.gateway.projection.test_observation -v
uv run python -m unittest tests.gateway.test_lifecycle -v
uv run python -m unittest tests.gateway.delivery.test_proactive_delivery tests.gateway.delivery.test_proactive_ingress -v
```

Also run the full adapter contract suite after changing a Gateway-facing port.
