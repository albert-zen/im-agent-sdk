# Architecture

## System shape

```text
┌──────────────────────────────────────────────────────────────┐
│ IM channels                                                  │
│ QQ · Telegram · Feishu · Weixin · DingTalk · Slack · …      │
└─────────────────────────────┬────────────────────────────────┘
                              │ native messages/actions
                    ┌─────────▼─────────┐
                    │ Channel adapters │
                    └─────────┬─────────┘
                              │ Message / Operation
              ┌───────────────▼────────────────┐
              │ IM Agent Gateway               │
              │                                │
              │ routing · bindings · dedup     │
              │ delivery projection · cursors │
              └───────────────┬────────────────┘
                              │ common application contract
                ┌─────────────▼─────────────┐
                │ Agent application adapters│
                └──────┬──────┬──────┬─────┘
                       │      │      │
                    Zen/T3  Codex  Claude Code · …
```

IM pages are not a separate architectural tier from CLI, desktop, or Web
clients. They are another access surface using the same Agent application
capabilities.

## Components

### Channel adapter

A Channel adapter translates between one configured IM bot/account instance
and the common channel contract.

It owns:

- credentials and connection lifecycle;
- native sender and conversation identity;
- inbound event verification and access-control inputs;
- native Markdown, cards, buttons, mentions, replies, and attachments;
- text limits, chunking, escaping, and fallback rendering;
- native message edit, typing, and delivery-receipt behavior;
- channel reconnect cursors and tokens when required by the platform.

It does not own projects, Agent threads, turns, or Agent approvals.

### Agent application adapter

An Agent application adapter translates between one configured Agent
application instance and the common application contract.

It exposes:

- application metadata and capabilities;
- project discovery and optional management;
- thread creation, listing, lookup, deletion, and history;
- user input delivery with a stable client message ID;
- thread status, interruption, and interactive requests;
- authoritative snapshots and ordered event subscriptions.

It owns native translation, not the resources themselves. The native
application remains authoritative.

### Gateway

The Gateway composes Channel and Agent application adapters.

It owns:

- the current application/project/thread binding for an IM conversation;
- typed Gateway operations that atomically mutate those bindings;
- deterministic routing;
- inbound idempotency;
- outbound projection and delivery correlation;
- per-conversation serialization and backpressure;
- reconnect cursors and projection caches;
- access policy at the IM boundary.

It does not own:

- the authoritative project or thread registry;
- the Agent transcript;
- an independent Turn state machine;
- model, provider, workspace, or sandbox configuration;
- Agent tool-approval truth.

Attachment bytes cross the Channel/Application boundary only through an
explicit source form. Shared-filesystem trust is deployment configuration, not
message Metadata and not a claim supplied by an inbound attachment. Remote URL
materialization belongs to the accepting Application adapter and its network
security policy.

The Gateway accepts an optional inbound Controller. A Controller may consume an
inbound message and invoke the same typed application/Gateway operations that
native buttons or other interactions invoke. If no Controller consumes the
message, the Gateway routes its content as normal Agent input. The Gateway does
not know Slash grammar, command aliases, selection-list caches, or fixed command
presentation.

The SDK-distributed `SlashController` provides the default common Slash UX. It
is a replaceable composition choice, not a Gateway dependency. Its Markdown
presenter owns the default English help, lists, errors, history, and catch-up
views.

Application operations and Gateway operations are separate typed families.
The Gateway may route an application operation to the referenced adapter, but
it does not reinterpret that operation as a binding mutation. Conversely,
binding a Conversation to a Thread validates the authoritative Thread without
implicitly changing an application's native active/open Thread.

## Two planes and one event stream

The input side has two planes:

```text
Data plane:    Message
Control plane: Operation
```

The output side is a unified event stream:

```text
Event plane:   AgentEvent
```

This separation allows the same operation to originate from:

- a slash command;
- an IM button or card action;
- a menu;
- a natural-language command interpreter;
- an API call.

