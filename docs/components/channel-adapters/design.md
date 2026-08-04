# Channel adapters component design

## Purpose

A Channel adapter translates one configured IM bot/account instance between
native messages/actions and the common Channel Port.

This document is the historical Channel-adapters navigation surface. The
focused Interaction Channel leaves are authoritative for shared contract,
ingress, outbound-delivery, and native-adapter ownership; this page records
the provider-facing boundary and links to those leaves rather than defining a
second Channel model.

## Formal Channel facades

The sole formal facade for Channel contract, admission, capability, profile,
delivery-support, receipt, and receipt-validation values is
`imagent.interaction.channels`. The historical `imagent.adapters` facade no
longer exports `ChannelAdapter`, `ChannelStartupConfigurationValidator`,
`MessageHandler`, `InboundAdmission`, or `InboundAdmissionHandler`; the
historical `imagent.contracts` facade no longer exports the Channel capability,
profile, support, reply-scope, receipt/status, or receipt-validation names.
Those historical facades retain unrelated Application/Gateway,
proactive-authorization, and passive-state names only. `imagent.channels`
remains the intended exact-object adapter facade for
`NativeTransportChannelAdapter` and `channel_from_config`.

## Ownership

Channel adapters own:

- credentials, authenticated connection lifecycle, and native reconnect
  tokens;
- native account, Conversation, sender, and message identity;
- signature/authentication verification and provider-specific access-control
  inputs;
- native Markdown/cards/buttons/replies/mentions and escaping;
- platform API limits, credentials, native idempotency, rate-limit mapping,
  final validation, and receipts;
- real platform capabilities and limits.

Shared admission/normalization, access-denial bounds, media staging, delivery
helpers, outbound access enforcement, and receipt contract values remain owned
by the focused Interaction Channel leaves. Native adapters invoke those
boundaries and retain provider policy; they do not add a second admission,
delivery, or contract implementation. `BaseChannelAdapter` is a thin native
delegator; its route context and diagnostic state remain adapter-owned, and
diagnostic emission is not part of this mechanical ownership move. Its inbound
dispatch retains the existing virtual access/report/diagnostic call order
before leaf-owned admitted handoff, while outbound access retains virtual
route-user resolution and lazy conversation fallback.

They do not own:

- Agent Application resources, transcript, Turns, requests, or approvals;
- Conversation bindings or Thread projection routes;
- common Slash product behavior;
- model/provider/workspace/sandbox policy.

## Inbound flow

1. Verify the native event or authenticated connection.
2. Normalize stable identities.
3. Apply sender/Conversation access policy.
4. Request a Gateway-owned durable admission lease from the stable
   Conversation/message identity; reject completed or in-flight duplicates.
5. Stage permitted media and emit an explicit `AttachmentSource`.
6. Hand one completed `InboundMessage` through the lease, or release the lease
   when preparation fails before handoff.

Access and deduplication happen before attachment download and Agent mutation.
The native process-local duplicate set is only a fast path. The durable
admission lease, defined by ADR 0011, is opaque to the Channel: after handoff,
Gateway alone completes, protects, or releases it according to the Application
side-effect outcome. A durable rejection removes the transient key so a later
provider redelivery can retry after an abandoned claim becomes reclaimable.

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

Those shared batch, receipt, identity, and caller-managed trusted-root read
helpers belong to Interaction outbound delivery. Adapters supply only the
provider-specific `send_one` operation. A retained in-memory artifact suffix
after cancellation or an unclassified exception describes the current native
attempt; it is not an SDK durable outbox or permission to replay an ambiguous
effect.

The current native transports accept outbound attachments only after a
consumer or delivery component has materialized them as an explicit
`LocalPath` in a filesystem namespace trusted by that Channel instance.
`RemoteUrl` and `AttachmentHandle` are rejected rather than fetched or silently
dropped. The optional proactive ingress materializes inline bytes into this
trusted `LocalPath` boundary. Common segmentation/grouping and ordered bounded
execution are defined by ADR 0010; native throttling and response mapping stay
here. Bytes, root lifetime, product quota, ledger, startup sweep, and crash-safe
cleanup remain caller/consumer-owned.

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
the focused Interaction ingress/outbound helpers, and one common
`channel_from_config` seam. The common native wrapper and factory live in the
Interaction adapter leaf; `imagent.channels` is their stable formal adapter
facade rather than another runtime implementation. Protocol dependencies
remain optional extras; importing Contracts, Ports, Gateway, or either adapter
facade does not import them.

`channel_from_config` also exposes the optional structural
`ChannelStartupConfigurationValidator` capability. Its
`validate_startup_configuration()` method invokes the native class's pure
configuration validator over the same snapshotted, resolved settings used by
`start()`. It does not construct or install a native adapter, allocate an HTTP
client or transport, start workers, register callbacks, publish credentials,
or mutate persistent state. Validation is repeatable before start, while
stopped, and after a completed lifecycle. Native configuration errors remain
explicit and bounded. A third-party Channel may omit this capability entirely;
it is not a method on the common `ChannelAdapter` lifecycle Port.
Every `NativeTransportChannelAdapter` requires its validator at construction,
so structural capability detection cannot report support that later degrades
to `NotImplementedError`.

Startup validation reports whether resolved local settings and prerequisites
are safe to start. It is neither live connection health under ADR 0014 nor a
message-pipeline extension under ADR 0015, and Gateway never invokes it on a
Channel socket read path.

Separately, QQ, Telegram, Feishu, and Weixin expose optional ADR 0014
`diagnostic_facts()` through the SDK native wrapper. Reads use only local
immutable lifecycle/worker snapshots and fixed queue counters; they never run
native I/O. QQ and Feishu report their bounded inbound queue, while Telegram
and Weixin report none. This is not startup validation or an ADR 0015 message
extension.

The native Channel fact/state implementation lives with the Interaction
adapter owner. Its public `ChannelDiagnosticFacts` and
`ChannelDiagnosticsProvider` contracts are owned by
`imagent.interaction.channels.diagnostics`; the common connection/queue
vocabulary is owned by `imagent.interaction.diagnostics`. This is distinct
from the public Gateway aggregate diagnostics and from media staging debug
logs; no compatibility implementation remains under the historical native
package.

Native behavior and limitations are documented separately:

- [QQ](adapters/qq.md)
- [Telegram](adapters/telegram.md)
- [Feishu/Lark](adapters/feishu.md)
- [Weixin iLink](adapters/weixin.md)

The [Issue #9 transfer map](../../migrations/issue-9-imcodex-owner-transfer.md)
records source provenance and the owner-side/consumer-side acceptance split.
The later IMCodex migration must switch product composition and delete its old
copies before Issue #9 is complete.
