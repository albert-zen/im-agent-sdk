# Native Channel adapters testing

Every built-in adapter must prove:

- stable configured identity, credentials/config validation, and honest
  capability/profile values;
- provider authentication/access and durable admission occur before media;
- the shared ingress admission-handoff transaction preserves duplicate/no-lease
  cleanup, synchronous/asynchronous preparation, transfer fencing, and
  pre-handoff-only release across all providers;
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
Diagnostic ownership tests prove the target module owns the native state
helpers while the canonical Interaction contract objects have one owner;
transition/epoch/reconnect behavior remains bounded and
side-effect-free, and the historical native diagnostics path is absent in a
clean process. Existing four-provider and Gateway diagnostics tests remain the
behavioral conformance suite.

Base ownership tests prove `BaseChannelAdapter` and `ChannelRouteContext` are
defined only by the Interaction adapters leaf, every provider and the runtime
wrapper use those exact owner objects, and the base delegates access-policy,
bounded denial-report, admitted inbound handoff, and outbound-enforcement
behavior to the focused Interaction leaves without a duplicate path. They also
prove inbound virtual overrides remain honored, denied input cannot reach
middleware, outbound route-user overrides remain lazy, and policy-raised
`PermissionError` values do not synthesize diagnostics. The historical
`imagent.channels.native.base` module is absent in a clean process. Existing
native Channel, access-policy, diagnostics, ingress, outbound, and Gateway
vertical tests remain the behavior-preservation suite for the mechanical move.
Runtime parity also proves that route-context updates remain adapter-runtime
state, the ingress middleware records them at the established point, and
complete inbound content and envelope normalization is delegated to the
Interaction ingress owner. Runtime contains no duplicate middleware,
normalizer, admission transaction, or outbound receipt helper.

Provider-focused suites additionally prove:

- QQ direct/group targeting, bounded quote parsing and anti-forgery,
  passive-to-proactive fallback, media staging, queue facts, and lazy facade;
- Telegram private/group/forum routing, mention/reply targeting, private token
  files, corrupt-offset failure, and credential-free diagnostics;
- Feishu/Lark named-domain restriction, topic identity, bounded resource
  staging, SDK queue overflow/reconnect, and redaction; and
- Weixin direct-user-only support, official-origin enforcement, protected
  credential state, context/cursor ownership, and explicit unsupported group
  and bot input.
