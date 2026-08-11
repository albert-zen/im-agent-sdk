# Interaction messages design

Component ID: `interaction.messages`

Parent: `interaction`

## Purpose

This leaf defines the immutable, content-bearing envelopes exchanged at the
IM bridge boundary. It keeps native inbound identity and outbound delivery
request identity distinct while preserving
ordered typed content.

## Ownership

This leaf owns:

- `InboundMessage` and `OutboundMessage` envelopes;
- `Content`, `TextContent`, `TextFormat`, and ordered content tuples;
- `ConversationRef`, `MessageRole`, `Metadata`, and `TextLengthUnit` values;
- the stable identity fields carried by each Interaction message kind.

It does not own:

- command parsing or control intent;
- Conversation binding, admission, routing, checkpoint, or idempotency state;
- native Channel encoding, delivery, receipt, or retry behavior;
- Application Thread, Turn, transcript, request, or execution truth;
- `AgentMessage`, which is an immutable, strong-`ThreadRef`-scoped
  Applications item consumed by history and canonical live-event
  normalization;
- attachment source trust or byte lifetime, which belong to
  [`interaction.media`](../media/design.md).

Applications compose the same `Content` values into their own `AgentMessage`
contract; that item is not defined or exported by this Interaction leaf.

## Inputs and outputs

Channel normalization supplies authenticated native message facts and ordered
typed content to produce `InboundMessage`. Gateway delivery supplies a stable
logical delivery identity and destination to produce `OutboundMessage`.
Applications compose the same `Content` values into their own `AgentMessage`
contract; that item is not defined or exported by this Interaction leaf.
Consumers receive frozen Python values or the equivalent language-neutral
schema values.

The two Interaction envelopes are not interchangeable:

| Envelope | Stable identity | Meaning |
|---|---|---|
| `InboundMessage` | native Channel `messageId` within its configured Channel/Conversation scope | one normalized IM input |
| `OutboundMessage` | Gateway `deliveryId` | one logical delivery request to a Conversation |

Text, timestamps, reply IDs, and Metadata never define identity or
deduplication. Content order is semantic and must survive normalization,
projection, and schema round trips.

## Contract and dependency boundary

`schemas/v1/common.schema.json` and `schemas/v1/messages.schema.json` are the
language-neutral shapes. The Python dataclasses and validators are the
reference implementation. Identifiers are non-empty and at most 512
characters; message content is non-empty at the schema boundary.

This is a lowest runtime value boundary. It depends only on
`interaction.media` to include the typed `AttachmentContent` variant in the
closed `Content` union; it has no dependency on Gateway or Applications
implementations. Other leaves may consume these values. A message never
becomes a generic mutable context bag: behavior-critical values require typed
fields, while Metadata remains passive extension data and is subject to the
bounded validation at the boundary that interprets or projects it.

Control intent is represented by typed Operations. A Slash command may be
recognized from inbound text by a Controller, but neither the grammar nor its
side effect belongs to this leaf.

## State and recovery

Message values are stateless and immutable. This leaf persists no message,
transcript, replay cursor, or delivery job. Recovery and idempotency use stable
Channel and Gateway identities owned by their respective components. The
Applications contract owns authoritative Agent history and its stable
`AgentMessage` identity.

## Current and target structure

The dependency-safe mechanical phase moves `ConversationRef`, text/content
values, `InboundMessage`, and `OutboundMessage` to the owning leaf. The
language-neutral message schema remains a cross-owner document and no schema
shape changes.

The mechanical target is:

```text
src/imagent/interaction/messages.py
tests/interaction/test_messages.py
```

`AgentMessage` is implemented by
`src/imagent/applications/contract.py`, where its strong `ThreadRef` scope and
history/live parity are authoritative. The schema remains intentionally split:
Interaction owns content/media definitions while Applications owns the
Agent-item definition that composes them.

The historical cross-layer `imagent.contracts` module is absent. Consumers and
repository runtime import this focused owner (or a focused package facade), and
no phase changes schema meaning or retains a second implementation.

## Authority

- [Vision](../../../VISION.md)
- [Architecture](../../../ARCHITECTURE.md)
- [Common protocol](../../contracts/protocol.md)
- [ADR 0001](../../../decisions/0001-contract-and-resource-foundations.md)
- [ADR 0002](../../../decisions/0002-design-authority-and-control-boundaries.md)
