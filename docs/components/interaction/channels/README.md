# Interaction Channel components

Channels are the product-neutral IM edge. They authenticate and normalize
native input, use the opaque Gateway admission lease before expensive media
work, encode planned output, and return truthful native delivery evidence.
They do not own Agent resources, Conversation/Thread bindings, delivery retry
policy, or a durable content queue.

## Leaves

| Leaf | Purpose | Documents |
|---|---|---|
| channel-contract | Typed lifecycle, capability, admission, operation, send, and receipt boundary | [design](channel-contract/design.md), [testing](channel-contract/testing.md) |
| ingress | Authenticated access, stable identity normalization, pre-media admission, and safe staging | [design](ingress/design.md), [testing](ingress/testing.md) |
| outbound-delivery | Native encoding/submission and truthful receipt/item evidence | [design](outbound-delivery/design.md), [testing](outbound-delivery/testing.md) |
| diagnostics | Channel-scoped immutable facts and optional provider contract | [design](diagnostics/design.md), [testing](diagnostics/testing.md) |
| adapters | QQ, Telegram, Feishu/Lark, and Weixin native transports and policy | [design](adapters/design.md), [testing](adapters/testing.md) |

## Dependency boundary

The Channel contract depends only on Interaction values. Ingress and outbound
delivery depend on that contract; concrete adapters depend on both. No Channel
leaf imports Gateway or an Application implementation. Gateway supplies an
opaque admission handler and planned outbound work through the contract, not
repositories or orchestration context.

The machine-readable [component map](../../component-map.yml) records current
mixed files as split candidates until focused mechanical moves establish the
target source and test tree. Existing broad Channel/platform documents remain
evidence during that migration but do not replace these leaf contracts.

Channel diagnostics are a separate structural capability governed by ADR 0014.
The contract lives in `imagent.interaction.channels.diagnostics` and depends
only on Interaction diagnostics values. Native collection/state helpers remain
in the [adapter diagnostics leaf](adapters/design.md); no Channel diagnostics
path imports Gateway or an Application implementation.
