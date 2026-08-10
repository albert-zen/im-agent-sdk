# Gateway composition design

Component ID: `gateway.composition`

Parent: `gateway`

## Purpose

Composition assembles one explicit `ImAgentGateway` object graph from typed
Applications, Channels, repositories, limits, and optional extension seams.
It is a wiring boundary, not a service locator or a fourth business layer.

## Ownership

This leaf owns the immutable `GatewayRepositories`, `GatewayLimits`, and
`GatewayExtensions` construction values and the wiring that passes each value
to its documented owner or read-only consumer. It does not own product
commands, native adapter internals, repository implementations, Channel access
policy, Application execution truth, or a generic pipeline/hook registry.

The configured binding repository is constructor-injected into the sole
mutating `gateway.routing.bindings` runtime. Projection-route policy receives
the same repository only as the read authority needed to test foreground
binding equality; `ImAgentGateway` itself does not retain or call the binding
repository. Composition still sequences Application Project/Thread truth,
binding transition facts, and foreground projection-route preparation without
becoming a second mutation owner.

Inputs are explicitly configured Application and Channel instances plus typed
Controller, repository, delivery, authorization, limit, and extension
dependencies. The output is one `ImAgentGateway` with no dependency lookup at
runtime. Dependencies point to the public Interaction and Applications
contracts and the specific Gateway admission, input, projection, presentation,
delivery, persistence, and diagnostics leaves that consume the values.

The formal public contracts are `GatewayRepositories`, `GatewayLimits`, and
`GatewayExtensions`. Their single implementation lives in
`imagent.gateway.composition`; the stable `imagent.gateway` facade re-exports
the exact same objects. The removed `imagent.gateway_composition` internal
module has no compatibility shim, so there can never be two implementations.

## Bounds and absence

`GatewayLimits` carries finite startup, recovery, projection (including active
Thread-observation), request, extension, in-memory idempotency, delivery-submission, and
Conversation-serialization limits. Leaf runtimes validate the limits they
consume during construction, before startup or external work. The default
process-local idempotency repository receives its positive record bound only
from this group; an explicitly supplied repository remains unmodified. Invalid
finite-capacity configuration fails explicitly. `GatewayExtensions` contains
only the four Gateway-owned ADR 0015
typed positions plus the Controller/request presenter composition points; A1
stays Application-owned. The group is not an `Any` context, callback stage
enum, or service bag.

Every optional repository or extension has an explicit default. Omitting an
ADR 0015 extension preserves the pre-extension path exactly and creates no
synthetic callback, diagnostic invocation, persistence, or side effect. The
grouped constructor is the one supported construction shape; composition does
not preserve an unlimited parallel flat-keyword API.

The D ordinary-input integration accepts a Controller only when the retiring
composition is already backed by one lease-bound `GatewayStoreSession` used as
bindings, projections, idempotency, request correlations, and delivery
submissions together. Every optional repository must be absent or that exact
object; mixing the session with any other repository fails during construction.
Gateway then constructs `StoreBackedGatewayEffectExecutor` and exposes only the
resulting `ConversationActions` to Controller code. This focused integration
does not expose the private session or turn `GatewayRepositories` into the v1
public store port; final `GatewayStore` acquisition and lifecycle ownership
remain later DAG composition work.

`projection_max_active_threads` is passed only to the Thread observation
runtime. It bounds distinct stable `ThreadRef` workers in one Gateway process:
an existing, starting, or in-flight same-Thread admission joins before
admission, while a new Thread at capacity fails before its Application
subscription or projection work begins. The admission lease remains keyed to
that Thread through task turnover until the caller completes, so a distinct
Thread cannot consume the final slot after route preparation. The bound never
persists a slot or replaces route/checkpoint authority; terminal worker cleanup
discards worker-owned health, while accepted-input fence state remains owned by
the final input owner until it has safely drained.

## State and recovery

The three groups are frozen process configuration and own no mutable or durable
state. Recovery belongs to the repository and runtime leaves receiving those
dependencies. Reconstructing an equivalent composition must not create a
second Application subscription, Channel admission path, transcript, Agent
runtime, content spool, or outbox.

## Current structure and remaining split

`src/imagent/gateway/composition.py` is the current and sole owner of the
three composition values. `src/imagent/gateway/controller_input.py` privately
adapts the exact Application/binding/effect callbacks required by C and creates
the inbound scoped surface; it exposes no public factory or runtime object.
`src/imagent/gateway/__init__.py` imports those exact values for the stable
package facade and still contains `ImAgentGateway` runtime orchestration.
Separating that remaining package-root facade/runtime combination is a later
mechanical slice; it does not duplicate the composition values or alter their
grouped constructor API.

## Authority

- [Vision](../../../VISION.md)
- [Architecture](../../../ARCHITECTURE.md)
- [Gateway aggregate](../design.md)
- [ADR 0006](../../../decisions/0006-core-admission-and-policy-ownership.md)
- [ADR 0015](../../../decisions/0015-typed-extension-seams-and-composition.md)
