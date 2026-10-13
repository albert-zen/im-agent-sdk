# Queued Gateway admission

Component ID: `gateway.input.queued-admission`

## Authority

This leaf implements the Gateway association and presentation portion of
[ADR 0019](../../../../decisions/0019-queued-input-admission.md). Native remains
the only input queue and execution scheduler. No message content, image bytes,
paths, retry clock, failure history or substitute Turn is persisted.

The public immutable Gateway `input_continuation` defaults to
PREFER_ACTIVE_TURN. QUEUE affects only ordinary input after Controller decline;
approvals and commands retain their explicit control contracts. Unsupported
native capabilities fail before admission, including after a Controller has
legitimately created a Thread with an immutable creation profile.

## Original association and one effect fence

`QueuedInputRuntime` validates the adapter's typed dispatch against the original
Conversation/message, actor digest, exact Thread/binding generation, stable
native client ID, QUEUE mode and prepared payload fingerprint. Its caller enters
`GatewayStoreSession.fence_queued_input` through the same inbound claim owner.
That transaction inserts unknown association and marks the existing claim
side-effect-started together. There is no second queue, idempotency owner or
permission to resend after an unknown result. Same-message redelivery, changed
content, changed defaults and reconstruction cannot reopen the claim.

Positive receipt merge validates that immutable origin and atomically installs
the original Turn reply correlation when admitted. Late queued RPC responses
cannot regress an admitted/cancelled row. Queue edits change native execution
content, not the original admission fingerprint. Correlation recovery uses the
original queued append and the exact admitted user message's Turn.

Queue rows share the store's finite effect-receipt capacity and are retained
without eviction or TTL resurrection. Cancellation reserves its own existing
native action receipt. At capacity it fails before native mutation; canonical
reads and merges still work. Store migration creates a new table and fences;
old ambiguous claims without origin metadata remain protected and are never
inferred or replayed.

## Observation and ordering

One bounded Gateway worker per affected Thread owns the optional typed native
queue subscription. Native subscribe/resume and canonical read are distinct:
subscription installs a buffer before native snapshot/barrier, while read alone
has no subscription or execution Resume effect. The Application client retains
per-Thread native resume ownership across caller cancellation. Gateway closes
admission, cancels and joins its queue workers before stopping Applications.

The worker reconciles after subscribe and on typed change notices. Initial
installation failure is pre-dispatch rejection. After successful installation,
transport, snapshot and stream failures retain the same single owner and retry
read-only observation with the configured bounded subscription backoff. No input
or execution Resume is retried; failures log only the bounded exception type.
Live presenter failure is isolated from the observation owner. Bounded bulk
recovery uses one complete snapshot for at most 4096 requested client IDs;
separate cohorts, if configured store capacity exceeds that bound, make no
cross-cohort revision claim. Each receipt is checked independently. Absence
means unknown, never input retry permission. No positive snapshot is cached.

Startup reestablishes queue associations before normal delivery readiness and
pending-request recovery. Both common message delivery (live and authoritative)
and request delivery call the same real-Turn correlation guard. A request may
arrive before its queue event or receipt RPC. Canonical read establishes its
original Turn association first. Existing correlated active Turns do not wait
for unrelated queued input. An uncorrelated Turn with unresolved queue origin
fails closed instead of borrowing the latest Conversation reply identity.

## Scoped controls and bounded presentation

`ConversationActions.read_queued_input(message_id)` and
`cancel_queued_input(message_id, action_id=...)` require the original actor and
Conversation. IDs are lookup keys, never authorization. Controls may target the
original row after a binding change without exposing native IDs. Read is
read-only canonical reconciliation. Cancel never interrupts an admitted Turn:
only canonical CANCELLED returns success; ADMITTED returns QUEUE_ALREADY_ADMITTED.
Unknown cancellation is reconciled read-only by the existing action executor and
never reopens the original input claim.

`QueuedInputStatus` contains only original message_id and UNKNOWN/PENDING/
ADMITTED/CANCELLED. `QueuedInputPresenter` receives this value and a closed
ADMISSION/STATE_CHANGED/ATTEMPT_FAILED reason with Gateway-fixed Conversation,
delivery ID and reply identity. State notices use stable state-derived identity;
response/event ordering cannot create a second logical same-state notice.
ATTEMPT_FAILED is live feedback only; no native error string/attempt is persisted
as queue state and restart cannot claim a historical failure reason.

Presenter work has independent finite task/content/text/time limits, owns no
native effects and closes with Gateway. Automatic notices recheck the original
binding Thread and generation plus active projection policy before and after
rendering, then use the existing internal presentation/delivery path. Clearing
or rebinding a Conversation therefore suppresses obsolete unsolicited notices.
The proactive external credential authorizer is not repurposed as an internal
projection credential. Explicit scoped read/cancel remains available.
