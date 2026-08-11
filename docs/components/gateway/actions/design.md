# Scoped consumer actions design

Component ID: `gateway.actions`

## Authority and purpose

This leaf is the sole ergonomic consumer boundary over closed Application and
Gateway operations. `ApplicationActions` is frozen to one `ApplicationRef` and
stable authenticated principal. `ConversationActions` is frozen to one
authenticated `ConversationRef` and actor and internally composes the same
Application action path.

Neither surface exposes Gateway, a concrete adapter, native client, store,
repository, session, lease, claim, checkpoint, credential, or an `Any` context.
No Conversation method accepts a substitute Conversation. Public construction
is owned by Gateway composition; callers obtain the surfaces through
`gateway.application(...)` and `gateway.actions(...)` while the canonical
public `Gateway` context is running.

## Semantic groups

Reads construct bounded typed Application queries or inspect the scoped
binding. They are side-effect free: they never select, bind, observe, activate
native UI, or create a resource. Bounds apply to returned collections as well
as requests: Application discovery has a finite cardinality, and history or
catch-up results cannot exceed their requested limit.
`get_binding` validates the complete hierarchical value and requires its
Conversation identity to equal the surface's frozen Conversation before
exposing it; runtime data cannot substitute another Conversation.

Primitive native mutations affect only Application truth. In particular,
`create_project` does not select, `create_thread` does not bind, and
`delete_project` or `delete_thread` never clears a Conversation binding.
Primitive Gateway mutations affect only binding or observation state.
`respond_request` is intentionally absent from `ApplicationActions`; the
Conversation runtime must prove that this Conversation received the request
and validate the delivered response shape. C supplies that check through B's
typed native preflight: B replays an existing terminal receipt first, but a new
action must pass authorization while reserved and before
`native_side_effect_started`. A closed authorization failure becomes a terminal
failed receipt without a native call.

Every effectful method requires a caller-stable `action_id`. The surface
derives an `ActionIdentity` from the configured Gateway namespace, authenticated
principal, optional fixed Conversation, fixed action kind, and that ID. It
passes only a bounded canonical semantic payload to
`derive_action_fingerprint`; B persists the digest, never raw CWD, title,
content, path, response, or command arguments.

## Execution seam and outcomes

C consumes exactly B's `GatewayEffectExecutor` methods and request values:

- store-only actions map to `StoreMutationRequest`;
- primitive native actions and authorized request responses map to
  `NativeMutationRequest`;
- the two SDK workflows map to `CreateBindingWorkflowRequest`.

Before that request mapping, each primitive constructs and validates the
closed Application or Gateway Operation owned by its semantic authority. The
operation is transient control intent, not a second executor or receipt; C
then projects its closed fields into B's operation-agnostic request.
Bounded create payloads are validated before any immutable copy, semantic
fingerprint, durable reservation, or native callback is constructed. C then
snapshots the validated mutable metadata and validates that snapshot again at
the callback boundary, so caller mutation cannot change the receipt's native
intent.

Every selection/observation and native mutation has a C-owned typed preflight
against the configured Application runtime. It proves exact Application
existence, declared capability support, and complete Project/Thread ancestry
through bounded discovery plus the canonical resource-read operations.
Store-only actions pass that check through B's `StoreEffectPreflight`: B checks
a lease-fenced terminal receipt first, then runs preflight before atomically
committing either its closed failure receipt or the requested mutation. A
foreign, stale, nonexistent, or unsupported resource never becomes bridge
state, and later resource drift cannot hide a committed result. Native
mutations and create/bind workflows pass the same check through B's
`NativeEffectPreflight`: B replays a terminal receipt first, then runs
preflight only for a newly reserved action and before the native side-effect
fence or callback. C maps native `not_found`, `request_stale`, and `unsupported`
results into the closed `ActionError` vocabulary rather than guessing that a
resource or capability exists. Any action that creates an observation route—
explicit observe, foreground binding, or foreground create-and-bind—also
requires non-unsupported streaming; a non-foreground binding does not invent
that requirement.

