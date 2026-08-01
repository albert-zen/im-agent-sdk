# Attachments and media component design

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

Media helpers depend on Contracts. Channel and Application integrations may
depend on Media. Media never imports either integration family or Gateway.

If future proactive Artifact delivery needs storage, planning, or retry, those
responsibilities belong to their owning component rather than expanding this
trust helper into a delivery subsystem.

## Change obligations

Changes require attachment trust tests, concrete adapter media tests, and a
review of [the common protocol](../contracts/protocol.md). Security-relevant
trust changes require an accepted ADR. Changes to native materialization order
also require restart-duplicate and pre-handoff failure coverage.
