# Channel ingress testing

Required scenarios:

- authentication and allow/deny policy precede media/network/filesystem work;
- all native identity fields are stable, bounded, and normalized without text
  or timestamp idempotency fallbacks;
- public inbound normalization preserves stable native message, Conversation,
  sender, and reply identity, created-at parsing/fallback, selected metadata,
  and text-before-attachments content ordering;
- the bounded transient `(channel_id, conversation_id, message_id)` fast path
  returns duplicates and removes its key after a no-lease result;
- admission is requested before synchronous or asynchronous preparation, and
  only confirmed pre-handoff failures release the opaque lease;
- the transfer fence precedes delivery, release failures become notes without
  masking the original error, and every failure discards the transient key;
- without an admission callback, the completed public message goes directly to
  the `MessageHandler`;
- durable completed/in-flight duplicates stop before preparation;
- preparation failure releases only its owned lease, while stale ownership
  cannot hand off or release a replacement claim;
- cancellation during preparation releases the untransferred lease, discards
  the transient key, and permits durable reclaim; cancellation after delivery
  callback entry never releases or reauthorizes input, even though transient
  cleanup lets the Gateway-owned durable claim answer a provider redelivery;
- media count/type/size/source/path/decoded-size and native metadata bounds are
  enforced before constructing the message;
- queue overflow and shutdown are explicit and joined; socket readers do not
  execute downstream consumer work;
- restart redelivery and native cursor/fast-path behavior preserve the durable
  admission authority; and
- QQ, Telegram, Feishu, and Weixin counterexamples all use the same ordering.

Focused policy evidence lives in
`tests/interaction/channels/test_ingress.py`: configuration parsing,
unrestricted and deny-all sentinels, `any`/`all` matching, and invalid mixed or
unknown modes. The four Channel suites prove provider configuration uses the
same owner and access still precedes admission/media work. Admission,
media/restart, queue, and shutdown evidence remains in the native and vertical
suites until those mechanics move in later focused slices.

The same focused module covers the private ingress content/envelope helper and
datetime parser: text-before-attachment ordering, source-message identity or
stable attachment fallback, media type, optional filename, size, explicit
untrusted `LocalPath`, fixed `kind` metadata, explicit reply overrides, native
reply fallback, selected metadata, stable identity coercion, valid ISO/`Z`
timestamps, and absent or malformed timestamp fallback. Runtime and provider
suites continue proving that the helper is invoked without changing bounded
route-context updates, admission handoff, native retrieval/decryption, or
acknowledgement behavior.

It also covers `_InboundAdmissionTransaction` with a bounded duplicate set,
no-lease retry, sync/async preparation, pre-handoff release, transfer fencing,
release-note/original-error precedence, failure cleanup, and direct delivery
when admission is absent. These tests exercise only the typed native/public
message and Channel callback contracts; they do not create a second admission
path or provider callback abstraction.

Focused tests also lock the leaf-internal inbound attachment tuple/defaults;
provider and vertical suites continue proving normalization into the public
Interaction message without treating the mutable native DTO as a contract.

Windows staging-security tests prove the helper is a no-op off Windows, is
owned only by Interaction ingress, and has no historical native import path.
Windows CI and the existing media materialization suite remain responsible for
the unchanged current-user SID, protected DACL, file/directory flag, and
failure behavior.

Media ownership tests prove the image/file materializers and result/error
values have one Interaction ingress owner and that the historical native media
module/package are absent. Existing four-provider, native Channel, admission,
restart, cancellation, quota, and vertical suites remain the behavioral proof
for the mechanically moved transaction boundary.
