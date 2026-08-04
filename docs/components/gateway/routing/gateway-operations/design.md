# Gateway operations design

## Purpose and ownership

`gateway.routing.gateway-operations` owns the closed, strongly typed set of
Gateway control intents and their typed results. It validates and dispatches
selection, observation, listing, and Conversation-scoped request-routing
operations under the Gateway's bounded orchestration rules.

This leaf owns:

- `GatewayOperation` and `GatewayOperationResult` variants and validators;
- stable operation identity and explicit unsupported/failure results;
- per-Conversation execution serialization;
- dispatch to the binding, projection-route, and request-correlation owners.

It does not own slash syntax, product commands, permissions, presentation,
native Application operation semantics, or native Application state. A
Controller recognizes interaction grammar and invokes typed actions;
`ControllerActions` is the public composition path. Application operations
remain a separate closed contract and are delegated to the owning Application
rather than reclassified as Gateway operations.

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

Replay behavior is operation-specific rather than a generic `operation_id`
deduplication guarantee. A mutation may converge after retry only where its
typed postcondition and repository contract prove the complete requested state
(for example, the same-target binding rule). Revisionless operations may write
a new revision again. Unknown outcomes fail explicitly unless that operation's
documented state comparison proves convergence; Gateway does not authorize a
blind repeat or manufacture a general durable operation log.

## Dependency boundary

Gateway operations may call the binding and projection-route leaves and the
minimal request-correlation transition API. Application operations cross only
the public Application contract. Interaction Controllers depend on exact
public typed actions, never on this leaf's implementation or Gateway context.

## Current structural gap

Gateway and Application operation variants currently share broad contract and
validator modules, while execution remains in the Gateway package root. The
current per-Conversation lock table also retains every observed Conversation
for process lifetime; the target owner requires a bounded, concurrency-safe
lifetime policy without evicting an in-use lock. Later focused slices will
separate the owner paths, close that capacity gap, and update all imports
without leaving two internal operation models.
