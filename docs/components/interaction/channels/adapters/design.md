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

`channel_from_config` is explicit composition, not global/import-time
registration. Optional provider dependencies remain extras; importing
Interaction contracts does not import native SDKs. Every worker/queue/cache and
reconnect delay is finite, failures are explicit, and no consumer work runs on
a provider socket-read callback.

QQ quote normalization remains QQ-specific bounded untrusted content and does
not become a common message/resource contract. Weixin credential/cursor state
remains native Channel state, not Gateway persistence.

The target `src/imagent/interaction/channels/adapters/` package contains the
QQ, Telegram, Feishu/Lark, and Weixin provider modules plus the shared
QQ/Telegram HTTP endpoint validator.
The validator remains adapter-internal: it validates provider configuration
without opening a transport and is not part of the Channel facade. All four
providers temporarily import the same shared base, media, artifact, and
diagnostic helpers from `src/imagent/channels/native` while those independently
reviewed boundaries remain in place. Runtime composition and the remaining
shared helpers move only in later focused mechanical slices.

The adapters package exposes only the component-map-approved QQ adapter and
bounded quote constants through a lazy public facade. Importing the package or
another provider does not load QQ; resolving one of those exact names loads
the target QQ owner and preserves object identity. The three historical QQ
native module paths are deleted without compatibility shims. No other
provider-private symbol is promoted.
