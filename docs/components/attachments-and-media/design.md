# Attachments and media component design

> Migration note: the product-neutral Interaction source/trust authority now
> lives in [`interaction.media`](../interaction/media/design.md). This broad
> document remains migration evidence for media behavior still physically
> mixed with Gateway and Application components.

## Purpose

This component owns explicit media-source boundaries shared by Channel and
Application integrations. It prevents attachment location and filesystem trust
from becoming implicit Metadata or product-specific coupling.

## Ownership

It owns:

- validation and resolution of `LocalPath` inside an explicitly configured
  shared root;
- the cross-adapter trust rule for typed `AttachmentSource`;
- reusable boundaries for future media or Artifact materialization proven by
  #9/#11 consumers.
- bounded inline-artifact staging for the optional proactive delivery ingress.

It does not own:

- attachment bytes, a durable blob store, or universal media transformation;
- Channel download/upload APIs and credentials;
- Application-specific encoding or upload APIs;
- unrestricted remote URL fetching;
- a product decision about where staging directories live.

## Trust and flow

Channel integrations may stage native media and emit a typed source.
Application integrations accept only source kinds declared by capability.
Native Channel staging begins only after stable identity and access checks have
acquired the opaque durable admission lease defined by ADR 0011. A duplicate
that receives no lease performs no download, staging, parsing, or quota work;
the lease persists identity and ownership only, never media content or paths.

`LocalPath` conveys a location, not authority. Deployment configuration must
establish a shared filesystem namespace and root. Resolution rejects missing
trust, relative paths, and paths outside that root.

Public proactive `LocalPath` input must also carry a lowercase-hex SHA-256
digest in `AttachmentContent.metadata["sha256"]`. The canonical lowercase
representation gives idempotency a content identity independent from a
temporary path; the accepting Channel still verifies that the bytes at the
trusted path match that digest before upload.

`RemoteUrl` materialization belongs to an accepting integration with scheme,
address, redirect, credential, size, and media validation. `AttachmentHandle`
remains unsupported until a resolver contract is configured.

The proactive JSON ingress accepts inline bytes rather than an arbitrary
server-side path. It authenticates the target before decoding, bounds decoded
bytes, creates a random exclusive directory beneath a consumer-configured
private root, writes SDK-controlled names, includes a content digest, and removes the staging
directory after synchronous submission. The same root must be trusted by the
configured Channel adapter. The ingress is not a durable blob store and does
not fetch remote URLs.

## Dependency direction

The focused Interaction media leaf owns its values and filesystem trust
helpers directly. Mixed language-neutral schemas may describe those values,
but the Python leaf imports no broad Contracts facade. Channel and Application
integrations may depend on Media. Media never imports either integration
family or Gateway.

If future proactive Artifact delivery needs storage, planning, or retry, those
responsibilities belong to their owning component rather than expanding this
trust helper into a delivery subsystem.

An ADR 0015 A1 materializer may return typed `AttachmentContent`, but candidate
locators remain untrusted and this component does not acquire their bytes or
lifetime. The consumer validates/materializes into its configured namespace;
O2 notification may assist clean-process lease release, while crash-safe
cleanup remains a consumer ledger or startup sweep rather than SDK storage.

The App Server A1 seam exposes only frozen bounded candidates from completed
native items. Image-generation `savedPath` values are typed as untrusted local
path candidates; dynamic-tool input images may be typed as untrusted `file:`
or `data:image/` candidates. Candidate count and locator characters are
bounded before consumer invocation. Stable native item identity is preferred;
when absent or outside the fact bound, a deterministic digest of the native
kind and exact bounded locator supplies candidate identity. The adapter never
opens, resolves, decodes, copies, deletes, or declares trust in a candidate.

The consumer materializer owns validation and any byte acquisition, then may
return a finite `ApplicationArtifactMaterialization` containing typed
`AttachmentContent`. Output validation bounds attachment count, aggregate
string facts, metadata cardinality/scalars, and source shape without treating
a path or URL as trusted. Materialization invocation is async with finite
concurrency and lifetime in the adapter's existing ordered dispatch/history
flow. Its result may attach artifacts to the corresponding canonical Agent
message; one terminal invocation may produce an artifact-only fallback when a
completed, interrupted, or failed Turn has no associated text message.

Live duplicate suppression is process-local and bounded. Authoritative
history may invoke the materializer again, so consumers use stable candidate
identity for idempotent materialization and reproduce the same item/terminal
association. Neither facts nor output are persisted. Live-only candidates
remain non-replayable and cannot advance a completion checkpoint. A failure is
an explicit Application observation failure before that item/terminal result
is emitted; recovery may retry from authoritative history.

The #48 non-artifact A1 presenters return text only and confer no attachment,
path, URL, byte, spool, quota, or cleanup authority. Those concerns remain in
the separate #33 materialization protocol.

## Change obligations

Changes require attachment trust tests, concrete adapter media tests, and a
review of [the common protocol](../contracts/protocol.md). Security-relevant
trust changes require an accepted ADR. Changes to native materialization order
also require restart-duplicate and pre-handoff failure coverage.
