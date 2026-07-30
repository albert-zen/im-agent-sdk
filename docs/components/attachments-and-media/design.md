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

It does not own:

- attachment bytes, a durable blob store, or universal media transformation;
- Channel download/upload APIs and credentials;
- Application-specific encoding or upload APIs;
- unrestricted remote URL fetching;
- a product decision about where staging directories live.

## Trust and flow

Channel integrations may stage native media and emit a typed source.
Application integrations accept only source kinds declared by capability.

`LocalPath` conveys a location, not authority. Deployment configuration must
establish a shared filesystem namespace and root. Resolution rejects missing
trust, relative paths, and paths outside that root.

`RemoteUrl` materialization belongs to an accepting integration with scheme,
address, redirect, credential, size, and media validation. `AttachmentHandle`
remains unsupported until a resolver contract is configured.

## Dependency direction

Media helpers depend on Contracts. Channel and Application integrations may
depend on Media. Media never imports either integration family or Gateway.

If future proactive Artifact delivery needs storage, planning, or retry, those
responsibilities belong to their owning component rather than expanding this
trust helper into a delivery subsystem.

## Change obligations

Changes require attachment trust tests, concrete adapter media tests, and a
review of [the common protocol](../contracts/protocol.md). Security-relevant
trust changes require an accepted ADR.
