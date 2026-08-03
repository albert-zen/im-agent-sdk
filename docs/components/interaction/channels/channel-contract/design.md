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

`ChannelCapabilities` is the single capability authority. `DeliveryProfile`
is its derived planning view for text units/limits, Markdown fallback,
attachments/grouping, and reply scope. Unsupported behavior fails explicitly.
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

The component is converging through behavior-preserving slices because its
current `ChannelCapabilities` public surface reuses the Application capability
enum `SupportLevel`. Receipt values and their validation have no such layer
dependency: their implementation owner is
`src/imagent/interaction/channels/contract.py`, while `imagent.contracts`
remains an exact formal re-export facade. Capability decoupling and the
lifecycle/admission protocol move are separate API and mechanical slices; they
must not be hidden inside receipt extraction.

The completed target has one implementation owner in
`src/imagent/interaction/channels/contract.py`. Top-level compatibility exports
exist only where the component map declares a formal facade.
