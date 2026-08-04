# Channel outbound delivery design

## Purpose and ownership

Outbound delivery owns shared Channel-side execution after Gateway planning:
source/trust and defensive contract validation, stable source-item/native
attempt correlation, bounded text/artifact helpers proven across Channels,
normalization of adapter outcomes into truthful typed receipt evidence, and the
shared outbound access decision used before native encoding or submission.

The shared artifact batch helper executes one ordered native-message attempt.
It records per-artifact success or permanent failure, continues after a known
permanent failure, and retains the failed artifact plus the unattempted suffix
on cancellation or an unclassified exception so its caller can report the
truthful in-memory outcome. This mutation is not a durable retry checkpoint and
does not authorize replay; the owning Gateway/consumer decides what recovery is
safe from its authoritative idempotency and receipt evidence.

It does not own provider escaping/encoding, credentials, upload/API calls,
native idempotency, rate-limit/response interpretation, common
segmentation/grouping, global/per-destination admission, ordering, retry
scheduling, durable idempotency/checkpoints, a content outbox, or Application
presentation. Provider-specific work stays in adapters; the planning and
durable concerns remain Gateway-owned.

The read helper accepts only a caller-configured trusted local root, resolves
the candidate beneath that root, and verifies file kind, declared byte count,
and optional SHA-256 before upload. The caller owns the bytes and the root's
lifetime. This leaf does not create or retain a spool/outbox, enforce product
quota, maintain a ledger, run a startup sweep, or perform crash cleanup.

The private `_to_native_artifact` helper converts one public
`AttachmentContent` into the leaf-internal `OutboundArtifact` DTO used by a
native send. It accepts only `LocalPath` sources, requires a non-negative
declared size and a non-empty filename (falling back to the staged path's
basename), infers the bounded native kind from attachment metadata or media
type, and preserves the stable attachment ID plus optional SHA-256. It does
not read bytes or resolve a trusted root; `read_managed_artifact` remains the
only shared byte-read helper. The native runtime invokes this helper while it
continues to own final Channel receipt assembly.

The private `_to_native_outbound` helper converts one public `OutboundMessage`
into the leaf-internal native `OutboundMessage` DTO. It preserves the native
conversation identity, ordered text and attachment content, Markdown choice,
stable delivery ID, and reply correlation; it filters the reserved native
metadata keys before adding the authoritative delivery/reply values. It
delegates each attachment to `_to_native_artifact`, performs no provider API
call, and does not assemble public receipts. `NativeTransportChannelAdapter`
invokes this leaf-owned conversion before the native call; result normalization
is handled by `_native_delivery_receipt` after the call returns.

The private `_native_delivery_receipt` helper owns the post-attempt conversion
from one generic native result plus its mutable native metadata into the public
`DeliveryReceipt`. It preserves the existing `NativeDeliveryResult` type-check
fallback, zero/one/multiple native-message-ID selection and detail text, maps
recognized artifact metadata through the stable public content indexes, and
returns the same accepted/per-item evidence without inventing retryable or
unknown acceptance. It performs no native call or provider response mapping;
the adapter invokes it only after the native send returns.

The private `ensure_outbound_allowed` helper evaluates the configured
`ChannelAccessPolicy` using the most recent admitted route user when available,
then the adapter's conversation-user fallback, and returns a bounded typed
admitted/user-id decision. It performs no native call, does not mutate route
context, and lets policy exceptions propagate without translating them into a
synthetic denial. `BaseChannelAdapter.ensure_outbound_allowed` first resolves
the user through its virtual `_last_inbound_user_id(message)` method and only
then calls `_conversation_user_id` when that value is absent. It emits the
existing adapter diagnostic event and raises the exact existing
`PermissionError` only for the leaf's explicit not-admitted decision.

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

The private `_artifact_item_receipts` helper is the sole shared conversion from
one native attempt's artifact receipt metadata to typed `DeliveryItemReceipt`
values. It uses the runtime-provided attachment-ID/content-index mapping,
ignores malformed or unrecognized metadata entries, returns items in content
order, and preserves only the native attempt's accepted/rejected evidence. It
does not infer retry/unknown outcomes or durable acceptance. The helper belongs
to this leaf; `NativeTransportChannelAdapter` only invokes it while assembling
the public `DeliveryReceipt`.

Only explicitly trusted/materialized sources are submitted. The built-in
native adapters accept trusted `LocalPath`; unsupported `RemoteUrl` or
`AttachmentHandle` values fail rather than being fetched/dropped. Artifact
bytes, quotas, ledger, sweep, and crash cleanup remain consumer/materializer
owned.

Shared text, artifact batch, receipt, identity, and trusted-root read helpers
live in `src/imagent/interaction/channels/outbound_delivery.py` and are shared
by QQ, Telegram, Feishu, and Weixin. No compatibility implementation remains
under the historical native package. The defensive native `split_text`
fallback preserves Unicode code points, prefers bounded paragraph/newline/space
breaks, and falls back to the declared positive code-point limit. A soft break
before half of the current limit is deliberately ignored so defensive
splitting cannot emit a pathologically short prefix.

This helper does not replace Gateway delivery planning or become a second
segmentation policy. The Gateway planner remains authoritative for normal
capability-driven segmentation; Channel adapters use the helper only to
defend direct calls that exceed their native limit. Platform encoders and API
clients remain adapter-owned.

Leaf-internal `OutboundArtifact`, mutable native `OutboundMessage`, and
`NativeDeliveryResult` DTOs carry data between common outbound helpers and
provider adapters before normalization into public receipts. Both public
Message-to-native DTO conversions and final native-result receipt normalization
are owned here; the native runtime only invokes them around the native call.
These DTOs are not
top-level exports, do not duplicate the public Message contract, and hold no
retry, checkpoint, or persistence authority.
