# Native Channel adapters design

## Purpose and ownership

This leaf contains concrete QQ, Telegram, Feishu/Lark, and Weixin adapters. An
adapter owns native credentials, authenticated connection/client lifecycle,
provider event/response mapping, platform policy/limits, reconnect cursor,
native acknowledgement, optional startup validation, and bounded ADR 0014
diagnostic facts.

It does not own Gateway binding/admission persistence/planning/retry,
Application execution, common/product commands, transcript, or a general
Channel plugin runtime.

## Shared shape and native differences

All built-ins implement the same Channel contract and call the shared
ingress/delivery ordering and validation helpers. Shared leaves may cover
access evaluation, media/file validation, bounded text/artifact mechanics,
stable receipt correlation, and runtime lifecycle where two or more real
Channels prove the semantics. Concrete adapters exclusively own provider
authentication/signatures, API encoding/escaping, credentials, native
upload/download/decryption/acknowledgement, URL rules, QR/token state, rate
limit/response mapping, and diagnostic worker facts.

`runtime.py` owns the common native-transport wrapper and `channel_from_config`
factory inside this leaf. Public `OutboundMessage` and `AttachmentContent`
conversion to leaf-internal outbound DTOs belongs to outbound-delivery; the
runtime invokes those leaf-owned conversions around the native call while
retaining only native send orchestration and the common transport factory. The
factory is explicit composition, not global or import-time registration. The
formal `imagent.channels` package remains a stable facade over those target-owned
objects; the historical `imagent.channels.runtime` implementation path is not a
second API or owner.
Optional provider dependencies remain extras; importing Interaction contracts
or the adapter facade does not import native SDKs. Every worker/queue/cache and
reconnect delay is finite, failures are explicit, and no consumer work runs on
a provider socket-read callback.

`diagnostics.py` owns the immutable native queue/connection/Channel facts, the
bounded process-local transport state, and adapter event/health debug emission.
It performs no I/O, callbacks, persistence, export, or operator policy. Media
staging keeps its own non-authoritative debug emission rather than importing a
concrete-adapter owner from the ingress leaf. The historical native diagnostics
module is removed without a compatibility path.

QQ quote normalization remains QQ-specific bounded untrusted content and does
not become a common message/resource contract. Weixin credential/cursor state
remains native Channel state, not Gateway persistence.

The target `src/imagent/interaction/channels/adapters/` package contains the
QQ, Telegram, Feishu/Lark, and Weixin provider modules plus the shared
QQ/Telegram HTTP endpoint validator and the shared native adapter base.
The validator remains adapter-internal: it validates provider configuration
without opening a transport and is not part of the Channel facade. All four
providers import `BaseChannelAdapter` and `ChannelRouteContext` from
`src/imagent/interaction/channels/adapters/base.py`. The historical
`imagent.channels.native.base` path is removed without a compatibility shim.
The base still coordinates adapter-owned diagnostics with shared ingress access
and outbound validation, so it remains an explicit split candidate until those
responsibilities move in later focused slices; this placement change does not
alter lifecycle, policy, or delivery behavior. Shared media staging and Windows
path security now live with Interaction ingress; the historical native helper
package is absent.

The adapters package exposes only the component-map-approved QQ adapter and
bounded quote constants through a lazy public facade. Importing the package or
another provider does not load QQ; resolving one of those exact names loads
the target QQ owner and preserves object identity. The three historical QQ
native module paths are deleted without compatibility shims. No other
provider-private symbol is promoted.
