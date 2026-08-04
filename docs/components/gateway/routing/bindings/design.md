# Gateway routing bindings design

## Purpose and ownership

`gateway.routing.bindings` owns the typed contract values for binding a
Conversation to a Project or Thread, clearing its Thread, and reporting the
result. It also owns their pure field/postcondition validation, the binding
repository/CAS authority, and same-target convergence. The Gateway package
root remains the current composition site for those typed binding-owner
methods until the later physical binding migration.

This leaf owns:

- typed project/Thread bind and Thread-clear operation/result values;
- pure binding-operation field validation;
- pure binding-result identity and postcondition validation;
- binding repository mutation and optimistic CAS; and
- same-target convergence, including its revisionless retry distinction.

It does not own Gateway aggregate dispatch, Conversation locks, foreground
projection route changes, recovery policy, Application Project or Thread
truth, native Thread activation, projection checkpoints, Application
observation workers, product command grammar, or a consumer's JSON display
cache. Controllers may submit the public typed actions, but product UX such as
`/new`, `/pick`, CWD, or profile selection stays outside Gateway routing.

## Contract and dependencies

A stable Conversation key and its one current binding are represented and
mutated through the binding owner. Multiple Conversations may independently
bind the same Application Thread. The binding repository stores only the
bridge-owned binding reference and revision; it never persists or copies
Project, Thread, transcript, Turn, or execution state. The current Gateway
root composes these binding-owner operations through explicit typed methods.

The public operation/result contracts are `BindConversationToProject`,
`BindConversationToThread`, `ClearConversationThread`, and
`ConversationBound`. Their focused implementation owner is
`imagent.gateway.routing.bindings`; `imagent.gateway.routing` and the stable
`imagent.contracts` facade re-export those exact objects. Their current and
target exports are recorded in the [component map](../../../component-map.yml).
Repository interfaces and concrete in-memory implementations remain in
Gateway persistence; the binding owner supplies the typed CAS calls and
postconditions rather than exposing a generic repository to operations.

`imagent.contracts` remains a finite exact public facade. Its runtime
`__getattr__` handles only the declared Gateway operation, binding, validator,
and delivery-facade names, while its `TYPE_CHECKING` branch imports the exact
public symbols for static typing. The finite import-order bootstrap between
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
preserve the one-current-binding and stable-revision guarantees.

Integration tests retain evidence for binding mutation/CAS, lock,
foreground-route, fan-out, and restart behavior. Binding state is bridge-owned
and repository-backed; it does not select a route, start observation, or
recover Application history.

## Physical boundary

The contract values and validators live in
`src/imagent/gateway/routing/bindings.py`; the current root composition site
invokes the binding owner's repository/CAS and same-target methods. This leaf
does not own `GatewayOperationType`, the mixed `GatewayOperation` union,
Gateway aggregate execution, Conversation locks, foreground route
preparation, or recovery. Aggregate Gateway validators call the exact
binding-owner validators and typed methods. No new routing behavior is
introduced, and the stable `imagent.contracts` facade preserves public object
identity without retaining retired binding names in `contracts.operations`.
