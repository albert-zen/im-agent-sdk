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

Current evidence is spread across `tests/test_native_channels.py`, the four
Channel suites, delivery planning/coordination/outcome tests, and vertical
slices. Target placement is
`tests/interaction/channels/test_outbound_delivery.py`.
