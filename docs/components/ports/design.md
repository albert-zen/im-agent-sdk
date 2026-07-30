# Python ports component design

## Purpose

`src/imagent/adapters.py` defines the Python runtime seams that implementations
plug into. These Protocols carry language-neutral contract values but are not
the language-neutral protocol themselves.

## Ownership

Ports owns:

- the Python package's top-level typed public surface;
- `ChannelAdapter` lifecycle, inbound callback, and send signatures;
- `AgentApplicationAdapter` lifecycle, typed operation, input, and Thread
  subscription signatures;
- `BindingRepository`, `ProjectionRouteRepository`, and
  `IdempotencyRepository` interfaces;
- callback aliases shared by Gateway and integrations.

It does not own:

- resource/message/operation semantics or JSON Schema;
- native Channel or Agent Application behavior;
- repository implementations or storage format;
- Gateway orchestration, recovery policy, or product UX.

## Dependency direction

Ports imports only Contracts. Gateway, persistence implementations, recovery,
test kits, and concrete integrations may depend on Ports. Contracts never
depend on Ports.

Adding a method requires a real caller and at least one implementation. A
native-specific method stays on a concrete adapter until at least two
integrations prove a common port.

`ProjectionRouteRepository` owns explicit merge/advance and Turn-correlation
operations because these are common Gateway projection state across
Application and Channel implementations. It does not expose transcript or
native Turn mutation APIs.

## Change obligations

Changes to a Port require checking all implementations, fakes, type checking,
contract suites, and any component docs whose call flow changes. A method
signature change is a public Python API change even when JSON Schema is
unchanged.
