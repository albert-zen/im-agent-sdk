# Vision

## Purpose

IM Agent SDK is a thin semantic bridge between multiple instant-messaging
Channels and multiple Agent Applications.

It gives incompatible IM platforms and Agent Applications one small resource,
message, operation, event, and adapter vocabulary without becoming a second
Agent framework.

Intended Agent Applications include Zen, T3 Code, Codex, Claude Code, and
future products. Intended Channels include QQ, Telegram, Feishu, Weixin,
DingTalk, Slack, and others.

## Authority boundary

Agent Applications always own:

- Project and Thread resources;
- transcript/item identity and authoritative history;
- Turn, request, approval, execution, and interruption truth;
- model, provider, workspace, sandbox, and native runtime behavior.

The SDK owns only IM bridge state:

- Channel transport and native delivery behavior;
- Conversation input bindings;
- outbound Thread projection routes;
- delivery correlation and idempotency;
- necessary reconnect/recovery projections that can be rebuilt from the
  authoritative Application.

The SDK never creates a second transcript, Agent state machine, execution
runtime, request authority, policy engine, or general orchestrator.

## Experience goal

A user can start work from IM, continue the same native Thread in another
client, then return without divergent histories. The surfaces are multiple
views over one Agent Thread, not bots copying content between independent
transcripts.

## Common semantic model

The SDK standardizes:

1. **Resources** — Application, Project, Thread, Conversation, binding, and
   projection route references.
2. **Messages** — inbound, outbound, and authoritative Agent content.
3. **Operations** — explicit control intent.
4. **Events** — canonical message and lifecycle observations.
5. **Capabilities** — honest support and explicit unsupported outcomes.

`Message` carries content. `Operation` carries control intent. Slash commands,
buttons, and natural-language interpreters may create typed Operations, but
their product grammar is not Core.

## Core admission rule

A concept enters SDK Core only when it is shared semantics rather than one
consumer's convenience.

- Agent-side semantics normally require evidence from at least two different
  Agent Applications.
- Channel-side behavior normally requires evidence from at least two different
  IM Channels.
- The design review must include a second implementation or a counterexample,
  not only Codex/IMCodex.

If only one product currently needs the behavior, prefer its concrete adapter,
consumer composition, optional Controller/contrib package, or a neutral
extension seam. Do not turn a Codex or IMCodex policy into a universal
contract.

Every design classifies a proposal:

| Class | Meaning | Typical owner |
|---|---|---|
| Core invariant | required for interoperable semantics and safety across implementations | Contracts/Core/Gateway infrastructure |
| Optional capability | common shape with honest per-integration support | Contracts plus concrete adapters |
| Adapter-specific policy | native translation, limits, recovery, rendering, or API behavior | Channel/Application adapter |
| Consumer policy | product UX, permissions, configuration, retry appetite, or orchestration | IMCodex or another downstream composition |

See the accepted
[Core admission and policy ownership ADR](decisions/0006-core-admission-and-policy-ownership.md).

## Principles

### One authoritative Agent history

Completed messages and recovery come from native authoritative
history/snapshot/catch-up. SDK projections can be cached or checkpointed only
when deletion and reconciliation reproduce the same Agent truth.

### Deterministic, separated routing

Conversation input selection, optional native Thread activation, and outbound
Thread projection are separate mutations. A model does not silently choose
their targets.

### Stable identity and honest ordering

Externally visible mutations and events have stable IDs. Text and timestamps
never define deduplication. Cursor/sequence guarantees are declared only when
the native producer preserves them; otherwise recovery reconciles from
authoritative history.

### Explicit capability differences

Integrations expose native support, declared fallback, or unsupported behavior.
Destructive, security-sensitive, attachment, replay, and request behavior is
never silently approximated.

### Small shared core

Channel Markdown, chunking, media, reply, credential, and delivery behavior
stays in Channel adapters. Application workspace, provider/model, sandbox,
request, and runtime behavior stays in Application adapters. Product commands
and permissions stay in Controllers or consumers.

### Maintainability is a boundary property

The goal is clear ownership, one-way dependencies, explicit failure,
diagnosability, and reviewable modules. More abstractions are not a goal.
Repeated review guidance should become tests, schemas, architecture rules, or
component documentation.

## Non-goals

The SDK is not:

- an Agent runtime or model provider;
- a replacement for native App Servers;
- a durable transcript or execution store;
- a universal permission, credential, sandbox, or approval policy engine;
- a project scheduler or multi-Agent orchestrator;
- a UI framework or all-in-one Agent product;
- a durable delivery job system without proof from multiple real consumers.
