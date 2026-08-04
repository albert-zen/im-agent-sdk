# Gateway operations design

## Purpose and ownership

`gateway.routing.gateway-operations` owns the shared Gateway discriminator and
base, the closed aggregate operation/result unions, the Gateway-owned list and
select values, aggregate failure values, aggregate validation, typed dispatch,
and bounded Conversation serialization.

This leaf owns:

- the shared `GatewayOperationType` and operation/result bases;
- `GatewayOperation` and `GatewayOperationResult` aggregate unions;
- `ListApplications`, `SelectApplication`, `ApplicationsListed`, and
  `GatewayOperationFailed`;
- aggregate field, discriminant, identity, and postcondition validation;
- typed dispatch and finite per-Conversation execution serialization;
- delegation to the binding, projection-route, and request-correlation owners
  through explicit typed methods or ports. `ApplicationOperation` remains a
  separate closed contract and is not a Gateway aggregate variant.

The composition implementation is the private
`_GatewayOperationExecutor`. It is not an accepted public contract and is not
re-exported by `imagent.gateway.routing` or the Gateway package facade. Its
constructor is used only by the Gateway root with the private, statically
typed delegate port; consumers receive the existing `ControllerActions` and
Gateway methods instead.

It does not own slash syntax, product commands, permissions, presentation,
native Application operation semantics, or native Application state. It also
does not own binding values, binding repository/CAS or same-target authority;
projection observation values, route policy or route persistence;
request-response values, response validation, claim/transition fences,
correlation persistence or replay; or generic persistence repositories. A
Controller recognizes interaction grammar and invokes typed actions;
`ControllerActions` is the public composition path. Application operations
remain a separate closed contract and enter through the existing
`ImAgentGateway.execute_application` and `ControllerActions.execute_application`
paths; the Gateway aggregate does not dispatch or call them.

## Contract and execution

Every variant has behavior-specific inputs and a behavior-specific success
result. Free-form metadata, `Any` contexts, service locators, generic pipeline
hooks, and stage callbacks are not extension mechanisms. Unknown operation
types and unsupported capabilities fail explicitly instead of falling back to
text commands or approximate native behavior.

Conversation-scoped mutations execute under the stable Conversation key so
local contenders cannot reorder binding, route, or response authority. Work
for unrelated Conversations is not forced through one global lock. Read-only
listing returns normalized Application-owned resource facts and performs no
binding, route, activation, or observation mutation.

The process-local keyed registry admits only a configured finite number of
distinct active Conversation keys. An already-active key may add a waiter at
that limit so serialization cannot split. A new key fails before Controller,
Application, binding, route, or Channel side effects with the stable retryable
`capacity_exhausted` operation error. Entries disappear after their last owner
or waiter exits, including cancellation; an occupied or awaited entry is never
evicted to manufacture capacity.

For claimed inbound input, capacity rejection is a known pre-acceptance
failure after durable admission but before native dispatch. The existing I2
rules remain authoritative: without I2 the owned claim is released and the
exception is raised; with I2 the claim is completed before terminal error
presentation. Presenter or Channel failure cannot reopen the input.

Replay behavior is operation-specific rather than a generic `operation_id`
deduplication guarantee. Operations preserves the existing same-target and
revisionless distinctions by dispatching to the binding, route, and request
owners; it does not infer or implement their postconditions. Unknown outcomes
fail explicitly unless the delegated owner documents a state comparison that
proves convergence. Gateway does not authorize a blind repeat or manufacture
a general durable operation log.

## Dependency boundary

Gateway operations call explicit typed owner methods or ports for binding,
projection-route, and request-correlation. The closed aggregate union imports
the exact projection-route operation/result values from
`gateway.routing.projection_routes` and the still-pending request values from
their request-correlation owner; it does not define a second union or preserve
the moved route values in `imagent.contracts`. They do not dispatch
`ApplicationOperation`, receive a generic repository/context object, or perform
owner mutation or concrete validation. Root binding delegates may query
Application truth through their existing typed root methods; that is binding
composition, not Gateway aggregate dispatch. The projection-route
implementation and policy are in their focused leaf; this owner invokes its
typed route port and does not create a second route authority or observation
worker. Interaction
Controllers depend on exact public typed actions, never on this leaf's
implementation or Gateway context.

## Physical owner and import boundary

The canonical implementation is
`src/imagent/gateway/routing/operations.py`. The Gateway package root composes
that owner and retains only broader admission, input, presentation, delivery,
diagnostics, and lifecycle orchestration. Application operation variants stay
in `src/imagent/applications/operations.py`.

`imagent.contracts` and `imagent.gateway.routing` remain finite exact public
facades. Their import-order bootstrap may resolve a partially initialized
binding leaf so the closed Gateway union is completed once both exact owners
are loaded. The retained `imagent.gateway` package root has a separate finite
lazy Gateway-operation resolver for exactly `ApplicationsListed`,
`GatewayOperation`, `GatewayOperationFailed`, `GatewayOperationResult`,
`GatewayOperationType`, `ListApplications`, `SelectApplication`,
`validate_gateway_operation`, and `validate_gateway_operation_result`. It
obtains each exact object from `imagent.gateway.routing.operations` only on
access and caches that object on the root, so every supported import order
preserves exact identity, signatures, and runtime annotations. This keeps the
root's aggregate exports out of package initialization until the closed owner
aggregate has completed. The resolver is not a compatibility implementation,
service locator, or second operation definition; unknown names fail explicitly.
