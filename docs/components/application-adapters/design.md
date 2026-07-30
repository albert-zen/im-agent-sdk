# Agent Application adapters component design

## Purpose

An Agent Application adapter translates one configured native control endpoint
into the common Application Port while leaving resource and execution truth in
the native Application.

## Ownership

Application adapters own:

- native authentication/connection/client lifecycle;
- native project mode and resource mapping;
- typed Application operation translation;
- input encoding and stable client-message ID round-trip;
- native event-to-`AgentEvent` mapping and fan-out publication;
- authoritative history/catch-up reads;
- explicit request, interruption, activation, attachment, replay, and ordering
  capabilities.

They do not own:

- native Projects, Threads, transcript, Turns, requests, execution, or
  retention;
- Conversation binding or Thread projection route;
- IM delivery, Markdown, bot credentials, or native Conversation IDs;
- common permission/product policy;
- a synthetic event journal or replay sequence.

## Resource and operation surface

Each adapter exposes a summary, `start`, `stop`, typed `execute`, `send_input`,
and fan-out-safe `subscribe_thread`.

Managed Applications expose real Projects. Flat/fixed Applications omit them.
Thread lookup is independent of Conversation selection. Native activation is
an explicit optional operation.

History contains all completed Agent messages in a Turn. Application-native
message phases remain namespaced Metadata until common reuse is proven.

## Events and recovery

Native notification producers publish into independent subscriber queues
without awaiting Channel delivery. The adapter emits a separate explicit
terminal Turn event after any number of completed messages.

Replay, gap detection, and sequence scope are separate capabilities. When the
native endpoint lacks replay or restart-safe sequence, fields are omitted and
Gateway recovers from authoritative history/catch-up.

## Attachments and requests

Adapters accept only declared typed sources and apply the
[attachments/media trust boundary](../attachments-and-media/design.md).
Workspace roots, URL policy, native upload, and media encoding remain
adapter/deployment behavior.

Approval and user-input request truth remains native. The common adapter may
translate request/response semantics; it never decides Full Access, sandbox,
or consumer permission policy.

An adapter advertises interactive requests only when it can translate both an
open request and its response without guessing a native wire shape. It maps a
native terminal or an invalidated response handle to typed resolution, while
the native Application remains first-writer authority. Pending-request
recovery is declared separately in adapter documentation and is performed only
from an authoritative native pending set.

## Native pages

- [Codex](adapters/codex.md)
- [Zen](adapters/zen.md)
- [T3](adapters/t3.md)

## Change obligations

Every change requires focused native tests, the reusable adapter contract
suite, event/recovery tests, and a capability honesty review.

The Codex App Server client ownership transfer is scoped by the
[Issue #9 SDK-side map](../../migrations/issue-9-imcodex-owner-transfer.md);
product supervision/configuration outside that map remains downstream.
