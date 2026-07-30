# Channel adapters component design

## Purpose

A Channel adapter translates one configured IM bot/account instance between
native messages/actions and the common Channel Port.

## Ownership

Channel adapters own:

- credentials, authenticated connection lifecycle, and native reconnect
  tokens;
- native account, Conversation, sender, and message identity;
- signature/authentication verification and access-control inputs;
- admission, normalization, duplicate checks, and media staging;
- native Markdown/cards/buttons/replies/mentions;
- chunking, escaping, rate limits, native idempotency, retries, and receipts;
- real platform capabilities and limits.

They do not own:

- Agent Application resources, transcript, Turns, requests, or approvals;
- Conversation bindings or Thread projection routes;
- common Slash product behavior;
- model/provider/workspace/sandbox policy.

## Inbound flow

1. Verify the native event or authenticated connection.
2. Normalize stable identities.
3. Apply sender/Conversation access policy.
4. Reject duplicates before expensive work.
5. Stage permitted media and emit an explicit `AttachmentSource`.
6. Emit `InboundMessage` or a typed interaction.

Access and deduplication happen before attachment download and Agent mutation.

## Outbound flow

The adapter accepts `OutboundMessage`, converts Markdown or falls back to plain
text, segments within native limits, applies rate limits, and returns a
`DeliveryReceipt`. The receipt reports native acceptance/rejection/unknown; it
does not claim device display.

Completed Agent messages are the default IM unit. Token-by-token native
messages are not a common requirement.

## Attachments and trust

Channel staging uses the shared
[attachments/media boundary](../attachments-and-media/design.md). A staged
`LocalPath` does not grant filesystem trust. Platform upload/download,
credentials, media limits, and native handles remain Channel-owned.

## Failure and reconnect

Native authentication, access, rate-limit, transport, size, formatting, and
receipt failures remain explicit. Channel reconnect cursors belong to the
Channel adapter; they are not Agent event replay cursors.

## Current implementation

The current seam imports pinned IMCodex Channel implementations. See
[adapters/imcodex.md](adapters/imcodex.md). Issue #9 will transfer ownership
into this repository before IMCodex becomes a downstream composition; that
work must preserve provenance and avoid a permanent dual implementation.
