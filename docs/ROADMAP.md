# Roadmap and open decisions

## Current phase: design review

The first repository revision contains design only.

Exit criteria:

- resource hierarchy is accepted;
- Message and Operation separation is accepted;
- initial operation names and semantics are accepted;
- unified event types are accepted;
- state authority and persistence boundary are accepted;
- Channel and Agent application adapter responsibilities are accepted.

## Milestone 1: contracts and test kit

Deliverables:

- language-neutral schema for resources, messages, operations, results, and
  events;
- capability schemas;
- reference validators;
- Channel adapter contract test kit;
- Agent application adapter contract test kit;
- in-memory Conversation binding repository;
- no production channel or application adapter yet.

## Milestone 2: prove both sides

Suggested vertical slice:

- one existing IM channel, initially QQ;
- Zen Agent application adapter;
- T3 Code Agent application adapter;
- create/list/switch/delete/status;
- message send and canonical user/Agent event synchronization;
- snapshot plus live-event recovery;
- Markdown projection and Full Access as product configuration.

The milestone is successful only when one IM conversation can switch between
real Zen and T3 threads without either adapter inventing a second transcript.

## Milestone 3: extract existing channel work

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

### Implementation language

Options:

- Python first, reusing the working IMCodex-family channel adapters;
- TypeScript first, aligning with Zen and T3 implementations;
- language-neutral schemas plus a Python reference Gateway.

Current recommendation: define language-neutral schema, then build the first
Gateway/reference adapters in Python because the proven channel edge is
Python. Revisit only after the contract review.

### Wire protocol

The semantic model intentionally does not yet choose JSON-RPC, HTTP, WebSocket,
or an in-process interface.

Current recommendation:

- adapters embedded in one process use native language interfaces;
- remote Agent applications use their native protocol through an adapter;
- add a dedicated SDK wire protocol only after two out-of-process adapters
  prove the same transport requirement.

### Project requirement

Project is a first-class common resource. Some applications may not expose a
discoverable project registry.

Decision still needed:

- require every adapter to synthesize a Project; or
- allow explicit `projects: unsupported` and threads without `projectRef`.

Current recommendation: allow explicit unsupported capability while keeping
Project in the common model.

### Conversation versus user selection

Initial binding is per IM Conversation. Group products may want different users
in one group to select different threads.

Current recommendation: keep conversation-level selection in the core and let
products opt into a principal-scoped binding repository later.

### Delete semantics

Applications differ between archive and permanent delete.

Current recommendation: expose both semantics through capability and result;
never silently reinterpret a permanent-delete request.

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
