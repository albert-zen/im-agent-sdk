# ADR 0003: Explicit attachment sources and trust

Status: Accepted

## Context

Passing `local_path` through Metadata made Channel/Application behavior depend
on an undocumented shared-filesystem assumption and let message content appear
to grant trust.

## Decision

`AttachmentContent` contains a discriminated `AttachmentSource`:

- `LocalPath`;
- `RemoteUrl`;
- reserved `AttachmentHandle`.

Application capabilities declare accepted kinds.

`LocalPath` is accepted only when deployment configuration establishes a
trusted shared root. The message cannot grant trust. `RemoteUrl` requires the
accepting integration to enforce scheme/address/redirect/credential/size/media
policy. Core provides no unrestricted downloader.

## Consequences

Media location is typed and capability-gated. Channel staging and Application
materialization remain native behavior. Future Artifact/media workflows extend
the neutral boundary only after cross-product evidence.
