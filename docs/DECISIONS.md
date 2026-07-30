# Accepted decisions

This document records decisions that have completed design review. Reopening a
decision requires updating the relevant authoritative documents in the same
change.

## D-001: Language-neutral contracts with a Python reference

The semantic contract is published as JSON Schema. The first reference
implementation and test kit use Python because the existing IMCodex-family
channel edge is Python.

The Python package is not the wire protocol. TypeScript and other
implementations may consume the same schemas.

## D-002: Project is first-class but not required at runtime

Project remains part of the common resource hierarchy:

```text
AgentApplication
  └── Project
        └── Thread
```

An application instance declares one project mode:

```text
managed — projects are discoverable/selectable resources
flat    — threads live directly under the application instance
fixed   — the application instance has one externally configured cwd/workspace
```

`flat` and `fixed` are supported operating modes, not missing implementations.
They expose threads without `projectRef`. An adapter must not synthesize a
misleading Project merely to satisfy the hierarchy.

## D-003: Conversation-level selection

The initial binding key is:

```text
(channelInstanceId, conversationId)
```

Per-user selection inside a group conversation is a future product extension,
not part of the first common binding contract.

## D-004: Archive and permanent deletion are distinct

Thread deletion capability is:

```text
unsupported | archive | permanent
```

An adapter must report the actual supported behavior. A permanent-delete
request cannot be silently implemented as archive.

## D-005: Design authority before implementation

The Vision, Architecture, Domain Model, Protocol, Adapter, and Decision
documents define the shared boundary. Runtime code cannot introduce new common
semantics without first documenting them.

## D-006: Application actions and Gateway bindings are separate typed unions

Common application operations have discriminated, behavior-specific fields and
matching typed results. They never mutate a Gateway Conversation binding.

Gateway operations own application/project/thread selection for one
Conversation. Binding a Thread validates the application-owned resource but
does not implicitly activate, open, or resume that Thread in the native
application. Native activation is a separate optional application operation.

Free-form Metadata remains an extension surface, not a place for common
operation arguments or success values.

## D-007: Default Slash UX is an optional Controller

The Gateway may compose an inbound Controller but does not parse Slash syntax
or render command panels. The SDK ships a default `SlashController` and
Markdown presenter over typed operations. Products may replace or extend that
Controller, and non-text interactions invoke the same typed actions directly.

Inbound channel observations use `InboundMessage`; outbound delivery requests
use `OutboundMessage` with a stable `deliveryId`.

## D-008: Attachment location is a typed, capability-gated source

`AttachmentContent` contains a discriminated `AttachmentSource`: `LocalPath`,
`RemoteUrl`, or reserved `AttachmentHandle`. Behavior-critical locations are
not carried in Metadata.

Application capabilities declare accepted source kinds. `LocalPath` is usable
only when deployment configuration explicitly trusts a shared filesystem;
messages cannot grant that trust. `RemoteUrl` requires adapter-owned fetch and
SSRF/size/type policy rather than an unrestricted common downloader.

## D-009: Thread event subscriptions are live fan-out streams

Each active Thread subscriber owns an independent delivery queue. Native event
producers publish without awaiting consumers; a slow or cancelled observer
cannot steal from or block another observer. The queues are live projections,
not an SDK transcript or execution authority.

`message.completed` never terminates a Turn subscription. Only explicit
`turn.completed`, `turn.failed`, or `turn.interrupted` events are terminal.

## D-010: Ordering fields state only native recoverable guarantees

`eventId` is required. `sequence`, its epoch, and `cursor` are optional.
Adapters omit sequence/cursor when the native application cannot preserve
their meaning across the declared scope and recovery window.

Capabilities separately declare replay, gap detection, and sequence scope.
Cursor expiration is explicit. Unsupported replay falls back to a fresh live
subscription plus authoritative history/catch-up reconciliation, not a
synthetic SDK event log.
