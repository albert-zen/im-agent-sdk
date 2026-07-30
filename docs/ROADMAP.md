# Roadmap and open decisions

## Current phase: Milestone 1 implementation review

The initial design review is complete. The accepted choices are recorded in
`DECISIONS.md`.

Milestone 1 implementation provides:

- [x] language-neutral schemas for resources, messages, operations, results, and
  events;
- [x] capability schemas;
- [x] managed, flat, and fixed project-mode validation;
- [x] reference validators;
- [x] Channel adapter contract test kit;
- [x] Agent application adapter contract test kit;
- [x] in-memory Conversation binding repository;
- [x] durable SQLite binding and idempotency repository;
- [x] production QQ, Telegram, Feishu, and Weixin adapters;
- [x] production Codex App Server, Zen, and T3 application adapters.

## Milestone 2: prove both sides

Implemented vertical slices:

- all four pinned IMCodex channels behind one Channel seam;
- distinct Zen, Codex, and T3 Code application adapters;
- create/list/switch/delete/status slash-command flow;
- message send, final Agent-event projection, and image attachments;
- Markdown-first projection and T3 Full Access as adapter configuration;
- durable bindings, inbound idempotency, and delivery idempotency.

Live edit-in-place delta projection and authoritative snapshot reconciliation
remain the next recovery slice; final messages already use the application as
the sole transcript authority.

## Milestone 3: deepen migrated channel work

Extract reusable behavior from IMCodex/IMZen/IMT3:

- stable channel and conversation identity;
- QQ, Telegram, Feishu, and Weixin adapters;
- sender access policy;
- media staging;
- Markdown fallback and segmentation;
- delivery correlation and conservative retry.

Do not migrate product-specific commands, workspace configuration, provider
selection, or durable job state into the SDK core.

## Milestone 4: more Agent applications

Candidate adapters:

- Codex CLI / Codex App;
- Claude Code;
- OpenCode;
- other App Server or ACP-compatible applications.

Each adapter must first document its authoritative project/thread model and
recovery guarantees.

## Open decisions

### Wire protocol

The semantic model intentionally does not yet choose JSON-RPC, HTTP, WebSocket,
or an in-process interface.

Current recommendation:

- adapters embedded in one process use native language interfaces;
- remote Agent applications use their native protocol through an adapter;
- add a dedicated SDK wire protocol only after two out-of-process adapters
  prove the same transport requirement.

### Binding durability

Some products accept losing the current selection on restart; others require
durable bindings across devices.

Current recommendation: define a repository interface and ship both in-memory
and durable implementations. Durability is a deployment choice, not a core
Agent requirement.

## Deferred

The following remain outside early milestones:

- Agent personas and automatic Agent selection;
- generic multi-Agent orchestration;
- model/provider configuration;
- sandbox and Full Access policy;
- universal media transformation;
- a large plugin system;
- an SDK-owned durable delivery workflow;
- automatic project discovery across a user's entire machine.
