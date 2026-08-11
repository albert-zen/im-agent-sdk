# Gateway effect execution design

Component ID: `gateway.effect-execution`

Parent: `gateway`

## Purpose and boundary

This leaf owns the block-B execution state machine over a leased
`GatewayStoreSession`. It exposes one private typed `GatewayEffectExecutor`
port for block C. C owns frozen `ApplicationActions` and
`ConversationActions`, public operation construction, actor/Conversation
scope, capability/authorization preflight, and product-facing ergonomics. It
does not receive a store, repository, lease, receipt, or concrete adapter.

B owns these operation-agnostic entry points:

- `replay_store_mutation(StoreMutationRequest)`;
- `execute_store_mutation(StoreMutationRequest, preflight=None,
  commit_fence=None)`;
- `execute_native_mutation(NativeMutationRequest, invoke, preflight=None,
  reconcile=None)`;
- `execute_create_binding_workflow(CreateBindingWorkflowRequest, invoke,
  preflight=None, reconcile=None, commit_fence=None)`.

The requests contain a validated `ActionFingerprint`, fixed action kind,
minimal stable target/reference facts, and when applicable a complete binding/
route plan. They contain no free-form context, service locator, credentials,
adapter, product command, or retry policy. The native callback receives only
the domain-separated stable phase ID and returns a known `Succeeded` or
`Failed` minimal reference/error. Raising or cancellation after the durable
native fence is ambiguous.

The optional `NativeEffectPreflight` is exactly
`Callable[[], Awaitable[ActionError | None]]`. C uses it for capability,
authorization, request-correlation, and bounded-response checks that must
precede a first native effect. B invokes it only while the durable receipt is
`reserved`, after terminal/native-started replay handling and before the native
fence. `None` authorizes; a closed `ActionError` commits a terminal failure
without a native call. Terminal retries never re-run preflight, so later
binding/request state cannot hide a committed result.

The private replay entry reads the lease-fenced terminal store-mutation receipt
for the complete fingerprint without reserving, mutating, or exposing the
receipt. It returns the stored outcome for an identical action, typed conflict
for changed payload, and `None` only when no terminal receipt exists. C uses
this read before process-local projection-route admission so a terminal route
success can still be presented as `Partial(value, stale_runtime)` while the
projection runtime is stopped; a new action remains a pre-write
`Failed(stale_runtime)`.

The optional `StoreEffectPreflight` has the same closed callback shape but no
native phase. B performs a lease-fenced terminal-receipt check before invoking
it. `None` permits the existing atomic store transaction; a closed error is
committed as a terminal Gateway receipt in a receipt-only transaction without
changing binding or route state. The final commit of either the failure or the
mutation checks the receipt again, so a concurrently committed terminal
outcome wins. Cancellation before that commit leaves no reserved Gateway
receipt or partial bridge mutation.

The optional `StoreEffectCommitFence` is an async context factory yielding
only `ActionError | None`. B enters it after terminal replay and preflight but
immediately around either the receipt-only rejection commit or atomic store
mutation. B still owns and invokes the store commit; the fence receives no
session, request, receipt, or callback. D composition supplies it only for a
pre-fenced route action so projection shutdown and that transaction cannot
cross. A closed fence error returns `Failed` without committing anything.

The optional `WorkflowEffectCommitFence` has the same authority-free result but
accepts only B's derived stable route ID. B invokes it only after a foreground
Thread workflow has a durable known native result and immediately around the
atomic terminal binding/route transaction. A closed fence therefore returns
`Partial(created, stale_runtime)` without binding or route mutation; terminal
workflow replay bypasses it, and restart can resume the known result without
another native call. Neither fence receives a session, receipt, or commit
callback.

## Outcome algebra

The public runtime result is the closed generic union:

- `Succeeded(value)`;
- `Failed(error)`;
- `Partial(value, error)`; and
- `OutcomeUnknown(error)`.

Expected unsupported, stale, conflict, capacity, native rejection, partial,
and ambiguous outcomes are values. Invalid construction/configuration may
raise before execution. The persisted error projection uses a fixed bounded
`ActionErrorCode`; it never stores exception text or native metadata.

## Execution rules

Store-only execution checks terminal replay, optionally runs its side-effect-
free preflight, then delegates one atomic mutation or receipt-only failure
transaction and returns the stored terminal result on identical retry. Native execution reserves, returns any
existing terminal result, runs the optional preflight, records the native
fence, then calls exactly once. Only the caller that atomically
advances `reserved` to `native_side_effect_started` may invoke; a concurrent
same-action caller observes the durable phase and cannot duplicate the call. A retry in
`native_side_effect_started` never calls `invoke` again. It may call
`reconcile` only when C supplied an implementation backed by evidenced native
idempotency or terminal operation-status semantics. `None`, missing resource,
and temporary `not_found` do not prove absence and remain unknown.

Workflow execution stores a known created reference before attempting its
binding phase. A later caller may resume only that uncommitted binding phase
under the originally captured generation. Generation mismatch becomes a
terminal partial result; no code path invokes native delete or repeats an
unknown create.

The executor retains no action registry, transcript, work queue, or product
policy. It is safe for C to wrap but is not itself a public action surface.
If cancellation or a storage error races a transaction acknowledgement, the
executor reads the receipt: a committed terminal result wins, a committed
native fence stays unknown, and a known workflow result remains resumable.
Callback results are validated inside the post-fence ambiguity boundary; an
invalid native or reconciliation value becomes durable unknown rather than an
escaping construction error.

## Authority

- [V1 design](../../../V1_DESIGN.md)
- [V1 executable specification](../../../V1_EXECUTABLE_SPEC.md)
- [ADR 0016](../../../decisions/0016-uniform-workspace-and-consumer-actions.md)
- [Gateway store](../persistence/gateway-store/design.md)
