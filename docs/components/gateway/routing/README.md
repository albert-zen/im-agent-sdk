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

The binding contract slice now has its focused owner at
`src/imagent/gateway/routing/bindings.py`, with the declared
`imagent.gateway.routing` facade and exact historical `imagent.contracts`
re-exports. Mutation, compare-and-swap, Conversation serialization, and route
preparation remain in their existing persistence, projection, and Gateway
orchestration owners. Gateway operations and projection routes remain pending
later one-slice migrations recorded in the machine-readable
[component map](../../component-map.yml).
