# Channel outbound delivery design

## Purpose and ownership

Outbound delivery owns shared Channel-side execution after Gateway planning:
source/trust and defensive contract validation, stable source-item/native
attempt correlation, bounded text/artifact helpers proven across Channels, and
normalization of adapter outcomes into truthful typed receipt evidence.

It does not own provider escaping/encoding, credentials, upload/API calls,
native idempotency, rate-limit/response interpretation, common
segmentation/grouping, global/per-destination admission, ordering, retry
scheduling, durable idempotency/checkpoints, a content outbox, or Application
presentation. Provider-specific work stays in adapters; the planning and
durable concerns remain Gateway-owned.

## Submission and receipts

The adapter advertises a profile that makes normal planner output valid. It may
defensively split or reject a direct invalid call, but cannot become a second
planning authority. Stable native idempotency uses the SDK delivery/segment/
attachment identities, never a temporary path or presentation text.

Receipts distinguish accepted, rejected, explicitly retryable, partial, and
unknown outcomes. Retryable/unknown evidence never invents native acceptance
identity. Per-item receipts map every attempted attachment to its stable
`attachment_id`; an accepted prefix is never overwritten by a later failure.
Ambiguous native outcomes do not authorize hidden resend.

Only explicitly trusted/materialized sources are submitted. The built-in
native adapters accept trusted `LocalPath`; unsupported `RemoteUrl` or
`AttachmentHandle` values fail rather than being fetched/dropped. Artifact
bytes, quotas, ledger, sweep, and crash cleanup remain consumer/materializer
owned.

Current shared delivery code is interleaved with Channel runtime/native base,
artifact/model helpers. The defensive native `split_text` fallback lives in
`src/imagent/interaction/channels/outbound_delivery.py` and is shared by QQ,
Telegram, Feishu, and Weixin. It preserves Unicode code points, prefers bounded
paragraph/newline/space breaks, and falls back to the declared positive
code-point limit. A soft break before half of the current limit is deliberately
ignored so defensive splitting cannot emit a pathologically short prefix.

This helper does not replace Gateway delivery planning or become a second
segmentation policy. The Gateway planner remains authoritative for normal
capability-driven segmentation; Channel adapters use the helper only to
defend direct calls that exceed their native limit. Platform encoders and API
clients remain adapter-owned.
