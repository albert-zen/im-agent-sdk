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
instance. `start` installs the completed inbound-message callback and an
`InboundAdmissionHandler`. The admission parameter remains optional only so a
Channel may still be used directly outside Gateway with its message callback;
every `ImAgentGateway` composition supplies both arguments exactly once. A
one-argument implementation is not a Gateway-compatible Channel, and Gateway
does not inspect signatures, reinterpret `TypeError`, or retry a message-only
form. `stop` joins owned workers. `send` receives one logical or already
planned outbound unit and returns a typed `DeliveryReceipt`; it never claims
device display.

The Channel lifecycle carries no Gateway operation callback. A native button,
card, or other product action is normalized by the consumer's Controller and
invokes the same typed `ControllerActions`/Gateway operation surface as a text
command. Channel implementations therefore import neither `GatewayOperation`
nor Gateway orchestration.

`ChannelCapabilities` is the single capability authority. Channel delivery
support uses the Interaction-owned `DeliverySupportLevel` values `native`,
`fallback`, and `unsupported`; it does not reuse the similarly shaped
Application `SupportLevel`. `DeliveryProfile` is the derived planning view for
text units/limits, Markdown fallback, attachments/grouping, and the
Interaction-owned `ReplyReferenceScope`. Unsupported behavior fails explicitly.
Optional `ChannelStartupConfigurationValidator` is pure and repeatable; ADR
0014 diagnostics is a separate synchronous read-only structural capability
owned by [`interaction.channels.diagnostics`](../diagnostics/design.md).

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

## Formal facade and placement

`ChannelAdapter`, admission values, capability/profile values, delivery
support, receipt values, and their validation have one implementation owner in
`src/imagent/interaction/channels/contract.py`. The
`imagent.interaction.channels` package is the sole formal facade for those
objects. The stable v1 `ChannelCapabilities` field and
positional-constructor order and the wire string discriminants remain
unchanged. The schema exposes a distinct `DeliverySupportLevel` definition
for Channel/profile fields even though its three wire values match
`SupportLevel`; `SupportLevel` remains the Application capability type and is
not accepted as the typed Channel API.

The contract module is the canonical owner, including its finite `__all__` and
runtime-resolvable annotations. Adapter runtime composition imports these
objects but does not define, validate, or lazily recreate any Channel contract
value. The private route-context value used by native adapters is not a second
Channel contract or public facade.

The historical `imagent.adapters` facade no longer exports
`ChannelAdapter`, `ChannelStartupConfigurationValidator`, `MessageHandler`,
`InboundAdmission`, or `InboundAdmissionHandler`. The historical
`imagent.contracts` facade no longer exports `ChannelCapabilities`,
`DeliveryProfile`, `DeliverySupportLevel`, `ReplyReferenceScope`, any
`DeliveryReceipt`/`DeliveryItemReceipt`/`DeliverySegmentReceipt` value or
status, or either delivery-receipt validator. Those facades retain their
unrelated Application, Gateway, proactive-authorization, and passive-state
names; they do not use a compatibility alias or lazy attribute for the
retired Channel names.

Internal Channel, Gateway, testing, and release code imports the focused
Interaction owner directly. `imagent.channels` is a separate intended
adapter facade: its `NativeTransportChannelAdapter` and `channel_from_config`
exports remain exact re-exports of the Interaction adapters runtime owner and
do not become a second Channel contract facade.
