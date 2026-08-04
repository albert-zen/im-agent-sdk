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
with observation, recovery, and route delivery: observation retains acceptance
buffering, routes retain per-route delivery ordering, and recovery invokes
only the typed Thread-scoped pending-snapshot method after a supervised gap.
Every correlation decision and mutation remains in this leaf. The historical
`imagent.request_projection_runtime` module is absent.

`imagent.gateway.projection` exposes the exact `InteractiveRequestProjection`,
`RespondToRequest`, and `RequestResponseRouted` owner objects. The historical
`imagent.contracts` facade and its `operations` and `validators` modules do not
retain these moved request attributes. The closed Gateway operation aggregate
imports the exact operation family from this owner and the Gateway root only
adapts an owner rejection into the aggregate failure result; it does not own
response validation, request locking, transitions, correlation persistence,
or replay.

The process-local repository implementation remains in
`gateway/persistence/memory.py`; persistence owns passive records and atomic
storage only. SQLite schema and SQL mutation live only in
`gateway/persistence/sqlite.py`, and row conversion remains in the pure
row-mapping leaf.

- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
- [ADR 0013](../../../../decisions/0013-bounded-application-event-admission.md)
