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
`gateway.application(...)` and `gateway.actions(...)` when that composition
slice is present.

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

The action layer has no store/session/lease/repository access and does not
sequence persistence calls. B returns its closed `ActionOutcome`; C maps the
minimal `EffectValue` into immutable `ActionValue`, preserving
`Succeeded`, `Failed`, `Partial`, and `OutcomeUnknown` exactly. The `.ref`
field is reconstructed only from B's authoritative stable reference.
Application reads and mutation callbacks validate the exact operation/result
pair before mapping it. A request-response success must name the authorized
`RequestRef`; a mismatched native result is never published as success.

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

## Composition migration

`src/imagent/gateway/actions.py` owns the surfaces. They are exact lazy exports
from `imagent.gateway` and `imagent`. The pre-v1 `ImAgentGateway` repository
graph is not an accepted constructor for this action engine; its final
`gateway.actions`/`gateway.application` factory wiring is accepted only when
the coherent B store session is present. This is an explicit DAG integration
edge, not permission to run mutations through loose repositories.

## Authority

- [v1 design](../../../V1_DESIGN.md)
- [v1 executable specification](../../../V1_EXECUTABLE_SPEC.md)
- [ADR 0016](../../../decisions/0016-uniform-workspace-and-consumer-actions.md)
- [B effect execution](../effect-execution/design.md)
