# Gateway projection request correlation design

Component ID: `gateway.projection.request-correlation`

Parent: `gateway.projection`

## Purpose and ownership

This leaf owns minimal bridge correlations that authorize an IM destination to
reply to an accepted IM-originated Turn or an interactive native request. It
owns Turn reply correlation, per-destination request-route correlation,
the typed `RespondToRequest` and `RequestResponseRouted` values, their
response validation, request-wide claim/transition fences, correlation
persistence, replay rules, and removal under terminal/retention cleanup.

It does not own native request truth, prompt or answer transcript copies,
approval policy, sender admission, native response execution, or Application
Turn state. The only native mutation remains the typed Application request
response operation after Gateway validates the destination correlation.

## Turn reply correlation

An IM-originated `AcceptedTurn` creates one immutable correlation from stable
Application/Thread/Turn/client-message identity to its originating
Conversation and optional IM reply target. It is never inferred from the last
inbound message or a route's destination context. A `steered/preserve_existing`
dispatch must name an already-correlated Turn before native mutation; it cannot
replace that correlation even if the new input came from another Conversation.
Missing correlation fails before dispatch. A native result that names a
different Turn is post-acceptance degradation and cannot retarget either Turn.

Explicit terminal Turn events, route/Thread cleanup, and finite retention
remove stale correlations. Recovery/external Turns without a matching bridge
correlation have no inferred reply target.

## Interactive request correlation

An Application-scoped `RequestRef` includes opaque epoch-safe native identity.
Gateway creates one minimal correlation per destination only after its stable
request delivery is accepted or already known completed. It holds the exact
Thread/Turn scope, Conversation, delivery ID, bounded supported response
shape, optional native expiry, state, and timestamps—never prompt, answer,
permission, or a second request state machine.

The request-wide state is monotonic:

```text
open -> responded | stale | resolved
responded -> stale | resolved
stale -> resolved
resolved -> terminal
```

Same-state repeats are idempotent. Expected states are a CAS fence, not
permission for a backward transition. A successful response transitions every
destination correlation to `responded`; native terminal truth wins by moving
them to `resolved`. A late successful destination delivery inherits the
request-wide terminal state and cannot create an open route. `stale` is also
monotonic for one epoch-scoped reference; transport reuse must use a new
reference.

Request execution serializes local contenders, while the Application remains
the first-writer authority. A destination that never received the request,
another response attempt, or resolved/stale state fails explicitly. One
destination's delivery failure neither blocks another destination nor
authorizes a response.

Gateway selects the stable `RequestRef` and owns this request-response policy.
It consumes only the waiter-safe process-local lock mechanics from
`gateway.concurrency`; the primitive stores no request or correlation state and
cannot authorize a response or retry. Request locking occurs only after the
operation has entered the configured finite active-Conversation registry, so
the number of simultaneously active request keys is bounded by that enclosing
admission even though the inner mechanics registry has no separate limit.

## Publication and immediate responses

A request prompt may reach its Conversation before the accepted Channel receipt
and minimal correlation finish publication. A response without an existing
durable correlation waits only for a known in-flight publication of the exact
`(RequestRef, ConversationRef)`, then performs the existing durable
destination/shape/state check under the request-wide native response lock.
Existing durable destination evidence bypasses publication waiting, including
while the same destination has a repeated publication. The wait grants no
authority: failure, ambiguous delivery, cancellation or retired routing still
leaves the response rejected. OPEN is never persisted before accepted/completed
delivery, and no response or Channel send is retried.

`GatewayLimits.request_publication_wait_timeout_seconds` is an explicit finite
positive bound, defaulting to five seconds. A waiter uses the smaller of this
bound and the request's remaining native expiry. When this wait times out, it
returns the stable
`request_delivery_pending` operation code, mapped to a pre-native `conflict`
action failure. It does not claim that the native request is stale. A completed
pre-native pending failure is terminal and cannot dispatch later; a user may
submit a new response with a new action ID through ordinary authorization.
Cancelling a waiter never cancels the existing managed publication.

