# Native Channel adapters design

## Purpose and ownership

This leaf contains concrete QQ, Telegram, Feishu/Lark, and Weixin adapters. An
adapter owns native credentials, authenticated connection/client lifecycle,
provider event/response mapping, platform policy/limits, reconnect cursor,
native acknowledgement, optional startup validation, and bounded ADR 0014
diagnostic facts.

It does not own Gateway binding/admission persistence/planning/retry,
Application execution, common/product commands, transcript, or a general
Channel plugin runtime.

## Shared shape and native differences

All built-ins implement the same Channel contract and call the shared
ingress/delivery ordering and validation helpers. Shared leaves cover access
evaluation, denial-report bounds, media/file validation, bounded
text/artifact mechanics, stable receipt correlation, and runtime lifecycle
where two or more real Channels prove the semantics. Concrete adapters
exclusively own provider
authentication/signatures, API encoding/escaping, credentials, native
upload/download/decryption/acknowledgement, URL rules, QR/token state, rate
limit/response mapping, and diagnostic worker facts.

`runtime.py` owns only the common native-transport wrapper and
`channel_from_config` factory inside this leaf. The provider-neutral
admission-handoff transaction, ingress middleware, and complete inbound
content/identity/time/selected-metadata normalization belong to Interaction
ingress; runtime constructs that leaf-owned middleware around the native
factory. The Base adapter retains bounded route-context state and records it
through the typed ingress boundary. Public
`OutboundMessage` and `AttachmentContent` conversion to
leaf-internal outbound DTOs belongs to outbound-delivery; the runtime invokes
those leaf-owned conversions around the native call while retaining only
native send orchestration and the common transport factory. The
factory is explicit composition, not global or import-time registration. The
formal `imagent.channels` package remains a stable facade over those target-owned
objects and preserves their exact object identity; the historical
`imagent.channels.runtime` implementation path is not a second API or owner.
The Channel contract/admission/receipt facade remains
`imagent.interaction.channels`; the historical `imagent.adapters` and
`imagent.contracts` facades do not re-export those Channel names.
Optional provider dependencies remain extras; importing Interaction contracts
or the adapter facade does not import native SDKs. Every worker/queue/cache and
reconnect delay is finite, failures are explicit, and no consumer work runs on
a provider socket-read callback.

`diagnostics.py` owns the native queue/connection snapshots, bounded
process-local transport state, and adapter event/health debug emission. The
canonical immutable `ConnectionDiagnosticFacts`, `QueueDiagnosticFacts`, and
`ChannelDiagnosticFacts` contracts belong to the Interaction diagnostics
leaves; this native helper imports them at the adapter boundary and does not
redefine them.
It performs no I/O, callbacks, persistence, export, or operator policy. Media
staging keeps its own non-authoritative debug emission rather than importing a
concrete-adapter owner from the ingress leaf. The historical native diagnostics
module is removed without a compatibility path.

QQ quote normalization remains QQ-specific bounded untrusted content and does
not become a common message/resource contract. Weixin credential/cursor state
remains native Channel state, not Gateway persistence.

The target `src/imagent/interaction/channels/adapters/` package contains the
QQ, Telegram, Feishu/Lark, and Weixin provider modules plus the shared
QQ/Telegram HTTP endpoint validator and the shared native adapter base.
The provider endpoint validator remains adapter-internal: it validates
provider configuration without opening a transport and is not part of the
Channel facade. All four providers import `BaseChannelAdapter` and
`ChannelRouteContext` from
`src/imagent/interaction/channels/adapters/base.py`. The historical
`imagent.channels.native.base` path is removed without a compatibility shim.
`BaseChannelAdapter` retains the exact native adapter method surface as a thin
delegator to ingress and outbound delivery. It owns only native lifecycle,
startup validation, bounded route-context state, and diagnostic state/facts. Its
`dispatch_inbound` method preserves virtual access/report/diagnostic calls and
their old order before delegating admitted handoff options to ingress. Its
outbound method preserves virtual route-user resolution and lazy conversation
fallback, then emits/raises only for the outbound leaf's explicit denial.
Access-denial diagnostic emission remains in the adapter boundary under ADR
0014, while the access decision, bounded report preparation, inbound handoff,
and outbound enforcement live in the focused Interaction leaves. Shared media
staging and Windows path security now live with Interaction ingress; the historical native helper
package is absent.

