# Channel outbound delivery testing

Required scenarios:

- every advertised profile produces natively acceptable planned units;
- plain/Markdown fallback and code-point/UTF-16/UTF-8 limits preserve order;
- attachment grouping, type/size/source, trusted path, and reply-scope limits
  fail before unsupported native effects;
- stable delivery/segment/attachment identity does not depend on staging path;
- accepted/rejected/retryable/partial/unknown and per-item evidence is truthful
  and validates against source content;
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
behavior, and mapping-to-`OutboundArtifact` coercion without promoting these
leaf-internal DTOs to public contracts.