Interactive request responses are admitted before snapshotting or
fingerprinting: at most 32 question IDs, at most 64 answers per question, and
at most 4,096 characters per answer. C fingerprints a canonical digest of the
bounded answer structure, while the durable effect request retains no raw
answer text.
The canonical composition injects the projection owner's narrow response seam:
preflight proves that this exact Conversation received the request, while the
native phase serializes on stable `RequestRef`, validates the persisted response
shape, and delegates first-writer truth to Application `request.respond`.
Terminal effect replay precedes reauthorization. A missing authoritative
pending snapshot marks restored open evidence stale rather than manufacturing a
request or retrying an unknown native effect.

The action layer has no store/session/runtime-lease/repository authority and
does not sequence persistence calls. Its opaque projection barrier-generation
handle carries none of that authority. B returns its closed `ActionOutcome`; C
maps the minimal `EffectValue` into immutable `ActionValue`. For a successful
Conversation binding/route mutation or workflow, including terminal receipt
replay, C invokes the composition runtime's typed projection-route
reconciliation seam with the returned route ID and, when begun before the
write, an opaque projection-owner lease. The lease carries no store or runtime
authority; it only identifies the exact route-barrier generation and serializes
same-route action lifecycles. The projection owner reads current route/binding
authority, so an old replay cannot recreate later-removed state. Failed,
partial, or unknown durable outcomes do not activate projection.
For explicit observe and foreground bind, whose stable route ID is known before
the store call, C asks the same projection owner to install its sole bootstrap
barrier before B can make the route visible. A durable non-success releases the
barrier; a durable success releases it only after reconciliation succeeds. A
failed activation therefore stays fenced until same-ID replay converges it. No
second barrier or repository pre-read is introduced. A create-and-bind workflow
creates a new native Thread before its atomic foreground route commit. Once B
knows that route ID, it enters the same composition-owned lifecycle fence and C
retains the resulting opaque barrier generation through the same reconciliation
seam.
Route removal retires the exact closed generation and wakes its blocked
delivery; a later same-ID route receives a new generation that an older lease
cannot complete.
If the durable outcome succeeded but process-local activation fails, C returns
`Partial` with the same value and a closed activation error rather than false
success; a later same-ID replay may converge activation without repeating B or
native effects. For a route-producing store action, C asks B for a terminal
receipt before installing a process-local barrier: identical terminal success
continues through current-state reconciliation, while changed payload remains
conflict and only an absent receipt enters new-action lifecycle admission.
After authoritative preflight, B enters the opaque action lease's
composition-owned commit fence around its terminal store transaction. The
action layer receives no store or receipt: it supplies only the fence context.
Shutdown and commit therefore have one winner. If shutdown owns the fence,
the new action is `Failed(stale_runtime)` with no terminal receipt, binding, or
route; if commit owns it, shutdown waits for that transaction and later
activation may honestly be `Partial(stale_runtime)`.
For a foreground create-and-bind workflow, native creation and its
`native_result_known` receipt necessarily precede route preparation. If
shutdown then wins, the workflow remains `Partial(created, stale_runtime)` with
no binding or route; restart resumes the known workflow and commits exactly
once. If its terminal binding/route transaction wins, shutdown waits and any
later activation failure is the same typed partial.
A terminal `Partial(created, stale_binding)` workflow proves its attempted
route is absent, so C marks that fact on the opaque abort seam and the projection
owner retires the unused barrier generation. Ambiguous post-entry store failure
does not make that claim and remains conservatively fenced.
Projection shutdown before a pre-fenced store mutation returns
closed `Failed(stale_runtime)` without writing the route; shutdown or worker
termination after durable success returns the same closed `stale_runtime`
`Partial`, retains safe
fencing, and lets restart replay converge when delivery has no sticky unknown
native outcome. If shutdown cancels an already-invoked Channel delivery, its
in-flight claim is never retried; explicit replay remains `Partial` and keeps
the baseline fence closed instead of falsely reporting activation success. A
caller cancellation after terminal
receipt is shielded and joined through the one reconciliation attempt; a repeated cancellation may
propagate, but cannot open the incomplete route barrier. Otherwise `Succeeded`,
`Failed`, `Partial`, and
`OutcomeUnknown` are preserved exactly. The `.ref` field is reconstructed only
from B's authoritative stable reference. Application reads and mutation
callbacks validate the exact operation/result pair before mapping it. A
request-response success must name the authorized `RequestRef`; a mismatched
native result is never published as success.