The adapters package exposes only the component-map-approved QQ adapter and
bounded quote constants through a lazy public facade. Importing the package or
another provider does not load QQ; resolving one of those exact names loads
the target QQ owner and preserves object identity. The three historical QQ
native module paths are deleted without compatibility shims. No other
provider-private symbol is promoted.

## Provider-specific boundaries

### QQ

QQ owns bot authentication, Gateway WebSocket reconnect state, stable C2C and
group identities, passive-reply context, media staging, and native
Markdown/file capabilities. C2C and group events retain native message and
sender IDs; group mentions are stripped only after native targeting succeeds.
Passive-reply context is bounded and degrades to proactive delivery when the
native reply window cannot be proven.

QQ quote snapshots are bounded adapter-specific untrusted input. Parsing
accepts only QQ evidence and bounds reference IDs, text, attachment counts,
filenames, voice transcripts, and rendered text. Raw envelopes, URLs, bytes,
and nested quote history are excluded. The adapter appends a labelled
untrusted block after the current text before the common native boundary emits
ordinary `TextFormat.PLAIN` content. The parsed shape never enters shared
native models or runtime code and does not create a common quote contract,
capability, or Metadata key.

The descriptive quote block grants no message, delivery, idempotency, binding,
reply-target, request, or approval authority. Only the authenticated native
inbound path supplies a provider snapshot. A caller may mimic the label only
as ordinary untrusted text; it cannot forge a trusted snapshot, and Metadata
is ignored for this feature.

Enabled instances require normalized `app_id` and `client_secret` values and
an HTTP(S) API endpoint. Authentication, reconnect, upload, reply-window, and
unsupported group-file failures remain explicit. Media is staged inside the
Channel-owned bounded spool before crossing the attachment source boundary.
Diagnostics expose only bounded lifecycle/worker facts and the fixed-capacity
inbound queue depth/overflow count, never credentials, endpoint, identities,
media paths, or exception text.

### Telegram

Telegram owns Bot API polling offsets, bot identity, private/group/forum
Conversation normalization, mention targeting, native reply IDs, and media
transfer. Private chats, groups, and forum topics remain distinct routes;
polling offsets are Channel reconnect state, not Agent cursors. Group input is
admitted only after native mention/reply targeting. Enabled instances require
a direct token or private token file and a credential-free HTTP(S) endpoint;
corrupt offsets fail closed. Bot API descriptions may surface only without
leaking tokens. Diagnostics expose bounded polling lifecycle facts without
tokens, offsets, endpoints, native identities, or exception text.

### Feishu/Lark

Feishu/Lark owns App credentials, the official SDK connection, named domain
selection, native chat/topic identity, mention targeting, resource transfer,
and reconnect health. Direct chats and topics remain distinct Conversations;
private resource references become content only through the bounded Channel
spool. The optional native SDK is constructed with strict transport security
and bounded inbound buffering. Only the named Feishu and Lark domains are
accepted, credentials are required, and subscription, reconnect, token,
resource, overflow, and delivery failures remain explicit. Diagnostics expose
bounded lifecycle/worker and inbound queue facts without credentials,
endpoints, identities, resource keys, paths, SDK snapshots, or exception text.

### Weixin iLink

Weixin owns consumer-enrolled iLink credentials, direct-message polling,
native reply context tokens, bounded reconnect state, media crypto/transport,
and credential-file protection. Only official direct-user identities are
supported; group and bot messages are explicitly unsupported. Context tokens
and update cursors are minimal Channel delivery/reconnect state. The transport
accepts only the official HTTPS origin, and corrupt, overly permissive,
wildcard-owner, or malformed credential state fails closed. Enrollment UX and
stale-credential recovery remain consumer policy. Diagnostics expose bounded
polling lifecycle facts without credentials, tokens, cursors, endpoints,
identities, paths, or exception text.
