# Channel ingress design

## Purpose and ownership

Ingress turns one adapter-authenticated native event into one verified
`InboundMessage`. It owns the shared ordering and helpers for configured access
evaluation, stable account/Conversation/sender/message normalization, a
bounded process-local duplicate fast path, pre-media admission use, common
media validation/staging, and explicit `AttachmentSource` values.

It does not own provider signature/authentication algorithms, credential/API
clients, provider-specific download/decryption/acknowledgement rules, the
durable claim implementation after handoff, binding, Controller/Application
dispatch, transcript, unrestricted fetching, or Agent policy. Those native
rules stay in concrete adapters; they must call the shared ingress boundary in
the documented order.

## Ordered boundary

The required order is:

1. receive an event authenticated by the concrete adapter;
2. normalize bounded stable identities;
3. apply sender/Conversation access policy;
4. acquire the opaque durable admission lease;
5. retrieve, validate, and stage permitted media under explicit quotas/trust;
6. deliver exactly one complete matching message, or release only a confirmed
   pre-handoff preparation failure.

Access and durable duplicate rejection precede network download, filesystem
staging, parsing, and Agent mutation. A no-lease result removes the transient
fast-path key so a future provider redelivery can reclaim an abandoned lease.
Provider acknowledgements/cursors remain adapter state and never replace SDK
stable identity.

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
the Interaction path directly. Other shared ingress mechanics remain
interleaved in Channel runtime/native helpers for later mechanical slices.
Platform-specific authentication, retrieval/decryption, and acknowledgement
remain under adapters.

The mutable provider-normalization `InboundMessage` and immutable
`InboundAttachment` DTOs are also leaf-internal ingress values. They exist
before construction of the public typed Interaction message, are not exported
as a second common Message model, and carry no Gateway/Application authority.
