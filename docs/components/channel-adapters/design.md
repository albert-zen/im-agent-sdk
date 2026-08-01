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
- native Markdown/cards/buttons/replies/mentions and escaping;
- platform API limits, credentials, native idempotency, rate-limit mapping,
  final validation, and receipts;
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

Gateway normally passes the adapter one segment produced from the Channel's
declared `DeliveryProfile`. The adapter performs native encoding, escaping,
upload/API calls, final platform validation, rate-limit interpretation, and
returns a `DeliveryReceipt`. The receipt reports native acceptance, rejection,
explicitly retryable failure, or unknown outcome; it does not claim device
display. When one platform call returns one native message ID, the receipt
preserves it.

An adapter may defensively split or reject an invalid direct call, but that is
not a second planning authority. Its advertised profile must make the common
planner's segment a valid native unit. Platform-specific cards or batching may
remain internal as long as receipts map every source content item truthfully.

Native artifact helpers also map each attempted attachment back to its stable
SDK `attachment_id`. Successful and permanently failed uploads become typed
`DeliveryItemReceipt` values, so a caller can distinguish partial delivery
without parsing adapter Metadata. Stable artifact idempotency derives from the
root delivery ID plus attachment ID, not a temporary local path.

The current native transports accept outbound attachments only after a
consumer or delivery component has materialized them as an explicit
`LocalPath` in a filesystem namespace trusted by that Channel instance.
`RemoteUrl` and `AttachmentHandle` are rejected rather than fetched or silently
dropped. The optional proactive ingress materializes inline bytes into this
trusted `LocalPath` boundary. Common segmentation/grouping and ordered bounded
execution are defined by ADR 0010; native throttling and response mapping stay
here.

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
