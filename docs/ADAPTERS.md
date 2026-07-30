# Adapter contracts

## Why two adapter families

IM Agent SDK has symmetric integration boundaries:

```text
ChannelAdapter
      ↓
Gateway
      ↓
AgentApplicationAdapter
```

A Channel adapter knows the IM platform but does not know Agent application
semantics. An Agent application adapter knows projects, threads, and events but
does not know QQ cards or Telegram Markdown.

## Channel adapter

### Required responsibilities

```text
identity()
capabilities()
start(onMessage, onAction)
stop()
send(message)
```

Channel adapters emit `InboundMessage` and accept `OutboundMessage`. The two
shapes intentionally differ: inbound native identity and sender are
observations, while outbound `deliveryId` is a delivery request identity.
Adapters populate a native message ID only in `DeliveryReceipt`.

Optional behavior is advertised by capabilities:

```text
updateMessage
deleteMessage
setTyping
sendInteractiveAction
downloadAttachment
uploadAttachment
```

### Channel capabilities

The first capability set covers:

```text
plainText
markdown
messageEdits
messageDeletion
typingIndicators
interactiveActions
attachments
replyReferences
nativeThreadsOrTopics
maxTextLength
maxAttachmentSize
```

Capabilities describe native behavior. A Gateway may provide a fallback, but
fallback must not be advertised as native support.

### Inbound processing order

1. Verify platform signature or authenticated connection.
2. Normalize account, conversation, sender, and message IDs.
3. Apply sender/conversation access policy.
4. Perform idempotency lookup.
5. Download or stage permitted attachments.
6. Emit normalized Message or Operation.

Deduplication and access checks happen before expensive attachment work and
before Agent mutation.

### Attachment sources and trust

Channel adapters put the staged location in the typed `AttachmentSource`, not
Metadata. Application capabilities list the source kinds actually accepted.
An adapter must reject every undeclared source kind.

`LocalPath` is valid only when deployment configuration explicitly establishes
that the Channel adapter's staging directory and the Application adapter share
a trusted filesystem namespace. The attachment cannot opt itself into that
trust. Adapters must treat paths as untrusted input, enforce their configured
root/policy where applicable, and validate file type and size before use.

`RemoteUrl` is not an instruction to perform an unrestricted server-side
request. An accepting adapter owns allowlists, scheme and address checks,
redirect policy, download limits, media validation, and credential handling.
Remote App Server configurations that cannot fetch/materialize or upload a
source reject it with an explicit unsupported-source error.

### Outbound behavior

The adapter owns:

- Markdown conversion and escaping;
- length-aware segmentation;
- optional edit-in-place delivery when a product explicitly enables it;
- throttling and platform rate limits;
- native idempotency keys where available;
- conservative retry when native idempotency is unavailable.

Streaming tokens are not an IM UX requirement. The default behavior sends
meaningful completed messages; QQ and Telegram do not receive one native
message per token.

## Agent application adapter

### Required resource surface

```text
summary
start()
stop()
execute(ApplicationOperation) -> ApplicationOperationResult
```

The deliberately small `execute` seam owns project/thread control operations;
the adapter maps each discriminated operation to its native application API
and returns the matching discriminated result. Behavior-critical arguments
and success values are named fields, not free-form Metadata or an untyped
`value`. This is a deep module boundary rather than a method-per-resource
mirror.

History-capable adapters implement both `turn.catchup` and `thread.history`.
Codex/Zen map these to native App Server Turn items; T3 groups its native
messages and activities by `turnId`. The Gateway owns the shared Markdown
presentation while Channel adapters retain platform escaping and segmentation.

Project creation or deletion may be optional. Project listing is part of the
managed application model. An adapter declares `projectMode`:

```text
managed
flat
fixed
```

Flat and fixed applications omit `ProjectRef`; they still provide the full
thread surface. A fixed cwd/workspace is application configuration, not a
synthetic Project.