Publication completion signals are process-local and limited to admitted
presentation jobs. The existing route coordinator and a waiter-counted exact-
destination lock serialize duplicate publications; entries are removed on every
exit. The response waits before taking the request-wide lock, and publication
never takes that lock or awaits native response. Store insertion still inherits
request-wide terminal state for late destinations. An unrelated or repeated
slow delivery cannot obstruct an already authorized response.

A synchronous callback or a Channel awaiting its response child receives a
bounded pending outcome when the publication-wait timeout wins. The existing
native-expiry fence can instead cancel the enclosing Channel.send and its
awaited response; caller cancellation and Gateway.stop retain the same
cancellation semantics. Cancellation, including while a failed preflight
receipt is settling, is not converted into a new pending result. Existing
receipt recovery returns a committed terminal outcome when available or leaves
a pre-native RESERVED receipt retryable by an explicit same-ID call. That call
rechecks current authority and expiry; no cancellation schedules later dispatch
or automatic replay, and fresh startup does not re-present expired requests.

A separately scheduled inbound child waits normally and may succeed once
publication finishes, regardless of inherited context. No asyncio private task
graph, ContextVar classification, cross-owner cancellation protocol, persistent
pending state, durable retry queue, or new recovery authority is introduced.
Existing managed presentation cancellation and Gateway shutdown own teardown.

## Restart and recovery

Correlations are bounded durable bridge routing facts. They are reconciled
only from authoritative native request snapshots when that capability exists;
the SDK never recreates a pending request from correlations. Without such a
snapshot, a transport reset invalidates stale response handles and leaves a
truthful degraded request-recovery fact. `request.resolved` does not close the
Turn.

The canonical runtime, typed response operation/result values, concrete
validation, stable identities, destination selection, Turn-correlation
orchestration, monotonic transition/claim fences, and authoritative pending
request reconciliation live together in
`gateway/projection/request_correlation.py`. One runtime instance is composed
with the input dispatcher, observation, recovery, and route-delivery
coordinators: the dispatcher owns acceptance buffering and ordering,
observation forwards events through its typed gate, routes retain per-route
delivery ordering, and recovery invokes only the typed Thread-scoped
pending-snapshot method after a supervised gap. Every correlation decision and
mutation remains in this leaf. The historical
`imagent.request_projection_runtime` module is absent.

`imagent.gateway.projection` exposes the exact `InteractiveRequestProjection`,
`RespondToRequest`, and `RequestResponseRouted` owner objects. The historical
`imagent.contracts` facade and its `operations` module do not retain these moved
request attributes; `imagent.contracts.validators` is physically absent. The
closed Gateway operation aggregate
imports the exact operation family from this owner and private Gateway
orchestration only adapts an owner rejection into the aggregate failure
result; it does not own
response validation, request locking, transitions, correlation persistence,
or replay.

The process-local repository implementation remains in
`gateway/persistence/memory.py`; persistence owns passive records and atomic
storage only. SQLite schema and SQL mutation live only in
`gateway/persistence/sqlite.py`, and row conversion remains in the pure
row-mapping leaf.

Canonical composition injects this owner's narrow authorization/routing seam
into `ConversationActions.respond_request`. The action preflight proves actual
accepted delivery to that Conversation before B's native fence; the request
lock then preserves Application first-writer truth. Startup uses only native
pending snapshots and marks pre-existing open evidence stale when that
capability is absent.

- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
- [ADR 0013](../../../../decisions/0013-bounded-application-event-admission.md)
- [ADR 0016](../../../../decisions/0016-uniform-workspace-and-consumer-actions.md)

Queued requests use the same canonical Turn guard before presentation, including
the independent request lane and restored pending requests. A request arriving
before its queue event or send acknowledgement cannot acquire a guessed reply
identity. Original queued evidence is persisted before routing the request.
