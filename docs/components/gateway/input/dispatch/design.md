# Gateway input dispatch design

Component ID: `gateway.input.dispatch`

Parent: `gateway.input`

## Purpose

Dispatch turns one verified, unconsumed Conversation input into one truthful
Application input attempt while preserving stable identity, the native
side-effect fence, and a distinct acceptance-ordering gate.

## Ownership and flow

This leaf owns the canonical stable `derive_client_message_id` derivation,
`prefer_active_turn` as the Gateway default, exactly-once authorization of the
typed `ApplicationInputDispatch` pre-dispatch fact, validation and correlation
of the returned `AcceptedTurn`, and the finite per-Thread acceptance-ordering
fence. The Gateway aggregate retains the existing per-Conversation serializer,
Controller/I1, strict binding resolution, route preparation, and I2
claim-phase wrapper. It owns no binding mutation or Thread creation. Native
steer implementation and active-Turn truth remain
with the Application; projection worker lifecycle, recovery, and delivery
remain with their owning leaves.

After Controller decline, the Gateway aggregate requires an already complete
Application/Project/Thread binding and validates that its Conversation and
resource ancestry match exactly. Missing hierarchy raises the public typed
`MissingBindingError` with stable `missing_binding` classification; foreign or
malformed ancestry fails as a contract violation. A complete hierarchy must
then resolve a registered Application and matching authoritative `project.get`
and `thread.get`; absent native ancestry raises public typed
`StaleBindingError` with stable `stale_binding` classification. These all occur
before I1, route/worker mutation, or native input. A non-`not_found` native read
failure preserves its fixed pre-acceptance operation classification rather than
pretending the binding is stale. Gateway never discovers a sole Application,
creates a Project/Thread, selects a default CWD, or interprets Message content
as control intent. An optional Controller may explicitly create/select and
create/bind through `ConversationActions`, then return `None`; the unchanged
Message continues through this exact same resolution and dispatch path.

After strict resolution and optional I1, the aggregate prepares the projection
route. It invokes this leaf once with the verified input and established
Conversation lock. This leaf derives the client message ID only from stable
Conversation and native message identity, then calls the Application with the
Gateway's `prefer_active_turn` default. Immediately before a native mutation,
the adapter must offer exactly one typed dispatch fact through the narrow
native side-effect pre-dispatch fence:

- `started` requires create-new correlation and no expected Turn;
- `steered` requires preserve-existing correlation and an expected active Turn.

An Application that cannot steer must report `started`; it may not fabricate
steer or silently queue. The returned `AcceptedTurn` must match the authorized
disposition/correlation identity. The canonical request-correlation owner
authorizes the pre-dispatch fact and persists or validates the matching reply
correlation; dispatch does not reach its repositories directly. Raw native
events are never exposed to Controller consumers.

## State, recovery, and bounds

Before the native side-effect fence, a known failure releases the matching
claim. Entering that native fence protects it as `side_effect_started`; lost
acceptance or cancellation after that point is `outcome_unknown` and never
authorizes redelivery. A valid `AcceptedTurn` makes inbound idempotency terminal
even if reply-correlation or buffered projection draining later fails. Started
input creates Turn reply correlation; steered input preserves the existing Turn
policy defined by ADR 0012.

The distinct acceptance-ordering gate and finite FIFO begin before the
Application call. While acceptance is pending, the single observation worker
forwards normalized events for that Thread into this FIFO. On every final
pending-dispatch exit, including a known pre-native-fence failure, dispatch
performs any required canonical correlation work, signals the ordering gate,
drains the FIFO through the existing typed projection event applier in order,
and removes its process-local entries. Overflow is an explicit per-Thread gap
and invokes the already-owned authoritative recovery path; it does not itself
change an inbound claim phase. A known pre-native-fence failure releases its
matching claim, while an entered native side-effect fence with an unknown
outcome or a valid `AcceptedTurn` preserves that claim's existing
non-redelivery status. Observation worker termination must not clear or signal
another owner's pending ordering gate; `restore()` clears this process-local
gate before routes/checkpoints regain their established recovery authority.

If the typed projection applier fails or is cancelled while draining the FIFO,
the gate reports the stable
`turn_acceptance_ordering_drain_failed` gap to the existing Thread recovery
seam before surfacing the drain failure. A live worker consumes that gap under
its established supervisor; a terminal worker records the typed health gap and
starts authoritative recovery without retaining a stale cancellation marker.
Neither case creates a concurrent second worker, redispatches the accepted
native input, or treats process-local FIFO loss as recovery truth.
If scheduling that recovery also fails, the original ordered-event failure or
cancellation remains authoritative and the scheduling failure is retained only
as logged secondary diagnostic context.

Conversation lock cardinality and Turn-acceptance event buffering are finite.
The Gateway aggregate selects and owns the Conversation lock; a new key at
capacity fails before Application effects. This leaf owns only the independent
per-Thread acceptance bound. Consumer work never runs on Channel or
Application socket-read paths.

The Gateway operation owner selects the stable Conversation key and maps the
configured capacity failure. Its entry/wait/cancellation mechanics come from
the dependency-neutral `gateway.concurrency` leaf, which owns no input,
binding, claim, or side-effect policy.

## Contracts and structure

The exact public facade exports
`imagent.gateway.input:{MissingBindingError,StaleBindingError}` and
`imagent.gateway.input:derive_client_message_id`; the historical
`imagent.contracts.validators` module and validator alias are physically
absent. Implementation is
`src/imagent/gateway/input/dispatch.py`. The private Gateway orchestration
owner calls the typed dispatcher after routing work, and the observation runtime uses its
typed acceptance-ordering gate to preserve event ordering. Neither relationship creates a
second Conversation registry, Application subscription, runtime, transcript,
outbox, spool, generic hook, service locator, global registry, or `Any`-typed
extension seam. The canonical observation owner retains the shared worker
lifecycle; dispatch consumes only its narrow typed event-applier boundary.

Dependencies are the common Interaction operation-error vocabulary,
Application contract/operations/events, Gateway
admission, Gateway binding-state validation, the request/reply-correlation
owner, and the existing typed projection event/recovery path. Strict binding
shape and authoritative existence resolution are complete before I1 and route
preparation; the missing/stale binding types and read preflight remain in this
owner because they classify entry into the one ordinary-input dispatch path.

## Authority

- [Vision](../../../../VISION.md)
- [Architecture](../../../../ARCHITECTURE.md)
- [Gateway aggregate](../../design.md)
- [ADR 0006](../../../../decisions/0006-core-admission-and-policy-ownership.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
- [ADR 0013](../../../../decisions/0013-bounded-application-event-admission.md)
