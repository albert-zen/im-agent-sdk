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
to its single owning runtime. It does not own product commands, native adapter
internals, repository implementations, Channel access policy, Application
execution truth, or a generic pipeline/hook registry.

Inputs are explicitly configured Application and Channel instances plus typed
Controller, repository, delivery, authorization, limit, and extension
dependencies. The output is one `ImAgentGateway` with no dependency lookup at
runtime. Dependencies point to the public Interaction and Applications
contracts and the specific Gateway admission, input, projection, presentation,
delivery, persistence, and diagnostics leaves that consume the values.

The formal public contracts are `GatewayRepositories`, `GatewayLimits`, and
`GatewayExtensions`. They currently live in `imagent.gateway_composition` and
move to `imagent.gateway.composition`; the stable `imagent.gateway` facade may
re-export the exact same objects, but there must never be two implementations.

## Bounds and absence

`GatewayLimits` carries finite startup, recovery, projection, request,
extension, delivery-submission, and Conversation-serialization limits.
Leaf runtimes validate the limits they consume during construction, before
startup or external work. Invalid finite-capacity configuration fails
explicitly. `GatewayExtensions` contains only the four Gateway-owned ADR 0015
typed positions plus the Controller/request presenter composition points; A1
stays Application-owned. The group is not an `Any` context, callback stage
enum, or service bag.

Every optional repository or extension has an explicit default. Omitting an
ADR 0015 extension preserves the pre-extension path exactly and creates no
synthetic callback, diagnostic invocation, persistence, or side effect. The
grouped constructor is the one supported construction shape; composition does
not preserve an unlimited parallel flat-keyword API.

## State and recovery

The three groups are frozen process configuration and own no mutable or durable
state. Recovery belongs to the repository and runtime leaves receiving those
dependencies. Reconstructing an equivalent composition must not create a
second Application subscription, Channel admission path, transcript, Agent
runtime, content spool, or outbox.

## Current and target structure

Current implementation is split between `src/imagent/gateway_composition.py`
and construction in `src/imagent/gateway/__init__.py`. The target owner is
`src/imagent/gateway/composition.py`. This documentation slice changes no code;
the package root remains an explained split candidate until the focused
mechanical move leaves one implementation and exact facade identities.

## Authority

- [Vision](../../../VISION.md)
- [Architecture](../../../ARCHITECTURE.md)
- [Gateway aggregate](../design.md)
- [ADR 0006](../../../decisions/0006-core-admission-and-policy-ownership.md)
- [ADR 0015](../../../decisions/0015-typed-extension-seams-and-composition.md)
