# Gateway routing components

Gateway routing keeps three bridge-owned decisions separate: the current
Conversation input binding, typed Gateway operation execution, and outbound
Thread projection routes. Native Thread activation remains an Application
operation, and observing a Thread never selects it for input.

## Leaf navigation

- [bindings design](bindings/design.md) and [testing](bindings/testing.md) —
  the single current Application/Project/Thread selection for a Conversation.
- [Gateway operations design](gateway-operations/design.md) and
  [testing](gateway-operations/testing.md) — typed validation and serialized
  execution of Gateway-owned control intent.
- [projection routes design](projection-routes/design.md) and
  [testing](projection-routes/testing.md) — stable outbound destination edges
  and the three accepted projection policies.

The binding contract slice has its focused owner at
`src/imagent/gateway/routing/bindings.py`, including its sole private
repository/CAS and same-target convergence runtime. Gateway operation
contracts, validation, dispatch, and bounded Conversation serialization have
their focused owner at `src/imagent/gateway/routing/operations.py`, and
projection route values, policy, validation, identity, activation, and
persistence have their focused owner at
`src/imagent/gateway/routing/projection_routes.py`.
The declared `imagent.gateway.routing` facade re-exports the exact owner
objects. `imagent.contracts` retains only the aggregate, binding, request, and
Interaction contracts it still owns or deliberately exposes; it does not
retain the moved `ObserveThread` or `ThreadObserved` names. Binding repository
mutation, optimistic compare-and-swap, and same-target convergence remain
binding-owned; route policy and persistence remain projection-route-owned; and
request response validation, transition fences, correlation persistence, and
replay remain request-correlation-owned. Operations delegates through typed
owner methods and owns neither generic repositories nor those concrete
validators.
