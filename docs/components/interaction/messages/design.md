# Interaction messages design

Component ID: `interaction.messages`

Parent: `interaction`

## Purpose

This leaf defines the immutable, content-bearing envelopes exchanged at the
IM bridge boundary. It keeps native inbound identity, outbound delivery
request identity, and canonical Agent item identity distinct while preserving
ordered typed content.

## Ownership

This leaf owns:

- `InboundMessage`, `OutboundMessage`, and `AgentMessage` envelopes;
- `Content`, `TextContent`, `TextFormat`, and ordered content tuples;
- `ConversationRef`, `MessageRole`, `Metadata`, and `TextLengthUnit` values;
- the stable identity fields carried by each message kind.

It does not own:

- command parsing or control intent;
- Conversation binding, admission, routing, checkpoint, or idempotency state;
- native Channel encoding, delivery, receipt, or retry behavior;
- Application Thread, Turn, transcript, request, or execution truth;
- attachment source trust or byte lifetime, which belong to
  [`interaction.media`](../media/design.md).

`AgentMessage` is the canonical content envelope used to project one
Application-owned item. Carrying an opaque `ThreadRef` and `agentItemId` does
not make this leaf a transcript or Thread authority.

## Inputs and outputs

Channel normalization supplies authenticated native message facts and ordered
typed content to produce `InboundMessage`. Gateway delivery supplies a stable
logical delivery identity and destination to produce `OutboundMessage`.
Application adapters normalize authoritative native items into
`AgentMessage`. Consumers receive frozen Python values or the equivalent
language-neutral schema values.

The three envelopes are not interchangeable:

| Envelope | Stable identity | Meaning |
|---|---|---|
| `InboundMessage` | native Channel `messageId` within its configured Channel/Conversation scope | one normalized IM input |
| `OutboundMessage` | Gateway `deliveryId` | one logical delivery request to a Conversation |
| `AgentMessage` | Application `agentItemId` within its Thread scope | one canonical authoritative Agent item |

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
Channel, Gateway, and Application identities owned by their respective
components. Authoritative Agent history may reproduce the same
`AgentMessage`; that reproduction must retain its stable item identity rather
than deduplicate by content or time.

## Current and target structure

Current message values are physically mixed with Application, Gateway,
capability, and request values in `src/imagent/contracts/model.py`; the
language-neutral message schema also contains Application input values. These
paths are declared split candidates, not multi-owner components.

The mechanical target is:

```text
src/imagent/interaction/messages.py
tests/interaction/test_messages.py
```

The later move must preserve the deliberate public facade, schema meaning, and
exact symbol ownership recorded in the component map. It must not retain a
second implementation or use an import cycle to make Interaction depend on an
Application or Gateway implementation.

## Authority

- [Vision](../../../VISION.md)
- [Architecture](../../../ARCHITECTURE.md)
- [Common protocol](../../contracts/protocol.md)
- [ADR 0001](../../../decisions/0001-contract-and-resource-foundations.md)
- [ADR 0002](../../../decisions/0002-design-authority-and-control-boundaries.md)