Binding preconditions use `expected_generation` exclusively. There is no
revision alias. Clear/delete/recreation safety and the monotonic generation
floor are B-store responsibilities.

Hierarchical clears map only their closed `BindingClearScope` and optional
generation precondition. C never reads a binding to manufacture retained
ancestors; B derives them inside the same receipt/CAS transaction. This makes
terminal replay precede current state and prevents stale pre-read ancestors
from overwriting newer intent.

Every `StoreMutationPlan` names the fixed Conversation explicitly. Observe and
clear-observation therefore remain route-only transactions with no synthetic
binding target, while B validates every supplied binding or route endpoint
against that Conversation.

Under `foreground_only`, primitive Thread binding carries its matching route in
the same store-mutation plan and fingerprints that policy choice. Other route
policies bind without manufacturing an observation route. Foreground
clear-observation requires B's store transaction to retain a route while its
Thread remains the Conversation's current binding; C must not approximate that
condition with a pre-read. C supplies
`RouteDeleteCondition.UNLESS_BOUND_TO_ROUTE_THREAD` only for that policy, so B
can preserve a matching bound route or delete an unbound/differently bound
route atomically after terminal-receipt replay.

The binding generation precondition participates in the semantic fingerprint.
Changing only that precondition under the same action ID therefore reaches B
as a changed-payload conflict instead of replaying a different CAS intent.

## Safe workflows

`create_and_select_project` and `create_and_bind_thread` are the only public
cross-authority workflows. C constructs the closed native create operation and
one workflow request; B owns durable fencing, native invocation/reconciliation,
binding-generation CAS, foreground-route transactionality, receipt replay,
and outcome classification. C never compensates by deleting a created native
resource after a binding failure. `Partial` therefore retains the created
resource reference.

The focused C tests use a strict `GatewayEffectExecutor` fake to verify request
mapping and coordinator behavior. Concrete acceptance requires the B-owned
memory and SQLite executors to run the same request shapes with atomic receipt,
restart, lease, unknown-outcome, and workflow-CAS evidence. No C-owned fallback
persistence or repository sequence is permitted.

## Composition

`src/imagent/gateway/actions.py` owns the surfaces. They are exact lazy exports
from `imagent.gateway` and `imagent`. Canonical `Gateway` acquires one coherent
B store session and lets the D composition seam construct exactly one private
store-backed executor. That same lifecycle-bound `GatewayEffectExecutor` serves
explicit public factories and inbound Controller actions; neither path receives
a session, receipt, runtime, adapter, or alternate input dispatcher.

Route-bearing actions retain D's receipt-first replay, exact bootstrap lease,
commit fence, post-commit reconciliation, and cancellation join. E adds no
route write, binding synchronization, worker subscription, or fallback
workflow. The public `gateway.actions` and `gateway.application` factories are
available only for the coherent session, and retained surfaces are invalidated
before canonical shutdown. Loose repository graphs still cannot construct
these surfaces.

## Authority

- [v1 design](../../../V1_DESIGN.md)
- [v1 executable specification](../../../V1_EXECUTABLE_SPEC.md)
- [ADR 0016](../../../decisions/0016-uniform-workspace-and-consumer-actions.md)
- [B effect execution](../effect-execution/design.md)
