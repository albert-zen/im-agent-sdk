# Gateway routing bindings design

## Purpose and ownership

`gateway.routing.bindings` owns the typed contract values for binding a
Conversation to a Project or Thread, clearing its Thread, and reporting the
result. It also owns only the pure field and binding-postcondition validators
for those values. The leaf does not own the current selection itself; that
authority remains in the existing Gateway operation and persistence owners.

This leaf owns only:

- typed project/Thread bind and Thread-clear operation/result values;
- pure binding-operation field validation; and
- pure binding-result identity and postcondition validation.

It does not own `ConversationBinding` persistence or authority, optimistic CAS,
repository mutation, Conversation locks, binding-equality authority,
foreground projection route changes, recovery policy, Application Project or
Thread truth, native Thread activation, projection checkpoints, Application
observation workers, product command grammar, or a consumer's JSON display
cache. Controllers may submit the public typed actions, but product UX such as
`/new`, `/pick`, CWD, or profile selection stays outside Gateway routing.

## Contract and dependencies

A stable Conversation key and its one current binding are represented and
mutated by existing Gateway owners. Multiple Conversations may independently
bind the same Application Thread. This leaf validates only the typed fields,
stable Conversation identity, and returned binding postconditions; it never
persists references or copies Project, Thread, transcript, Turn, or execution
state.

The public operation/result contracts are `BindConversationToProject`,
`BindConversationToThread`, `ClearConversationThread`, and
`ConversationBound`. Their focused implementation owner is
`imagent.gateway.routing.bindings`; `imagent.gateway.routing` and the stable
`imagent.contracts` facade re-export those exact objects. Their current and
target exports are recorded in the [component map](../../../component-map.yml).
Repository contracts live in Gateway persistence; concrete repositories do
not become part of this leaf.

`imagent.contracts` remains a finite exact public facade. Its runtime
`__getattr__` handles only the declared Gateway operation, binding, validator,
and delivery-facade names, while its `TYPE_CHECKING` branch imports the exact
public symbols for static typing. This finite cycle break exists only because
the legacy operation module still assembles a mixed union; it is
not a compatibility implementation, service locator, arbitrary module
lookup, or second contract definition. Each resolved name is cached as the
same owner object.

## Execution boundary and recovery

The existing Gateway operation owner validates and dispatches these values;
the persistence owner performs repository mutation and optimistic CAS; the
existing Gateway locks serialize one Conversation; and the projection-routing
owner controls binding equality, foreground route changes, and recovery policy.
Those owners preserve the one-current-binding and stable-revision guarantees.

Integration tests retain evidence for those existing mutation, CAS, lock,
foreground-route, fan-out, and restart owners. The binding leaf itself is
stateless and does not select a route, start observation, or recover history.

## Physical boundary

This issue is a mechanical contract-owner extraction. The module contains
only the binding operation/result dataclasses and pure binding field/
postcondition validators. It does not own `GatewayOperationType`, the mixed
`GatewayOperation` union, Gateway operation execution, repository CAS,
Conversation locks, binding equality, foreground route preparation, or
recovery. Aggregate Gateway validators and the existing Gateway owners call
the exact binding-owner objects. No new routing behavior is introduced, and
the stable `imagent.contracts` facade preserves public object identity without
retaining retired binding names in `contracts.operations`.
