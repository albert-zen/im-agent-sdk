# Channel contract design

## Purpose and ownership

This leaf defines the typed Python boundary between one configured native IM
Channel instance and Gateway composition: `ChannelAdapter`, immutable
capabilities/profile values, optional side-effect-free startup validation, the
opaque `InboundAdmission` lease, lifecycle callbacks, `send`, and truthful
delivery receipts.

It owns no native credentials/transport, access policy, Gateway claim
repository, binding, delivery planner/retry loop, transcript, or Agent state.
Channel absence or absence of an optional validator/diagnostics provider is an
honest capability difference, not implicit success.

## Contract

One stable `channel_instance_id` identifies one configured account/bot
instance. `start` installs bounded inbound/operation callbacks and, for modern
media-capable adapters, an `InboundAdmissionHandler`. `stop` joins owned
workers. `send` receives one logical or already planned outbound unit and
returns a typed `DeliveryReceipt`; it never claims device display.

`ChannelCapabilities` is the single capability authority. Channel delivery
support uses the Interaction-owned `DeliverySupportLevel` values `native`,
`fallback`, and `unsupported`; it does not reuse the similarly shaped
Application `SupportLevel`. `DeliveryProfile` is the derived planning view for
text units/limits, Markdown fallback, attachments/grouping, and the
Interaction-owned `ReplyReferenceScope`. Unsupported behavior fails explicitly.
Optional `ChannelStartupConfigurationValidator` is pure and repeatable; ADR
0014 diagnostics is a separate synchronous read-only structural capability.

## Admission and failure boundary

The admission callback accepts only stable Conversation/message identity and
returns a one-shot opaque lease or no lease. The Channel may release a lease
only for confirmed preparation failure before handoff, or deliver exactly one
matching complete `InboundMessage`. After delivery begins, Gateway owns claim
completion/release/protection. The contract exposes no repository or claim
mutation API.

All identities and queues are finite. Native ambiguity maps to unknown rather
than hidden replay. Lifecycle and callback work never runs consumer delivery
or Agent execution on a native socket-read task.

## Current and target placement

Receipt values, capability/profile values, delivery support, reply scope, and
their validation have one implementation owner in
`src/imagent/interaction/channels/contract.py`. `imagent.contracts` remains an
exact formal re-export facade; it does not retain parallel implementations.
The stable v1 `ChannelCapabilities` field and positional-constructor order and
the wire string discriminants remain unchanged. The schema exposes a distinct
`DeliverySupportLevel` definition for Channel/profile fields even though its
three wire values match `SupportLevel`. `SupportLevel` remains the Application
capability type and is not accepted as the typed Channel API.

Lifecycle/admission protocol movement remains a separate mechanical slice.
Top-level compatibility exports exist only where the component map declares a
formal facade.
