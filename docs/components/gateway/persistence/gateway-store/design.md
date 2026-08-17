# Gateway store design

Component ID: `gateway.persistence.gateway-store`

Parent: `gateway.persistence`

## Purpose

This leaf owns the one public v1 persistence port and its coherent memory and
SQLite durability domains. A consumer chooses `MemoryGatewayStore` or
`SQLiteGatewayStore`; it never assembles a public dictionary of repositories.
Focused binding, route, checkpoint, correlation, idempotency, delivery, lease,
workspace-identity, and effect-receipt protocols remain internal owner seams
behind a store runtime session.

The store persists only IM bridge state and rebuildable projections. It is not
a transcript, resource registry, native-operation database, content spool,
credential store, artifact ledger, or general transaction service.

## Namespace and runtime fence

One store instance/database is one Gateway namespace. The first lease
acquisition fixes its stable `gateway_id`; reuse with another ID fails rather
than merging bridge authorities. The namespace has one renewable lease with:

- an opaque owner token;
- a monotonically increasing fencing epoch; and
- a store-authored expiry instant.

Lease acquisition, renewal, release, and every bridge-state mutation compare
the owner token, epoch, and unexpired lease inside the same store transaction.
The public composition validator admits only a positive non-boolean integer
epoch and a timezone-aware `datetime` expiry, in addition to the exact stable
Gateway and owner identities requested by acquisition.
Callers never supply the current time. Expiry permits takeover and increments
the epoch, but it cannot revoke a native call already fenced by the old owner.
Reacquisition by the current owner renews that one lease without changing its
epoch, so existing same-owner session aliases follow the store-authored expiry;
an expiry instant copied into a session is not a second client-side fence.
After takeover, every old-session mutation fails, including binding, route,
checkpoint, correlation, idempotency, delivery, workspace, and effect-receipt
mutation.

SQLite maintenance authority is default-deny and exists only during the
constructor's schema/migration scope. Repository mutations perform an explicit
lease guard inside their transaction even when the requested write converges
or affects zero rows; row triggers are an additional defense, not the sole
fence. The session delegates only the exact focused protocol methods and never
exposes its connection or private state.

Coherent Gateway composition recognizes the complete structural session
capability, including SQLite's deliberately delegated focused repository
methods. Detection validates the lease plus every required callable; it does
not rely on nominal Protocol inheritance that could shadow delegation with
stub methods, and it exposes no session capability to Controllers.

`GatewayStoreSession` is the focused internal runtime capability returned by
lease acquisition. It carries no product actor, adapter, content, or
authorization policy. Block C consumes only the effect-execution port and
never receives the store, session, lease, or repository views.

## Binding generation and atomic Gateway mutations

`ConversationBinding.generation` is the durable Conversation mutation clock.
It replaces the pre-v1 revision vocabulary. The store retains the highest
generation even while the Conversation is unbound or its current row is
removed, so clear, delete, compaction, row recreation, restart, and selecting
the same target can never recreate an earlier generation.

A store-only action is one prevalidated `StoreMutationRequest`. In one
lease-fenced transaction the store:

1. checks an existing terminal receipt before current binding/route state;
2. rejects same effective action identity with a changed payload fingerprint;
3. reserves shared receipt capacity for a new identity;
4. checks an optional binding-generation precondition;
5. applies the complete binding target or derives hierarchical-clear retained
   ancestors from binding state read inside this transaction, then applies any
   route upsert/removal against the resulting binding state;
6. advances and retains the binding generation when binding state changes;
7. writes the minimal terminal outcome receipt; and
8. commits or rolls back the complete transaction.

When C supplies a resource/capability preflight, the executor first performs a
lease-fenced terminal-receipt check without reserving state. A closed failure
is committed through a separate receipt-only lease-fenced transaction that
checks terminal replay and capacity again and never touches a binding or
route. A successful preflight enters the unchanged atomic mutation transaction,
whose first step checks the receipt again. This keeps callbacks outside store
locks/SQLite transactions while guaranteeing that an already or concurrently
committed terminal outcome wins.

