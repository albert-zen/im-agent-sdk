# Channel ingress design

## Purpose and ownership

Ingress turns one adapter-authenticated native event into one verified
`InboundMessage`. It owns the shared ordering and helpers for configured access
evaluation, stable account/Conversation/sender/message identity and public
envelope normalization, a bounded process-local duplicate fast path, pre-media
admission use, common media validation/staging, and explicit `AttachmentSource`
values.

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
5. retrieve, validate, and stage permitted media under explicit quotas/trust,
   then assemble the ordered content tuple;
6. normalize the final public inbound envelope and deliver exactly one complete
   matching message, or release only a confirmed pre-handoff preparation
   failure.

Access and durable duplicate rejection precede network download, filesystem
staging, parsing, and Agent mutation. A no-lease result removes the transient
fast-path key so a future provider redelivery can reclaim an abandoned lease.
Provider acknowledgements/cursors remain adapter state and never replace SDK
stable identity.

The private `_normalize_inbound_message` helper in `ingress.py` is the single
leaf-owned public-envelope boundary after runtime has assembled the ordered
content tuple. It preserves direct required native message, Conversation, and
sender identity coercion; the explicit reply override and native reply
fallback; the selected `channel_id`, `input_error`, and `trace_id` metadata;
and `_parse_datetime` ISO/`Z` parsing with its current fallback behavior. It
does not update route context, assemble or reorder text and attachments, run
admission, retrieve or decrypt provider media, or acknowledge a provider.
Those mechanics remain in their existing runtime and adapter owners for later
focused slices.

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
the Interaction path directly. Runtime still owns route-context updates and
content/attachment assembly, while it invokes the ingress-owned public
identity/time/metadata envelope helper. Admission handoff sequencing remains
in runtime for a later mechanical slice. Platform-specific authentication,
retrieval/decryption, and acknowledgement remain under adapters.

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
