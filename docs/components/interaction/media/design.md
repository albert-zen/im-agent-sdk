# Interaction media design

Component ID: `interaction.media`

Parent: `interaction`

## Purpose

This leaf defines typed attachment content and the source, media type, size,
and trust boundaries shared by Channel and Application integrations. It makes
media location explicit without making a path, URL, or message grant access.

## Ownership

This leaf owns:

- `AttachmentContent`, `AttachmentSource`, `AttachmentSourceKind`,
  `AttachmentGrouping`, `LocalPath`, `RemoteUrl`, and `AttachmentHandle`;
- validation of typed source shape, declared size, media type, and accepting
  capability limits;
- validation of the shared generic UTF-8 text and structurally checked PDF
  subset from filename plus actual transferred bytes;
- resolution of `LocalPath` only beneath an explicitly configured trusted
  shared root;
- bounded inline staging mechanics used by the optional proactive ingress.

It does not own:

- attachment bytes, a durable blob store, SDK spool/outbox, quota ledger, or
  crash-cleanup ledger;
- Channel download/upload APIs, credentials, native receipts, or retry
  policy;
- Application-specific materialization, encoding, or native upload behavior;
- unrestricted URL fetching or redirect/network policy;
- Gateway admission, delivery coordination, projection checkpoints, or
  idempotency;
- consumer staging-directory placement or artifact product UX.

## Source and trust contract

`AttachmentContent` carries a stable attachment identity, media type,
discriminated source, optional filename and declared byte size, and passive
Metadata. Attachment locations never travel through Metadata.

- `LocalPath` conveys a location, not authority. The accepting integration
  resolves an absolute path against deployment-configured shared-root trust
  and rejects missing trust, relative paths, and escape from that root.
- `RemoteUrl` requires the accepting integration to enforce scheme, address,
  redirect, credential, byte-size, and media policy. Core provides no
  unrestricted downloader.
- `AttachmentHandle` remains reserved until an explicit resolver is
  configured; unsupported consumers fail explicitly.

Channel capabilities declare accepted source kinds, media types, maximum
size/count, and grouping. Application capabilities currently declare accepted
source kinds; concrete accepting adapters enforce their evidenced native
media and size policy. Validation happens before native I/O where the boundary
has the required facts. Declared size is not proof of byte identity. Public
proactive `LocalPath` delivery additionally requires a lowercase SHA-256
content identity, and the accepting Channel verifies the actual bytes before
upload.

## Flow and dependency boundary

Native Channel authentication, stable identity, and access policy precede
media preparation. Media-capable Channel adapters acquire the opaque durable
admission lease from Gateway before download, staging, parsing, or quota work.
This leaf consumes the typed lease-independent media facts; it does not own or
persist admission.

Application adapters accept only declared source kinds. ADR 0015 A1 artifact
candidates are bounded untrusted facts, never `LocalPath` authority. The
consumer materializer owns validation, byte acquisition, quota, lifetime, and
crash-safe cleanup before returning bounded typed `AttachmentContent`. O2 may
notify clean-process release but is best-effort and cannot become a durable
cleanup mechanism.

`interaction.messages` depends on this leaf to include `AttachmentContent` in
its closed `Content` union. Media itself imports no Message, Gateway, Channel,
or Application implementation; Channel and Application leaves may consume
the typed media values.

## State and recovery

Typed media values and trust configuration are process-local. This leaf
persists no bytes or paths. A preparation failure before handoff releases only
the still-owned pre-handoff `in_flight` admission lease; after the handoff
callback starts, Gateway owns the terminal transition. Restart redelivery is
durably rejected before repeated media work when the prior identity completed
or remains in flight.

Authoritative Application history may invoke consumer materialization again,
so consumers use stable candidate/item identity and replay-safe acquisition.
Crash-safe byte cleanup remains a consumer ledger or startup sweep. Live-only
materialization never advances a recoverable completion checkpoint.

## Current and target structure

The extraction is intentionally split by ownership. The first mechanical
slice moves the source discriminants, attachment value, and local-filesystem
trust helpers from `src/imagent/contracts/model.py` and
`src/imagent/attachments.py` into the owning leaf. The mixed message Content
union, capability declarations, delivery records, and proactive ingress stay
in their current owners until their own focused slices; this move does not
change their behavior or schemas.

The mechanical target is:

```text
src/imagent/interaction/media.py
src/imagent/interaction/media_staging.py
tests/interaction/test_media.py
tests/interaction/test_media_staging.py
```

The deliberate `imagent.contracts` public facade re-exports the exact media
objects from this leaf. Repository runtime imports use the owning leaf, and
the obsolete `imagent.attachments` internal module is not retained. Inline
artifact staging now has its pure bounded filesystem mechanics in
`interaction.media`: a typed encoded-artifact value, decoded-byte validation,
private-directory creation, digest/path-safe writes, and staged
`AttachmentContent` construction. Gateway proactive ingress still owns JSON
parsing, authorization order, delivery, cancellation join, synchronous
cleanup lifetime, and result mapping. The Interaction helper cannot authorize,
submit, retain, or delete a staged attempt. No phase retains a second
implementation or introduces an SDK durable spool.

Shared generic-file detection also lives in this leaf. It accepts the same
explicit extension allowlist as before, rejects NUL/non-UTF-8 text, and checks
PDF header/object/xref/end markers from actual bytes rather than trusting the
filename. Channel staging maps the leaf's typed validation errors to its
existing provider-facing rejection codes; filename normalization, byte/count
limits, downloading, spool quota/lifetime, and process isolation remain in the
Channel ingress implementation.

On outbound public composition, the same finite source/media/count/size/group
facts are planned before Channel execution. A configured consumer root and
digest are then validated by the real Channel adapter before its native-send
boundary; a `Message` grants neither filesystem nor network authority.

## Authority

- [Vision](../../../VISION.md)
- [Architecture](../../../ARCHITECTURE.md)
- [Common protocol](../../contracts/protocol.md#message-envelopes)
- [ADR 0003](../../../decisions/0003-attachment-sources-and-trust.md)
- [ADR 0011](../../../decisions/0011-durable-inbound-admission-before-media.md)
- [ADR 0015](../../../decisions/0015-typed-extension-seams-and-composition.md)
