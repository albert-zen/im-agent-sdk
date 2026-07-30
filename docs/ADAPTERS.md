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
execute(Operation) -> OperationResult
```

The deliberately small `execute` seam owns project/thread control operations;
the adapter maps each typed Operation to its native application API. This is a
deep module boundary rather than a method-per-resource mirror.

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
selection. It does not replace the Gateway's Conversation binding.

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
- switch validation;
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
