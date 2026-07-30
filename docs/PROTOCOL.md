# Messages, operations, and events

This document defines semantic contracts, not a wire encoding. JSON, Python,
TypeScript, JSON-RPC, HTTP, WebSocket, or an in-process implementation may
carry the same objects.

## Common envelope rules

Every externally visible mutation or event has a stable identifier.

Identifiers must not be synthesized by comparing text or timestamps. At
minimum, integrations preserve:

```text
channelMessageId
clientMessageId
agentItemId
operationId
eventId
applicationInstanceId
projectRef?
threadRef?
turnId?
requestId?
sequence or cursor
```

Unknown adapter-native fields may be carried as namespaced metadata, but core
behavior cannot depend on undocumented metadata.

## Message objects

Message represents content, not control intent.

The common content model is used by two envelopes:

```text
ChannelMessage {
  messageId
  conversationRef
  sender
  role?
  content[]
  replyTo?
  createdAt
  metadata
}

AgentMessage {
  agentItemId
  threadRef
  role
  content[]
  clientMessageId?
  createdAt
  metadata
}
```

The Gateway translates `ChannelMessage` into `AgentInput`. The application
emits authoritative `AgentMessage` objects. They share content semantics but do
not pretend an IM Conversation and an Agent Thread are the same address.

Initial content parts:

```text
Text {
  text
  format: plain | markdown
}

Attachment {
  attachmentId
  mediaType
  filename?
  sizeBytes?
  url?
  metadata
}
```

The common object preserves Markdown source. Channel adapters decide how to
escape, chunk, render, edit, or fall back to plain text.

### Stable client message ID

The Gateway derives or preserves a stable client message ID from the native
channel message identity:

```text
(channelInstanceId, conversationId, channelMessageId)
  -> clientMessageId
```

The selected Agent application receives that ID with the user input and emits
it again on the canonical user-message event. This enables optimistic local
echo without duplicates and makes externally originated user messages visible
to desktop and Web clients.

## Operation object

Operation represents explicit control intent.

```text
Operation {
  operationId
  conversationRef
  actor
  type
  target
  arguments
  createdAt
}
```

The initial operations are:

| Operation | Effect |
|---|---|
| `application.list` | List configured Agent application instances |
| `project.list` | List projects exposed by one application |
| `project.select` | Select a project and clear an incompatible thread |
| `thread.create` | Create a thread and select it |
| `thread.list` | List threads, optionally scoped to a project |
| `thread.switch` | Validate, subscribe to, and select a thread |
| `thread.delete` | Delete or archive according to explicit capability |
| `thread.status` | Read normalized status |
| `turn.interrupt` | Interrupt a running turn when supported |
| `request.respond` | Respond to approval or user-input request |

### Thread creation

```text
thread.create {
  applicationRef
  projectRef?
  title?
  initialContext?
}
```

Successful creation selects the new thread for the originating Conversation.
Whether a product offers lazy creation after `/new` is a product interaction
choice; the core operation itself creates a real thread.

### Thread switching

```text
thread.switch {
  threadRef
}
```

Execution order:

1. resolve the application instance;
2. validate and read the target thread;
3. establish recoverable observation of the target;
4. atomically update the Conversation binding;
5. emit `binding.changed`;
6. route subsequent messages to the selected thread.

If the application requires a native "activate/open thread" call, its adapter
performs it. The shared semantic result is still the Conversation binding.

### Thread deletion

Deletion capability is explicit:

```text
unsupported
archive
permanent
```

The adapter must not map a requested permanent delete to archive without
reporting the actual result. Deleting the selected thread clears the thread
selection but keeps a compatible application/project selection.

### Listing

List operations are paginated and side-effect free:

```text
ListResult<T> {
  items[]
  nextCursor?
}
```

`thread.list` may be scoped to one project or to the whole application when
supported.

## Operation result

```text
OperationResult {
  operationId
  status: succeeded | failed
  value?
  error?
  completedAt
}
```

Errors include a stable code, user-safe message, retryability, and optional
adapter-native diagnostic metadata.

## Unified Agent event

AgentEvent is the application-to-client observation stream.

```text
AgentEvent {
  eventId
  applicationInstanceId
  projectRef?
  threadRef?
  turnId?
  sequence
  cursor?
  type
  data
  createdAt
}
```

Initial event types:

### Message events

```text
message.created
message.delta
message.completed
```

`message.delta` is transient. `message.completed` contains the complete
canonical item. Deltas need not be journaled by the SDK.

### Thread events

```text
thread.created
thread.updated
thread.deleted
```

### Turn events

```text
turn.started
turn.completed
turn.failed
turn.interrupted
```

### Status and request events

```text
status.changed
request.opened
request.resolved
```

Request types initially include:

```text
approval
user_input
```

Approval is distinct from sandbox policy and from IM sender authorization.

### Gateway event

```text
binding.changed
```

This event is scoped to the Gateway/Conversation rather than the Agent thread.
It allows multiple IM views or devices to update their selected context.

## Ordering

- `sequence` is monotonic within the adapter's declared event scope.
- `cursor` is opaque and only interpreted by the producing adapter.
- timestamps are descriptive and never define authoritative order.
- a repeated `eventId` is the same event and must be idempotently projected.
- a detected gap triggers snapshot/history reconciliation.

## Delivery projection

Channel delivery identity is derived from:

```text
(destination, sourceEventId, segmentIndex)
```

It is not derived from Turn ID alone because one Turn may produce multiple
visible items and one item may require several channel segments.

A delivery receipt distinguishes:

```text
accepted_by_platform
rejected_by_platform
unknown
```

Platform acceptance does not claim that a user's device displayed or read the
message.
