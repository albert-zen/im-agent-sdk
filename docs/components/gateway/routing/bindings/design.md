# Gateway routing bindings design

## Purpose and ownership

`gateway.routing.bindings` owns the typed contract values for binding a
Conversation to a Project or Thread, clearing its Thread, Project, or
Application selection, and reporting the result. It also owns their pure
field/postcondition validation, the binding
repository/CAS authority, and same-target convergence. One private, typed,
constructor-injected binding runtime is the sole caller that mutates the
binding repository; Gateway composition supplies validated Application truth
and sequences that runtime with projection-route work.

This leaf owns:

- typed Project/Thread bind and hierarchical clear operation/result values;
- pure binding-operation field validation;
- pure binding-result identity and postcondition validation;
- binding repository mutation and optimistic CAS; and
- same-target convergence, including its generationless retry distinction.

It does not own Gateway aggregate dispatch, Conversation locks, foreground
projection route changes, recovery policy, Application Project or Thread
truth, native Thread activation, projection checkpoints, Application
observation workers, product command grammar, or a consumer's JSON display
cache. Controllers may submit the public typed actions, but product UX such as
`/new`, `/pick`, CWD, or profile selection stays outside Gateway routing.

## Contract and dependencies

A stable Conversation key and its one current hierarchical binding are
represented and mutated through the binding owner. Multiple Conversations may
independently bind the same Application Thread. Application-only and Project-
only selections remain valid, but every Thread selection requires and exactly
matches the binding's Project ancestor in every Project mode. Fixed/flat
workspace Projects are therefore ordinary stable binding scopes; mode never
authorizes a Project-less Thread. The binding repository stores only the
bridge-owned binding reference and generation; it never persists or copies
Project, Thread, transcript, Turn, or execution state. Gateway composition
constructs the binding runtime with the configured repository and calls its
explicit typed methods; it does not perform repository reads, writes, CAS, or
same-target comparison itself.

The public operation/result contracts are `BindConversationToProject`,
`BindConversationToThread`, `ClearConversationThread`,
`ClearConversationProject`, `ClearConversationApplication`, and
`ConversationBound`. Clear Thread retains Project, clear Project retains
Application, and clear Application leaves the Conversation unbound. The new
clear precondition fields use `expected_generation` exclusively, with no
revision alias, and accept only non-Boolean non-negative integers. Their focused implementation owner is
`imagent.gateway.routing.bindings`; `imagent.gateway.routing` re-exports those
exact objects. Their current and
target exports are recorded in the [component map](../../../component-map.yml).
Repository interfaces and concrete in-memory implementations remain in
Gateway persistence; the binding owner supplies the typed CAS calls and
postconditions rather than exposing a generic repository to operations.

The runtime returns typed before/after binding facts for ordinary selection,
Project binding, Thread binding, and Thread clearing. A foreground Thread bind
is split into a typed preparation fact and commit so composition can prepare
the projection route before CAS without taking ownership of binding mutation.
Preparation compares the exact Application/Project/Thread target and rejects
a same-target guard unless it is absent, the current generation, or the
immediately preceding generation. Commit then converges that accepted
same-target retry without a write or generation bump. A non-foreground,
generationless same-target bind remains an ordinary repository write and
advances the generation; generationless input is not a general idempotency key.

Block A preserves the current optimistic generation contract. The monotonic
binding generation, store-only action receipts, workflow CAS, and atomic
receipt/foreground-route transaction required by ADR 0016 belong to block B;
no local compatibility receipt or second persistence path is introduced here.

The historical `imagent.contracts` module is absent. The finite import-order bootstrap between
the binding and Gateway-operation leaves exists only to complete their one
closed union; it is not a compatibility implementation, service locator,
arbitrary module lookup, or second contract definition. Each resolved name is
cached as the same owner object.

## Execution boundary and recovery

The Gateway operation owner at
`src/imagent/gateway/routing/operations.py` validates the aggregate and
dispatches through typed binding methods; this binding owner performs
repository mutation, optimistic CAS, and same-target convergence. The
operations owner serializes one Conversation, while the projection-routing
owner controls foreground route changes and recovery policy. Those owners
preserve the one-current-binding and stable-generation guarantees.

Integration tests retain evidence for binding mutation/CAS, lock,
foreground-route, fan-out, and restart behavior. Binding state is bridge-owned
and repository-backed; it does not select a route, start observation, or
recover Application history.

If a foreground binding write raises after its outcome may be unknown, the
binding runtime reads the repository and compares the complete desired target.
Only a verified exact target may be treated as a possibly committed write for
route fencing. A different or absent current binding cannot authorize the
prepared route. If verification itself fails, the original failure is
preserved, annotated, and the route remains fenced; Gateway composition still
owns applying that returned fence fact to projection recovery.

## Physical boundary

The contract values and validators live in
`src/imagent/gateway/routing/bindings.py`, together with the private binding
runtime and its typed transition facts. The private
`gateway.orchestration` owner composes that runtime but retains no binding
repository/CAS or same-target implementation.
This leaf does not own `GatewayOperationType`, the mixed `GatewayOperation`
union, Gateway aggregate execution, Conversation locks, foreground route
preparation, or recovery. Aggregate Gateway validators call the exact
binding-owner validators, and composition calls the runtime's typed methods.
No new routing behavior is introduced. The historical cross-layer contracts
module is absent rather than retaining compatibility names.
