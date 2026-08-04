# Channel outbound delivery testing

Required scenarios:

- every advertised profile produces natively acceptable planned units;
- plain/Markdown fallback and code-point/UTF-16/UTF-8 limits preserve order;
- attachment grouping, type/size/source, trusted path, and reply-scope limits
  fail before unsupported native effects;
- outbound access enforcement uses the latest admitted route user, preserves
  the conversation fallback and exact rejection error, and runs before any
  native send;
- stable delivery/segment/attachment identity does not depend on staging path;
- ordered artifact execution records each accepted or permanently failed item,
  continues after permanent failure, and retains the failed plus unattempted
  suffix on cancellation or unclassified exception without claiming durable
  retry authority;
- caller-managed trusted-root reads reject escape/missing/non-file sources and
  verify declared byte count and optional SHA-256;
- public `AttachmentContent` conversion accepts only explicit `LocalPath`
  sources, preserves stable attachment metadata, derives a filename and
  bounded native kind deterministically, and rejects invalid size or filename
  inputs without reading bytes;
- public `OutboundMessage` conversion preserves native conversation/content
  order, Markdown selection, stable delivery/reply correlation, and only
  caller metadata outside the reserved native-owned keys;
- generic native-result normalization preserves the non-result fallback,
  zero/one/multiple native-message-ID selection and detail text, stable
  content-index ordering, and public accepted/per-item evidence without
  changing retryable or unknown semantics;
- accepted/rejected/retryable/partial/unknown and per-item evidence is truthful
  and validates against source content;
- native artifact receipt metadata maps recognized stable attachment IDs to
  sorted typed per-content item receipts, while malformed or unknown entries
  are ignored without inventing retry/unknown evidence;
- retryable/unknown receipts contain no invented acceptance identity;
- a later segment/item failure preserves an already accepted prefix;
- cancellation/shutdown join native work before temporary artifacts may be
  released; and
- QQ, Telegram, Feishu, and Weixin native response mappings pass the same
  contract while retaining platform-specific encoding.

Focused defensive text evidence lives in
`tests/interaction/channels/test_outbound_delivery.py`: invalid limits,
empty/trimmed input, paragraph/newline/space soft breaks, hard breaks, and
Unicode code-point preservation. The four Channel suites prove the same owner
is used without changing platform limits or encoding. Receipt, artifact,
delivery planning/coordination/outcome, and vertical evidence remains with its
current owner until later focused slices.

Focused tests additionally lock native result defaults, mutable artifact-list
behavior, mapping-to-`OutboundArtifact` coercion, and
`AttachmentContent`-to-`OutboundArtifact` conversion without promoting these
leaf-internal DTOs to public contracts. They also prove the historical
`imagent.channels.native.artifacts` module is absent, stable attachment
identity ignores temporary path changes, and artifact recovery state remains
bounded to the current native-message attempt. The same focused suite covers
`_artifact_item_receipts` ownership and its stable-ID/content-index ordering
plus `_to_native_artifact`, `_to_native_outbound`, and
`_native_delivery_receipt` ownership. Native adapter tests retain the same
provider response mappings while proving the runtime only invokes the
outbound-delivery receipt normalizer after the native call.

Adapter-base parity tests prove the inherited outbound check preserves
`_last_inbound_user_id` overrides and lazy conversation fallback, delegates the
policy decision to this leaf, and emits/raises only for its explicit
not-admitted result. A policy-raised `PermissionError` produces no synthetic
denial event or health side effect. The diagnostic event for a rejected native
send remains covered by adapter diagnostics and is not re-owned by this leaf.
