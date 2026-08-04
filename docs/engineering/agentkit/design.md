# AgentKit design

## Purpose

AgentKit is the repository's maintainability harness. It routes a change to
durable intent, checks declared ownership and architecture, records lifecycle
receipts, and supplies review guidance. It does not understand or author
product intent and it is not an Agent runtime, transcript store, policy engine,
service locator, or general orchestrator.

## Durable inputs and boundary

The checked-in launcher is `scripts/agentkit.py`; it invokes the pinned
AgentKit revision through `uvx`. `agentkit.yml` declares change routing,
required docs, review policy, and maintainability budgets. The component map is
the single source for ownership and import linting. The plugin skill documents
the human workflow at
[`plugins/agentkit/skills/agentkit/SKILL.md`](../../../plugins/agentkit/skills/agentkit/SKILL.md).

Task state, receipts, and logs under `.agentkit/` are ignored operational
state. They are useful for the current lifecycle but are not repository
authority and must never be committed.

Before `check` or `lint-architecture`, the repository launcher runs the
isolated component-map validator. This keeps AgentKit from becoming a second
import-layer graph. Unknown modules, forbidden edges, cycles, undeclared
facades, and stale exceptions fail before delegation.

The pinned launcher sets UTF-8 mode for its child process so clean checkouts do
not decode check output through a locale-specific code page. It never relies on
a sibling checkout or a globally installed AgentKit executable.

AgentKit v1 has three known limitations: component path matching uses one
generic `code` field for source and tests; global design/workflow docs are
first-class but arbitrary global intent docs have no separate read-only or
reverse-impact role; and deleted paths are matched only against the post-change
manifest. The repository records the current fallbacks in configuration
comments and mapping tests. Upstream tracking is maintained in
[AgentKit issue #3](https://github.com/albert-zen/AgentKit/issues/3),
[issue #4](https://github.com/albert-zen/AgentKit/issues/4), and
[issue #5](https://github.com/albert-zen/AgentKit/issues/5).

The repository does not use broad `src/**` catch-alls, stale deleted-file
mappings, or empty compatibility files to hide these limitations. Mapping may
split one documentation responsibility into stable subcomponents when a code
path should read only its applicable adapter page.

## Lifecycle

Substantial architecture, public-contract, state-model, cross-component,
adapter, or plugin changes use one durable lifecycle:

1. `start --task` records the task and affected intent sources;
2. `orient`, `intent-guidance`, and `docs-impact` route work before edits;
3. implementation and focused evidence follow the authoritative docs;
4. `check` and `review-guidance` validate mapping and review obligations; and
5. `close --review-complete` records completion after the review loop.

Read-only orientation and small, isolated reversible edits may skip the
lifecycle. If scope expands, start it before continuing. A missing product,
architecture, API, recovery, or failure decision is a human question—not an
invitation to invent durable meaning.

## Review boundary

AgentKit can route and remind; it cannot replace architecture acceptance,
clean-context review, or engineering judgment. Reviewers receive the durable
intent paths, changed files, and validation evidence. A clean-context review
is a separate gate when the task's owner requires it.

## Change obligations

A launcher pin, lifecycle, routing, or architecture-gate change updates
`agentkit.yml`, the checked-in skill, mapping tests, and doctor/check/routing
smoke evidence in the same slice. Operational `.agentkit/` state remains
ignored and is never committed.
