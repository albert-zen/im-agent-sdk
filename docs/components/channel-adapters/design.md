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
does not claim device display. When one platform call returns one native
message ID, the receipt preserves it. A segmented text or mixed
text/attachment delivery may produce several native IDs; the current singular
public field remains unset and the receipt detail reports the accepted count
rather than choosing a misleading ID.

The current native transports accept outbound attachments only after a
consumer or delivery component has materialized them as an explicit
`LocalPath` in a filesystem namespace trusted by that Channel instance.
`RemoteUrl` and `AttachmentHandle` are rejected rather than fetched or silently
dropped. General proactive Artifact materialization and delivery planning
remain Issues #11/#12 work.

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

The SDK owns the reusable QQ, Telegram, Feishu, and Weixin native transports,
media helpers, admission policy, and one common `channel_from_config` seam.
Protocol dependencies remain optional extras; importing Contracts, Ports, or
Gateway does not import them.

Native behavior and limitations are documented separately:

- [QQ](adapters/qq.md)
- [Telegram](adapters/telegram.md)
- [Feishu/Lark](adapters/feishu.md)
- [Weixin iLink](adapters/weixin.md)

The [Issue #9 transfer map](../../migrations/issue-9-imcodex-owner-transfer.md)
records source provenance and the owner-side/consumer-side acceptance split.
The later IMCodex migration must switch product composition and delete its old
copies before Issue #9 is complete.
