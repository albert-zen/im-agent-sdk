# Vision

## Purpose

IM Agent SDK makes different Agent applications controllable from different
instant-messaging channels through one resource, operation, and event model.

Its long-term value is not a particular QQ, Telegram, or Feishu bot. Its value
is the stable seam between:

- IM platforms with incompatible message and interaction capabilities; and
- Agent applications with incompatible project, thread, execution, and event
  models.

The intended applications include Zen, ZenX, T3 Code, Codex CLI, Codex App,
Claude Code, and future coding-agent products. The intended channels include
QQ, Telegram, Feishu, Weixin, DingTalk, Slack, and other IM systems.

## Product thesis

Coding-agent applications are converging on a common shape:

```text
Agent application
  └── Project or workspace
        └── Thread or session
              └── Messages, turns, requests, and execution events
```

Their native APIs and semantics still differ. IM Agent SDK gives an IM client a
consistent way to:

- discover Agent application instances;
- list and select projects;
- create, list, switch, delete, and inspect threads;
- send messages to the selected thread;
- observe user messages, Agent output, streaming deltas, status changes,
  failures, interruptions, approvals, and user-input requests.

## Experience goal

A user can start work from an IM conversation, continue it in ZenX or T3 Code,
then return to IM without losing the thread or seeing divergent histories.

The experience should feel like multiple views over one Agent thread, not like
multiple bots copying messages between independent transcripts.

## Core concepts

The SDK standardizes four different kinds of objects:

1. **Resources** — Agent application, project, and thread.
2. **Messages** — content sent by users and produced by Agents.
3. **Operations** — explicit control intent such as switching or deleting a
   thread.
4. **Events** — the unified stream through which clients observe canonical
   messages and lifecycle changes.

Messages and operations are deliberately separate. A slash command, button, or
natural-language intent may create an operation, but the core operation is not
stored as magic chat text.

## Principles

### One authoritative Agent history

The connected Agent application remains authoritative for its projects,
threads, transcript, turns, requests, and execution state. The SDK must not
create a competing transcript or another Agent runtime.

### Deterministic routing

Routing is selected by explicit bindings and operations. A model does not
silently choose which application, project, or thread receives a message.

### Cross-client consistency

The same completed user and Agent messages must be observable from IM, desktop,
CLI, and Web clients. Stable IDs and ordered cursors are contract requirements,
not implementation details.

### Honest capability differences

Channel and Agent application features vary. The SDK exposes capabilities and
explicit unsupported errors instead of pretending all integrations can edit,
delete, stream, approve, or manage projects identically.

### Small shared core

The common model contains only semantics proven across integrations.
Platform-specific rendering and Agent-specific runtime behavior stay in their
adapters.

## Non-goals

The SDK is not:

- an Agent runtime or model provider;
- a replacement for Zen App Server, T3 Server, Codex App Server, or Claude
  Code;
- a project scheduler or multi-Agent orchestration engine;
- a universal credential store;
- a second durable transcript;
- a sandbox or tool-approval policy engine;
- a UI framework;
- an OpenClaw- or QwenPaw-style all-in-one Agent product.

Products built with the SDK may provide those capabilities, but they are not
part of the SDK core.
