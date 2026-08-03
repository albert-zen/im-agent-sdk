# Native Channel adapters testing

Every built-in adapter must prove:

- stable configured identity, credentials/config validation, and honest
  capability/profile values;
- provider authentication/access and durable admission occur before media;
- reconnect/cursor/local duplicate state is bounded and remains adapter-owned;
- queue overflow, cancellation, shutdown, rate limit, and native ambiguity are
  explicit;
- native message/media/reply/quote mappings respect every field/count/size/
  trust bound;
- planned text/artifacts map to truthful native calls and receipts;
- startup validation allocates no clients/workers or persistent state and
  matches start configuration;
- diagnostics are synchronous, local, immutable, redacted, bounded, and
  tolerate absent/raising/mismatched providers; and
- optional native dependencies are absent from base imports and clean-wheel
  smoke constructs each declared extra.

Run the reusable contract kit, `test_native_channels.py`, all four platform
suites, Gateway vertical tests, package-independence, wheel build, and clean
install smoke. Target tests mirror platform modules under
`tests/interaction/channels/adapters/` after the mechanical adapter move.

The shared endpoint-validator test owns the exact accepted schemes and rejects
whitespace, missing hosts, invalid ports, userinfo, query strings, and
fragments. QQ and Telegram startup tests additionally prove that both adapters
consume the single Interaction-owned helper without allocating transport
resources.
