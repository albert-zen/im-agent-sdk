# Channel ingress design

## Purpose and ownership

Ingress turns one adapter-authenticated native event into one verified
`InboundMessage`. It owns the shared ordering and helpers for configured access
evaluation, access-denial report preparation and bounded reporting, stable
account/Conversation/sender/message identity and public envelope
normalization, a bounded process-local duplicate fast path, the
provider-neutral pre-media admission/handoff transaction, common media
validation/staging, and explicit `AttachmentSource` values.

It does not own provider signature/authentication algorithms, credential/API
clients, provider-specific download/decryption/acknowledgement rules, the
durable claim implementation after handoff, binding, Controller/Application
dispatch, transcript, unrestricted fetching, or Agent policy. Those native
rules stay in concrete adapters; they must call the shared ingress boundary in
the documented order.

## Ordered boundary

The required order is:

1. receive an event authenticated by the concrete adapter;
2. normalize the bounded stable identities needed for access and admission;
3. apply sender/Conversation access policy;
4. acquire the opaque durable admission lease;
5. retrieve, validate, and stage permitted media under explicit quotas/trust;
6. assemble the ordered content tuple, normalize the final public inbound
   envelope, and deliver exactly one complete
   matching message, or release only a confirmed pre-handoff preparation
   failure.

Access and durable duplicate rejection precede network download, filesystem
staging, parsing, and Agent mutation. A no-lease result removes the transient
fast-path key so a future provider redelivery can reclaim an abandoned lease.
Access-denial reporting is separately bounded to
`ACCESS_DENIAL_REPORT_LIMIT` reports in
`ACCESS_DENIAL_REPORT_WINDOW_S`; suppression never weakens the access gate.
Provider acknowledgements/cursors remain adapter state and never replace SDK
stable identity.

The private `_normalize_inbound_message` helper in `ingress.py` is the single
leaf-owned content and public-envelope boundary. It assembles text before
attachments in native order, uses a stable source-message identity or the
bounded per-message attachment fallback, preserves media type, optional
filename, size, explicit untrusted `LocalPath`, and fixed `kind` metadata, and
then preserves direct required native message, Conversation, and sender
identity coercion; the explicit reply override and native reply fallback; the
selected `channel_id`, `input_error`, and `trace_id` metadata; and
`_parse_datetime` ISO/`Z` parsing with its current fallback behavior. It does
not update route context, retrieve or decrypt provider media, or acknowledge
a provider. Runtime retains only route-context updates before invoking this
helper; provider-native work remains in the adapters. The ingress-owned
admission transaction is the separate boundary described below.

The private `_InboundAdmissionTransaction` owns the provider-neutral
transaction after access policy: it retains only the bounded transient
identity set and its lock, requests the opaque lease, invokes one finite
preparation/normalization stage, and either delivers exactly one complete
public message or releases only a confirmed pre-handoff failure. It preserves
the no-lease key discard, transfer fence before `InboundAdmission.deliver`,
release-failure note, original-error precedence, and failure key discard. The
stage callables are narrow ingress-specific Protocols; they are invoked for
one transaction and are not a generic middleware or hook chain. Runtime keeps
bounded route-context updates and supplies the normalizer; concrete provider
authentication, retrieval/decryption, and acknowledgement remain in adapters.

`BaseChannelAdapter` preserves its existing access and dispatch method surface
as a thin delegator to this leaf. Its `dispatch_inbound` method still invokes
the virtual `self.inbound_allowed(inbound)`,
`self.prepare_access_denial_report()`, and
`self.emit_access_denial(inbound, suppressed)` calls in their established
order; only the admitted options construction and middleware handoff are
delegated to the focused ingress helper. The inherited access-policy and
bounded-report methods delegate their leaf-owned mechanics without creating a
second policy or limiter path. Native diagnostic state and the
`emit_access_denial` call remain adapter-owned because diagnostics are governed
separately by ADR 0014; ingress supplies the access decision and bounded report
value without moving diagnostic ownership.

## Capacity, trust, and recovery

Inbound queues, event/field counts, text/media sizes, downloads, staging
directories, worker concurrency, and shutdown joins are finite. A message
cannot grant filesystem or URL trust: `LocalPath` requires deployment trust;
remote retrieval remains native adapter policy with explicit scheme/address/
redirect/credential/size/media checks. Preparation crash leaves reclaimable
claim state, not an SDK content job.

`ChannelAccessPolicy`, its `any`/`all` match mode, and configuration ID
parsing live in `src/imagent/interaction/channels/ingress.py`. The policy takes
only stable native user and Conversation IDs; it does not authenticate a
provider, infer identity from text or time, or become a product permission
service. QQ, Telegram, Feishu, and Weixin construct and evaluate that one
shared value before admission and media work.

The obsolete provider-native access module is not a compatibility facade:
access policy is an internal owning-leaf contract, so repository callers use
the Interaction path directly. Runtime still owns bounded route-context
updates, while it invokes the ingress-owned admission transaction and complete
content/identity/time/metadata envelope helper. Platform-specific
authentication, retrieval/decryption, and acknowledgement remain under
adapters.

`ingress_security.py` owns the private Windows staging-path DACL helper. On
Windows it resolves the current process user SID and replaces each staged file
or directory DACL with current-user-only full control; on other platforms it
is a no-op. It is not a public generic filesystem API and does not choose the
staging root, quota, retention, ledger, or cleanup policy. The historical
`imagent.channels.native.windows_security` module is removed without a
compatibility shim.

`ingress_media.py` owns the shared inbound image/file download-validation and
private staging transaction used by all four built-in Channels. Image and file
staging intentionally remain together because they share one cross-process
lock, quota, secure-create, retention cleanup, killable-worker, deadline, and
cancellation boundary. The module maps Interaction media validation into
bounded provider-facing outcomes but is not the public `interaction.media`
value/trust contract. Its spool is process-local inbound preparation, never a
durable SDK content outbox or consumer artifact ledger. The historical
`imagent.channels.native.media` module and now-empty native package are removed
without compatibility shims.

The mutable provider-normalization `InboundMessage` and immutable
`InboundAttachment` DTOs are also leaf-internal ingress values. They exist
before construction of the public typed Interaction message, are not exported
as a second common Message model, and carry no Gateway/Application authority.
