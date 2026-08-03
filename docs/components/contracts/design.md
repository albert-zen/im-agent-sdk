# Contracts component design

> Migration note: focused Interaction authority now lives in
> [messages](../interaction/messages/design.md) and
> [operations](../interaction/operations/design.md). This broad document
> remains current evidence for contract leaves that have not yet been split.

## Purpose

Contracts define the smallest language-neutral semantics shared between IM
Channels, the Gateway, Controllers, and Agent Applications. They let those
parts interoperate without turning the SDK into an Agent runtime or a product
framework.

## Ownership

This component owns:

- resource references and summaries;
- content envelopes and explicit attachment sources;
- typed Application and Gateway operations and results;
- capabilities and explicit error shapes;
- Agent events, ordering fields, history projections, bindings, projection
  route checkpoints, and minimal Turn reply-correlation value objects;
- Application input continuation preference, pre-dispatch disposition/policy,
  and truthful accepted-Turn result values;
- Python validators and the matching JSON Schemas.

It does not own:

- native project, Thread, Turn, transcript, request, or execution truth;
- Conversation binding mutations or output route policy;
- native Channel rendering and delivery;
- native Application API behavior;
- product-specific commands, provider/model settings, permissions, or
  workspace policy.

## Source of truth and state

The JSON Schemas are the language-neutral contract. The Python dataclasses,
unions, and validators are the reference implementation. Neither is a
database. Contract objects describe or reference state owned by the
appropriate system:

- Agent Application resources and events remain application-owned.
- Conversation bindings and Thread projection routes are Gateway-owned.
- native message IDs and delivery receipts are Channel observations.

Free-form Metadata is an extension surface only. Behavior-critical arguments,
success values, ordering guarantees, attachment locations, and common
capabilities require typed fields.

## Dependency direction

Contracts import no Python runtime port, Gateway, Controller, persistence, or
concrete adapter implementation. [Python Ports](../ports/design.md) depend on
contract types. Every other runtime component may depend on Contracts.

## Common-abstraction threshold

A proposed Agent-side semantic normally needs evidence from at least two real
Agent Applications. A proposed Channel behavior normally needs evidence from
at least two real Channels. One product's requirement remains in its
Controller, Adapter, or consumer integration until reuse is demonstrated.

## Change obligations

Changes under `src/imagent/contracts/` or `schemas/v1/` require checking:

- [protocol.md](protocol.md);
- [testing.md](testing.md);
- schema/Python discriminants and required fields remain aligned;
- every affected concrete and fake adapter;
- whether the change is cross-component enough to update an accepted ADR
  under `docs/decisions/`.
