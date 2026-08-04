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
`src/imagent/gateway/routing/bindings.py`, and Gateway operation contracts,
validation, dispatch, and bounded Conversation serialization have their
focused owner at `src/imagent/gateway/routing/operations.py`. The declared
`imagent.gateway.routing` facade and exact finite `imagent.contracts` facade
re-export those owner objects. Binding repository mutation, optimistic
compare-and-swap, and same-target convergence remain binding-owned; route
policy and persistence remain projection-route-owned; and request response
validation, transition fences, correlation persistence, and replay remain
request-correlation-owned. Operations delegates through typed owner methods
and owns neither generic repositories nor those concrete validators.
Projection-route implementation remains the next one-slice migration recorded
in the machine-readable [component map](../../component-map.yml).
