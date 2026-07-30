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
- [x] no production channel or application adapter yet.

## Milestone 2: prove both sides

Suggested vertical slice:

- migrate one existing IM channel, initially QQ, from the pinned IMCodex
  implementation rather than rewriting it;
- Zen Agent application adapter;
- T3 Code Agent application adapter;
- migrate the mature Codex App Server protocol/client package when beginning
  the Codex application adapter;
- create/list/switch/delete/status;
- message send and canonical user/Agent event synchronization;
- snapshot plus live-event recovery;
- Markdown projection and Full Access as product configuration.

The milestone is successful only when one IM conversation can switch between
real Zen and T3 threads without either adapter inventing a second transcript.

## Milestone 3: expand migrated channel work

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