The core never depends on how the operation was expressed.

## Authority and persistence

| State | Authority | May the Gateway persist it? |
|---|---|---|
| Agent application registry | Deployment/product | Configuration only |
| Project registry | Agent application | Cache only |
| Thread registry | Agent application | Cache only |
| Thread transcript | Agent application | Projection/cache only |
| Turn and request state | Agent application | Projection/cache only |
| Conversation selection | Gateway | Yes |
| Inbound idempotency | Gateway | Yes |
| Outbound delivery correlation | Gateway | Yes |
| Reconnect cursor | Gateway | Yes |
| Channel credentials | Channel deployment | External configuration |

Gateway-persisted state must not become a second source of Agent truth.
Deleting the Gateway projection cache and replaying from the application must
produce the same visible Agent history.

## Conversation binding

The default binding key is:

```text
(channelInstanceId, conversationId)
```

Its value is:

```text
applicationInstanceId
projectRef?
threadRef?
revision
```

The binding describes the current destination for future messages. It does not
change the identity or history of the selected thread.

Some group-chat products may eventually require per-user selection:

```text
(channelInstanceId, conversationId, principalId)
```

That is a product policy and remains an open extension. The initial common
contract uses conversation-level binding.

## Message flow

1. A Channel adapter verifies and normalizes a native inbound message.
2. Access policy runs before attachment download or Agent mutation.
3. An optional Controller may translate a slash command, button, or other
   interaction into a typed operation; normal content continues unchanged.
4. The Gateway derives a stable client message ID from the native identity.
5. The Gateway resolves the conversation binding.
6. The selected Agent application adapter sends the input to the selected
   thread.
7. The Agent application broadcasts the canonical user item and subsequent
   Agent events.
8. The Gateway projects those events into the capabilities of each subscribed
   channel.

The canonical user-item event is required even when the originating client
already displayed an optimistic local message. Clients deduplicate by stable
ID.

Native notification producers fan out into independent subscriber streams and
return without waiting for Channel delivery. Slow IM projection therefore
cannot stall an application socket read path. Projection exits only on an
explicit terminal Turn event, never merely on `message.completed`.

## Recovery flow

A live event stream alone is insufficient for seamless cross-client work.

An adapter that supports recovery should provide:

1. subscription establishment;
2. an authoritative thread snapshot or history page;
3. an opaque replay cursor when natively supported;
4. a scoped, epoch-qualified sequence when natively supported;
5. explicit cursor-expired or gap outcomes.

On reconnect, a Gateway or UI reconciles its projection from authoritative
history, then continues consuming ordered events. Streaming deltas may be
dropped and reconstructed from the final completed message.

When native replay is unavailable, the honest path is subscribe first, read
authoritative history/catch-up, reconcile stable IDs, then drain live events.
The SDK never manufactures a restart-unsafe counter and presents it as a
recoverable sequence.

## Concurrency

- Operations affecting one conversation binding are serialized.
- Inputs targeting one Agent thread preserve application-defined ordering.
- Different threads may progress concurrently.
- Channel delivery for one destination is serialized to prevent reordered
  chunks, edits, and retries.
- A slow IM platform must not block the Agent application's event producer.

## Failure model

Failures are explicit and scoped:

- unsupported capability;
- invalid or stale reference;
- authentication or access denial;
- binding missing;
- project or thread not found;
- application unavailable;
- operation rejected;
- delivery accepted/rejected/unknown;
- event cursor expired or gap detected.

The core does not invent a durable self-healing workflow. Products may retry
safe idempotent reads and deliveries according to platform policy, but failure
must remain visible to the user.

## Security boundaries

Four policies remain distinct:

1. IM sender access control.
2. Agent application authentication.
3. Agent tool approval.
4. Sandbox or execution restriction.

An IM allowlist does not grant Full Access. Full Access does not bypass IM
sender authorization. Sandbox configuration is not inferred from an approval
decision.
