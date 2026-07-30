# Domain model

## Overview

The full common resource hierarchy is:

```text
AgentApplication
  └── Project
        └── Thread
```

The hierarchy is an application-facing organization model. Runtime shape is
declared by `projectMode`:

```text
managed: AgentApplication → Project → Thread
flat:    AgentApplication → Thread
fixed:   AgentApplication(fixed cwd/workspace) → Thread
```

## Identity rule

Native IDs are opaque and scoped by an Agent application instance.

```text
ApplicationRef = applicationInstanceId

ProjectRef = (
  applicationInstanceId,
  nativeProjectId
)

ThreadRef = (
  applicationInstanceId,
  nativeThreadId,
  projectRef?
)
```

A native thread ID is never assumed to be globally unique. References from
different Agent application instances cannot be compared or substituted.

## Agent application

An Agent application instance is one configured control endpoint.

Examples:

- a local Zen App Server;
- a remote Zen deployment;
- one T3 Code Server;
- one Codex CLI/App Server environment;
- one Claude Code integration.

Two instances of the same application kind may expose different credentials,
homes, projects, and threads.

Minimum fields:

```text
applicationInstanceId
kind
displayName
capabilities
metadata
```

## Project

A Project organizes threads around a working context.

Minimum fields:

```text
projectRef
displayName
rootPath?
repoUrl?
metadata
```

The native representation varies:

| Application | Likely project projection |
|---|---|
| T3 Code | Native Project |
| Codex CLI | Repository/workspace root or cwd |
| Codex App | Native project/workspace projection |
| Claude Code | Working directory and its sessions |
| Zen | External client/App Server workspace registry |

Repository URL alone is not a safe project identity:

- a local repository may have no remote;
- one remote may have several worktrees;
- the same remote may be cloned more than once;
- non-Git workspaces still need projects.

An adapter should prefer a stable native application ID. `rootPath` and
`repoUrl` are descriptive metadata, not universal identity.

### Project modes

`managed` applications expose Project discovery and authoritative reads.
Gateway-owned Conversation selection may then bind to a returned `ProjectRef`.
Thread list and creation may be scoped by `ProjectRef`.

`flat` applications expose a single application-wide thread list. Thread
references omit `projectRef`.

`fixed` applications are configured with one cwd/workspace outside the shared
contract. Their threads are also flat and omit `projectRef`. The configured
working context may be exposed as application metadata, but it is not promoted
to a fake Project resource.

Thread creation, listing, reading, deletion, status, input, and events remain
available in all three modes according to their own capabilities. Gateway
binding and optional native activation are separate from those resource
operations.

### Project and Zen Core

Project is a first-class IM Agent SDK resource without becoming a required Zen
Runtime object.

Zen may keep its Agent Runtime as an append-only Thread/ItemList. A Zen-facing
application layer can expose projects and the project-to-thread index outside
that runtime. This preserves both:

- a consistent SDK navigation model; and
- a small Zen Core.

## Thread

A Thread is an Agent application's durable conversation and execution context.

Minimum summary fields:

```text
threadRef
title?
status
updatedAt?
metadata
```

The Agent application owns:

- thread creation and deletion semantics;
- transcript and item identity;
- turn execution;
- model or runtime configuration;
- interruption and interactive requests;
- archival and retention.

The SDK may expose these capabilities but does not redefine their native truth.

## Conversation

A Conversation is the stable destination within one configured Channel
instance.

```text
ConversationRef = (
  channelInstanceId,
  nativeConversationId
)
```

Examples:

- QQ direct message: `c2c:<openid>`;
- QQ group: `group:<group_openid>`;
- Telegram chat plus topic;
- Slack channel plus thread timestamp;
- Feishu chat or topic.

`channelInstanceId` is required because two bot accounts on the same platform
may receive identical-looking native conversation IDs.

## Binding

A Binding records the currently selected Agent context for one Conversation.

```text
Binding {
  conversationRef
  applicationRef?
  projectRef?
  threadRef?
  revision
  updatedAt
}
```

Invariants:

- project and thread references belong to the selected application;
- a thread's project, when known, matches the selected project;
- changing the project clears an incompatible selected thread;
- deleting the selected thread clears the thread selection;
- listing resources never changes the binding;
- binding changes are atomic from the Conversation's perspective.
- selecting a Thread for Conversation input does not imply native application
  activation;
- native activation, when supported, is an explicit application operation and
  does not mutate a Conversation binding.

## Thread projection route

A Thread projection route is minimal Gateway-owned delivery state:

```text
ThreadProjectionRoute {
  routeId
  threadRef
  conversationRef
  replyToMessageId?
  updatedAt
}
```

It does not select future input and does not activate a native application
Thread. It contains no Agent message, transcript, Turn state, or execution
state. Those values are always recovered from the Agent application.

Route IDs are stable for a `(ThreadRef, ConversationRef)` pair. A product
chooses `foreground_only`, `remembered_last_recipient`, or `all_observers`
policy. The first policy filters delivery through the current input binding;
the second replaces the remembered destination for that Thread; the third
keeps multiple explicit observers.

## Thread status

The initial normalized states are:

```text
idle
running
waiting_for_approval
waiting_for_input
completed
failed
interrupted
unknown
```

Status is a projection from the Agent application snapshot and events. The
Gateway does not maintain an independent authoritative status table.
