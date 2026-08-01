# ADR 0011: Durable inbound admission before media work

Status: Accepted

## Context

Every native Channel can redeliver one authenticated message, including after
the SDK process restarts. Channel integrations already apply access policy and
a process-local duplicate check before attachment download, but Gateway
acquires its durable inbound idempotency claim only after the Channel has
materialized media and emitted a complete `InboundMessage`.

That ordering protects the later Agent mutation but permits a restart
redelivery to repeat network download, filesystem staging, quota consumption,
and parsing. Moving durable admission earlier must not make Channel adapters
depend on Gateway, persist message content, or let a stale preparation worker
release or use a replacement worker's claim.

## Decision

### Channel and Gateway share an opaque admission lease

The Python Channel Port accepts an `InboundAdmissionHandler` in addition to
the completed-message and operation callbacks. After native authentication,
stable identity normalization, and access policy, a Channel requests admission
with only:

- its configured `ConversationRef`; and
- the stable native message ID.

Gateway derives the existing inbound idempotency scope/key, creates a fresh
opaque owner token, and claims the configured `IdempotencyRepository`. An
already-completed or currently in-flight identity returns no lease, so the
Channel performs no attachment preparation and emits no message.

An acquired `InboundAdmission` is an opaque one-shot lease. The Channel may:

- release it when normalization or media preparation fails before handoff; or
- deliver exactly one completed `InboundMessage` whose identity matches the
  admitted identity.

The lease refreshes and verifies its fenced repository ownership immediately
before handoff. Gateway verifies ownership again after any startup buffering
and after acquiring the per-Conversation processing lock, before Controller or
binding work. A worker whose reclaimable lease was replaced while it prepared
media or waited to run therefore cannot enter Gateway processing. After
handoff begins, Gateway owns every terminal transition; the Channel must not
release the lease merely because Gateway processing raises.
Cancellation or fencing failure before the handoff callback releases only the
owned claim; after the callback starts, the same condition belongs to Gateway.

### Existing idempotency states remain authoritative

Admission reuses the current `in_flight`, `side_effect_started`, and
`completed` records. No message body, attachment value/path, sender, transcript
item, Turn state, or job payload is added to persistence.

`in_flight` remains a reclaimable fenced lease. The repository exposes an
owner-checked refresh operation that updates only the lease timestamp. It
cannot refresh `side_effect_started` or another owner's record.

Gateway keeps the current outcome rules after handoff:

- a known pre-side-effect failure releases the owned claim;
- immediately before non-idempotent Application input dispatch, the claim
  becomes `side_effect_started`;
- dispatched-unknown input remains sticky;
- accepted input completes the claim even if later projection correlation
  fails.

During Gateway startup, a handed-off message is buffered together with its
claim identity. A failed startup releases only those claims that remain safely
pre-side-effect. Gateway closes inbound admission before awaiting rollback, so
a callback racing failed startup receives no lease or releases a just-handed
claim instead of entering live processing. Gateway never drops an owned claim
without an explicit transition.

Startup remains in buffering mode until its queued messages and operations
drain successfully. Input racing an awaited drain failure is therefore added
to the rollback set rather than processed as live traffic before `start()` has
a successful outcome.

### Compatibility and responsibility

The ordinary completed-message callback remains supported and Gateway still
claims at that late boundary for adapters that have no media-preparation stage.
Production media-capable Channel adapters must use pre-admission. The reusable
contract kit makes the handler part of the Channel Port so this is not hidden
Gateway-specific duck typing.

During migration, Gateway inspects the Channel `start` signature before calling
it and uses the legacy two-callback form when the admission callback is not
accepted. It does not retry a `start` call after side effects. Legacy adapters
retain the late durable claim but do not gain the pre-media duplicate guarantee.

Process-local duplicate sets remain an optional fast path. A durable no-lease
result removes the transient key so a later redelivery can retry after a stale
claim becomes reclaimable. Durable correctness comes from Gateway admission,
not the local retention window.

## Classification and evidence

- **Core invariant:** stable-ID admission, fenced ownership, duplicate rejection
  before expensive work, and no retry after an ambiguous remote side effect.
- **Adapter-specific policy:** native authentication/access, identity parsing,
  attachment discovery/download, and provider acknowledgement.
- **Consumer policy:** persistence choice, configured stale-lease duration,
  retry UX, and deployment capacity.

QQ, Telegram, Feishu, and Weixin all admit provider events before native media
materialization and can receive provider redelivery. Their media and transport
shapes differ, while the admission identity and failure boundary are shared.

## Consequences

Restart redelivery can be rejected before attachment work without moving media
or Agent truth into persistence. A crash during preparation leaves a
reclaimable lease rather than a durable job. The Channel/Gateway Port gains one
explicit lifecycle seam and repository implementations gain an owner-checked
lease refresh operation.
