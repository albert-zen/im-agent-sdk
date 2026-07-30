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

The common content model is used by distinct inbound, outbound, and Agent
envelopes:

```text
InboundMessage {
  messageId
  conversationRef
  sender
  content[]
  replyTo?
  createdAt
  metadata
}

OutboundMessage {
  deliveryId
  conversationRef
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

The Gateway translates `InboundMessage` into `AgentInput`. `messageId` is an
observed native identity; `deliveryId` is a stable outbound request identity.
An outbound delivery does not pretend to already have a native message ID,
sender, or Agent role. A Channel adapter returns the eventual native message
identity in `DeliveryReceipt`.

The application emits authoritative `AgentMessage` objects. These envelopes
share content semantics but do not pretend an IM Conversation, an outbound
delivery request, and an Agent Thread are the same resource.

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
  source
  metadata
}

AttachmentSource =
  LocalPath { path }
  | RemoteUrl { url }
  | AttachmentHandle { handleId }
```

The common object preserves Markdown source. Channel adapters decide how to
escape, chunk, render, edit, or fall back to plain text.

Attachment location is never passed through Metadata. `LocalPath` states only
where a Channel adapter staged bytes; it does not grant trust. An application
adapter accepts it only when deployment configuration has explicitly proven a
shared-filesystem boundary. `RemoteUrl` requires adapter-owned URL policy,
network fetching, size/type validation, and redirect controls. The
`AttachmentHandle` variant reserves a future resolver/upload boundary and is
unsupported until one is configured.

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

## Typed operations

Operation represents explicit control intent. The common contract has two
discriminated operation families because application-native mutations and
Gateway-owned bindings have different authorities.

Application operations are sent to exactly one Agent application adapter:

```text
ApplicationOperation {
  operationId
  type
  applicationRef
  typed fields for type
  createdAt
}
```

Gateway operations carry the IM actor and Conversation because they mutate
Gateway-owned selection state:

```text
GatewayOperation {
  operationId
  type
  conversationRef
  actor
  typed fields for type
  createdAt
}
```

The initial application operations are:

| Operation | Typed success result | Effect |
|---|---|---|
| `project.list` | `ProjectsListed` | List projects without changing a binding |
| `project.get` | `ProjectRead` | Validate and read one project |
| `thread.create` | `ThreadCreated` | Create a real application Thread |
| `thread.list` | `ThreadsListed` | List threads without changing a binding |
| `thread.get` | `ThreadRead` | Validate and read one thread |
| `thread.activate_native` | `NativeThreadActivated` | Optionally change native application UI selection |
| `thread.delete` | `ThreadDeleted` | Delete/archive with the actual mode reported |
| `thread.status` | `ThreadStatusRead` | Read normalized status |
| `thread.history` | `ThreadHistoryRead` | Read recent authoritative Turns |
| `turn.catchup` | `TurnCatchupRead` | Read latest Turn progress |
| `turn.interrupt` | `TurnInterrupted` | Interrupt a running Turn |
| `request.respond` | `RequestResponded` | Respond to an approval or user-input request |

The initial Gateway operations are:

| Operation | Typed success result | Effect |
|---|---|---|
| `application.list` | `ApplicationsListed` | List configured application instances |
| `application.select` | `ConversationBound` | Select an application and clear project/thread |
| `conversation.bind_project` | `ConversationBound` | Validate/select a project and clear thread |
| `conversation.bind_thread` | `ConversationBound` | Validate/select a thread for future input |
| `conversation.clear_thread` | `ConversationBound` | Clear the selected thread |

Each Python and wire operation variant exposes its fields directly rather than
putting behavior-critical values in `Metadata`. The result is also a
discriminated variant with named fields; callers never infer the runtime type
of an untyped `value`.

### Thread creation

```text
thread.create {
  operationId
  applicationRef
  projectRef?
  title?
  initialContext[]
  createdAt
}
```

Creation and input selection are separate operations. A Controller may create
a Thread and then atomically bind its Conversation to the returned
`threadRef`; the application operation itself never writes a Conversation
binding.

### Thread selection and native activation

```text
conversation.bind_thread {
  operationId
  conversationRef
  actor
  threadRef
  expectedRevision?
  createdAt
}

thread.activate_native {
  operationId
  applicationRef
  threadRef
  createdAt
}
```

`conversation.bind_thread` reads the authoritative Thread to validate it, then
atomically updates the input binding. It never activates, opens, resumes, or
otherwise mutates the Agent application's native active-thread state.
`thread.activate_native` is a separate optional application operation and
capability. A product that needs both invokes both explicitly and handles each
result independently.

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

### Catch-up and history

These are user-facing context restoration operations, not streaming-token
delivery:

```text
turn.catchup {
  threadRef
  limit
}

thread.history {
  threadRef
  limit
  page
}
```

`TurnCatchup` contains the latest Turn status and recent meaningful Agent
progress messages. `ThreadHistory` contains recent Turn entries with the user
goal, final/latest Agent result, terminal status, error, and compaction marker.

Slash commands are one expression of these typed operations:

```text
/catchup [messages]
/history [turns] [--page N]
```

The official optional Slash Controller parses and presents those commands.
Its handler returns `None` when it does not consume an inbound message, or a
tuple of `OutboundMessage` deliveries when it does. A replacement Controller
receives only the typed `ControllerActions` surface: execute an application
operation, execute a Gateway operation, and read the current Conversation
binding. Buttons and channel-native interactions can invoke those same typed
actions directly without manufacturing Slash text.

## Operation results

Every success result repeats the originating operation's discriminant and has
named result fields such as `projects`, `thread`, `status`, `history`,
`catchup`, or `binding`.

```text
ThreadCreated {
  operationId
  type: thread.create
  status: succeeded
  thread
  completedAt
}

ApplicationOperationFailed {
  operationId
  type
  status: failed
  error
  completedAt
}
```

Gateway failures use the corresponding `GatewayOperationFailed` variant.
Errors include a stable code, user-safe message, retryability, and optional
adapter-native diagnostic metadata. An adapter-specific extension may add its
own typed interface outside the common union; it cannot inject an unknown
payload into the common discriminated union.

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

`message.completed` does not mean `turn.completed`. One Turn may emit multiple
complete Agent messages, including application-specific phases carried in
Metadata. A Turn subscription remains active until an explicit
`turn.completed`, `turn.failed`, or `turn.interrupted` event for that Turn.

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

Thread subscriptions are fan-out observations. Every active subscriber gets
its own stream of each published canonical event; subscribers never compete
for one queue. This is live delivery, not an SDK-owned transcript.

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