Project and Thread capabilities use `reading` for authoritative resource
lookup. Conversation selection is a Gateway capability, so application
capabilities do not advertise `selection` or `switching`. Mutable native UI
selection is advertised separately as `nativeThreadActivation`.
`attachmentSources` lists accepted `local_path`, `remote_url`, and/or
`attachment_handle` source forms.

### Required runtime surface

```text
sendInput(threadRef, message, clientMessageId)
subscribeThread(threadRef, afterCursor?)
```

Optional runtime capabilities:

```text
interruptTurn
respondRequest
replayFromCursor
activateNativeThread
```

`activateNativeThread` exists only for applications with mutable native
selection. It is expressed as the explicit `thread.activate_native`
application operation. The Gateway's `conversation.bind_thread` operation
never invokes it implicitly and it does not replace the Conversation binding.

### Snapshot and subscription

An adapter should expose enough information to implement:

```text
subscribe
→ read authoritative snapshot/history
→ reconcile projection
→ consume ordered events
→ re-read on sequence gap
```

If replay is unavailable, the capability says so and reconnect falls back to a
fresh snapshot.

Subscriptions to one Thread must fan out. Each active subscriber receives the
same canonical events in publication order and owns its own consumption
position. Cancelling or slowing one subscriber cannot consume, delay, or
discard another subscriber's events.

`subscribeThread` registers the live observer before returning its iterator.
The Gateway establishes that subscription before `sendInput`, so an
application that emits native notifications synchronously during input
acceptance cannot race past observation.

Native socket/read callbacks publish into subscriber queues without awaiting
IM delivery. Queue consumption and Channel delivery run downstream. The SDK
does not create a second transcript to implement this fan-out; authoritative
recovery still comes from the application snapshot/history surface.

`message.completed` is a complete Message only. Adapters preserve every such
event and emit a separate explicit terminal Turn event.

### Application-specific mappings

#### Zen

- Thread maps to the Zen App Server Thread.
- Messages and execution events map to canonical Items and protocol events.
- Project is exposed by an outer Zen CLI/ZenX/App Server workspace registry,
  not forced into the append-only Agent Runtime.

#### T3 Code

- Project and Thread map to T3 native resources.
- T3's provider/model/runtime controls remain T3-specific metadata or product
  operations unless later proven common.
- External user items must be included in the same projection used by local
  T3 input.

#### Codex CLI and Codex App

- Thread maps to the native Codex session/thread.
- Project normally maps to repository/workspace root, not remote URL alone.
- The adapter uses Codex's native control surface; this does not make Codex an
  execution backend inside Zen.

#### Claude Code

- Thread maps to the native session.
- Project normally maps to the working directory/session grouping.
- Resume, interruption, permission, and event semantics are translated by the
  adapter rather than generalized from CLI output text.

## Capability negotiation

Capabilities are queried before an operation is exposed in UI or executed.

Feature support and project shape are separate declarations.

Feature outcomes are:

```text
supported natively
supported with declared adapter fallback
unsupported
```

Destructive and security-sensitive behavior cannot use silent fallback.

## Contract tests

Every Channel adapter should pass common tests for:

- stable identity;
- duplicate inbound delivery;
- Markdown/plain-text fallback;
- segmentation order;
- attachment limits;
- edit and retry semantics;
- receipt meaning.

Every Agent application adapter should pass common tests for:

- reference scoping;
- pagination without binding side effects;
- create then read/list;
- thread-read validation independently from native activation;
- deletion semantics;
- stable client message ID round-trip;
- canonical user and Agent message events;
- snapshot plus event reconciliation;
- explicit unsupported capabilities.

## Research references

Useful implementation patterns:

- [OpenClaw channel routing](https://docs.openclaw.ai/channels/channel-routing)
  for deterministic bindings;
- [OpenClaw Gateway clients](https://docs.openclaw.ai/gateway/clients) for
  snapshot/event recovery;
- [QwenPaw architecture](https://qwenpaw.agentscope.io/docs/architecture/) for
  channel event projection and per-session queues.

Their complete Agent/session/workspace ownership models are intentionally not
copied into this SDK.
