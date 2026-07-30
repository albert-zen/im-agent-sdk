# IMCodex Channel seam

## Current native source

The current adapter imports the pinned owner-controlled IMCodex package at:

```text
858398226e8f76e49f8259ae686939f209e1bb36
```

It exposes QQ, Telegram, Feishu, and Weixin through one
`ImcodexChannelAdapter` seam while retaining native lifecycle, admission,
media, Markdown, reply, retry, and receipt behavior.

Authoritative provenance and candidate transfer paths live in
[REUSE.md](../../../REUSE.md).

## Mapping

- native inbound messages become `InboundMessage` with stable configured
  Channel/Conversation/sender/message identities;
- staged media becomes typed `LocalPath`;
- common `OutboundMessage` is converted into the compatible native outbound
  shape;
- native delivery IDs become `DeliveryReceipt`;
- advertised capabilities come from real native support.

## Ownership boundary

This is a temporary package boundary, not desired long-term ownership. Issue
#9 must transfer the smallest coherent Channel modules, tests, fixtures,
license notices, and source commit records into this SDK. After transfer:

- `im-agent-sdk` owns reusable adapters/clients/tests;
- IMCodex consumes and configures the SDK;
- no dynamic import or runtime package dependency points from SDK to IMCodex;
- any IMCodex-only allowlist, bot policy, command, or deployment choice remains
  a consumer decision.

Do not maintain two production implementations or an exitless compatibility
shim.