This transaction is the storage primitive for select, bind, every clear,
observe, and clear-observation. The request is operation-agnostic: block C owns
the public closed operation variants and maps them to this validated plan.
Its Conversation is explicit even for route-only work, so observation changes
do not create a binding or advance the binding generation.
When route removal carries `unless_bound_to_route_thread`, the same transaction
preserves the route if its Thread remains the Conversation's resulting current
binding and removes it otherwise. Thus an explicit foreground clear cannot
silence bound output, while an unbound or differently bound route remains
clearable without an upsert or caller-side read. Success retains the matched
route's stable Thread reference and route ID whether it was protected or
deleted. A missing route still succeeds with the requested route ID and no
invented Thread reference; memory and SQLite replay the same complete outcome.
Every hierarchical clear does advance the generation, including an
already-cleared or unbound Conversation; application clear removes the current
row while retaining the successor generation tombstone. This prevents a
clear/no-row/clear ABA from satisfying an older workflow CAS.

## Effect receipts and capacity

Gateway, primitive native, Conversation request-response, and composite
workflow receipts share one positive finite, non-evicting capacity per
namespace. A new identity at capacity fails before a Gateway or native effect.
Existing replay, terminal reads, reconciliation, and phase completion remain
available at capacity. Time does not evict a receipt, and a tombstone counts
toward the same bound. Namespace rotation or explicit purge is valid only
under an external guarantee that retired caller action IDs cannot return.

An `ActionIdentity` is namespaced by Gateway, trusted principal, optional
Conversation, action kind, and caller-stable action ID. B derives a
domain-separated action key and payload fingerprint. Workflow phase IDs are
derived in a different domain. The raw principal, arguments, CWD, title,
content, path, credential, response, and native payload are never persisted;
only digests and bounded stable reference fields cross the store boundary.

Receipts have the finite phases `reserved`,
`native_side_effect_started`, `native_result_known`, and `terminal`.
Store-only actions commit directly to terminal; they never retain a reserved
phase, including while an optional preflight is running. Native actions persist
`native_side_effect_started` before the callback. A known native result becomes
terminal for a primitive action or `native_result_known` for a workflow. An
ambiguous result stays protected and yields `outcome_unknown`; elapsed time or
a negative resource lookup cannot convert it into retry permission.

## Native and workflow transactions

The store does not call an Application. It supplies the durable phases used by
`GatewayEffectExecutor`:

- reserve/check fingerprint and capacity before effects;
- commit `native_side_effect_started` immediately before the call;
- retain a fixed known error or minimal authoritative resource reference;
- allow reconciliation only through the executor's evidenced idempotent phase
  call or terminal operation-status port; and
- reject stale runtime owners on every later receipt/state write.

Create-and-select and create-and-bind reserve the current Conversation binding
generation with their receipt. Once native creation is known, the final store
transaction compares that generation and atomically commits the hierarchical
binding, matching `foreground_only` route when requested, successor
generation, and terminal workflow outcome. A mismatch commits `partial` with
the created reference and a fixed stale-binding error. It never deletes the
created native resource. A terminal workflow receipt is read before current
binding state, so later user intent cannot erase the action's stored result.

## Workspace identity and leakage boundary

Startup persists only `(ProjectRef, root_fingerprint)` for fixed/flat
`WorkspaceIdentity`. Reusing one stable Project/workspace ID with a different
fingerprint fails while retaining the original evidence. The canonical root
path is never stored.

SQLite schema inspection is acceptance evidence. No B-owned table or column
may contain message bodies, transcript items, prompts/responses, CWD/title,
paths, credentials, raw native events, native execution state, callbacks,
spool bodies, or replayable work.
The current binding column is `generation`. Opening the immediately preceding
`revision` schema renames it before use. A one-time secure-delete migration
projects legacy delivery receipts onto closed bounded evidence, nulls the
retired error/detail fields, confirms WAL truncation, and vacuums stale bytes
before writing its completion marker. A busy checkpoint fails closed with no
accepted marker so a later open retries physical cleanup; later non-null values
are rejected as malformed current state rather than repaired.

## Authority

- [V1 design](../../../../V1_DESIGN.md)
- [V1 executable specification](../../../../V1_EXECUTABLE_SPEC.md)
- [ADR 0016](../../../../decisions/0016-uniform-workspace-and-consumer-actions.md)
- [Gateway persistence](../README.md)
