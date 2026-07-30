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
- `DeliveryAuthorizer` and `DeliverySubmissionRepository` interfaces for
  scoped proactive delivery, immutable route snapshots, and typed outcomes;
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
native Turn mutation APIs. Route refresh preserves an omitted checkpoint and
rejects a conflicting explicit value. Checkpoint advance is compare-and-swap
against an expected opaque Agent item ID; implementations never infer ordering
from that ID. Correlation bulk deletion requires at least one explicit
selector.

`DeliveryAuthorizer.authenticate` converts an opaque untrusted credential into
a trusted `DeliveryPrincipal`; the caller cannot declare its own effective
scope. `DeliverySubmissionRepository.reserve_delivery_submission` is atomic
and stores identity/snapshots/outcomes only. It is intentionally not a queue,
content store, or retry scheduler.

## Change obligations

Changes to a Port require checking all implementations, fakes, type checking,
contract suites, and any component docs whose call flow changes. A method
signature change is a public Python API change even when JSON Schema is
unchanged.
