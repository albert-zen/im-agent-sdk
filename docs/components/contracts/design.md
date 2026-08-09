# Contracts component design

> Migration note: focused Interaction authority now lives in
> [messages](../interaction/messages/design.md) and
> [operations](../interaction/operations/design.md), with attachment source
> and trust authority in [media](../interaction/media/design.md). Application
> capability authority now lives in
> [Applications capabilities](../applications/capabilities/design.md). This
> broad document remains current evidence for the versioned schema union and
> Gateway-owned bridge values that have not yet been physically split.

## Purpose

Contracts define the smallest language-neutral semantics shared between IM
Channels, the Gateway, Controllers, and Agent Applications. They let those
parts interoperate without turning the SDK into an Agent runtime or a product
framework.

## Ownership

This component records the remaining cross-owner schema and facade surface; it
does not own the Application model family. The focused Gateway routing leaf
now owns Gateway operation values and validators; this component retains the
following cross-owner surface:

- Conversation bindings, projection routes, and minimal Turn reply-correlation
  state;
- the cross-owner language-neutral schema union and any validators that still
  belong to other leaves; and
- the deliberate finite `imagent.contracts` public facade.

Channel contract, admission, capability/profile, and delivery-receipt values
are deliberately excluded from that historical facade. Their sole formal
Python surface is `imagent.interaction.channels`; the separate
`imagent.channels` facade is reserved for exact native adapter objects.

The complete Application model family is owned by
[`applications.application-contract`](../applications/application-contract/design.md):
`Page`, `ApplicationRef`, `ProjectRef`, `ThreadRef`, input continuation and
dispatch values, Project/Thread/Turn summaries and statuses, `AgentInput`,
`AgentMessage`, history/catch-up/snapshot values, `AcceptedTurn`,
`ApplicationInputDispatch`, `ApplicationInputOutcomeUnknown`, and
`validate_thread_ref`.

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

- Agent Application resources, items, Turns, history, and events remain
  application-owned; the typed Python model family lives in the Applications
  contract owner.
- Conversation bindings and Thread projection routes are Gateway-owned.
- native message IDs and delivery receipts are Channel observations.

Free-form Metadata is an extension surface only. Behavior-critical arguments,
success values, ordering guarantees, attachment locations, and common
capabilities require typed fields.

## Dependency direction

Contracts import no Python runtime port, Gateway orchestration, Controller,
persistence, or concrete adapter implementation. The finite
`imagent.contracts` facade resolves exact Gateway operation and binding owners
without implementing them. Its permanent finite
`ConversationBinding` delegate resolves to the Gateway persistence-state owner.
The scoped action surface imports that owner directly; the facade remains a
finite exact object facade, not a compatibility model or an additional binding
contract. [Python
Ports](../ports/design.md) depend on contract types. Every other runtime
component may depend on the public contract facade.

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
