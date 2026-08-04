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

The current implementation still spreads these owners across contracts,
projection modules, and the Gateway package root. The machine-readable
[component map](../../component-map.yml) records those split candidates and
their target paths; these leaf documents define the boundary before the later
mechanical moves.
