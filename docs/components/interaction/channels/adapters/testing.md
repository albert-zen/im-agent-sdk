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
install smoke. All four provider focused tests plus endpoint-validator tests
now mirror their source under `tests/interaction/channels/adapters/`.

The shared endpoint-validator test owns the exact accepted schemes and rejects
whitespace, missing hosts, invalid ports, userinfo, query strings, and
fragments. QQ and Telegram startup tests additionally prove that both adapters
consume the single Interaction-owned helper without allocating transport
resources.

Facade tests prove the approved QQ names are exact object-identity re-exports,
unknown names fail with `AttributeError`, and importing the adapters package or
another provider does not eagerly import the QQ module. QQ cluster tests prove
the target module owns those objects and the old module paths are absent.
Runtime facade tests prove `NativeTransportChannelAdapter` and
`channel_from_config` are target-owned, the formal `imagent.channels` facade
preserves exact object identity, importing either facade does not load a native
provider, and the historical `imagent.channels.runtime` path is absent.
