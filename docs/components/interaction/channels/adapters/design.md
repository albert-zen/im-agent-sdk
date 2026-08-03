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

Current native code remains under `src/imagent/channels/native` and runtime
during migration. Target placement is
`src/imagent/interaction/channels/adapters/` with platform modules and shared
helpers, after contract/ingress/outbound leaves move independently.
