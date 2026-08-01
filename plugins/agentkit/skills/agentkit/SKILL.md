---
name: agentkit
description: Keep IM Agent SDK changes tied to durable ownership, component docs, checks, and review.
---

# Using AgentKit In IM Agent SDK

AgentKit is this repository's maintainability harness. It routes a task or
changed path to the authoritative project and component docs; it does not
replace engineering judgment or become an Agent runtime.

## Launcher

Use the checked-in launcher so every environment runs the pinned AgentKit
revision:

```text
python scripts/agentkit.py <command>
```

Do not assume a global `agentkit` executable.

## When To Use The Lifecycle

Run `start` for architecture, public contract, state/data model,
cross-component workflow, adapter ownership, plugin/hook, or other substantial
changes. Read-only orientation and small, isolated, reversible edits may skip
the lifecycle. If small work expands, start before continuing.

```text
python scripts/agentkit.py start --task "<task>"
```

Task state and receipts live under ignored `.agentkit/`; they are operational
state, not product documentation.

## Read Durable Intent

Always treat these as global authority:

- `docs/VISION.md`
- `docs/ARCHITECTURE.md`
- `docs/decisions/README.md`

Then read the affected component's `design.md` and `testing.md` under
`docs/components/`. Contracts also have `protocol.md`; native adapters have
focused pages under their component's `adapters/` directory.

`docs/README.md` maps common code paths to their docs and tests. `agentkit.yml`
is the executable change-impact mapping.

If product behavior, ownership, a public contract, recovery semantics, or a
failure policy is missing or ambiguous, ask the human before inventing durable
meaning.

## Work And Review

Use the repository-aware commands during work:

```text
python scripts/agentkit.py orient --path <path>
python scripts/agentkit.py intent-guidance --component <name> --change-type <type>
python scripts/agentkit.py docs-impact --path <path>
python scripts/agentkit.py check
python scripts/agentkit.py status
python scripts/agentkit.py remind
```

`check` is deterministic and remains useful without an open task. For a
started substantial task, also run:

```text
python scripts/agentkit.py review-guidance
```

When review is expected and subagents are available, use a clean-context
reviewer. Give it durable intent paths, the changed files, and validation
evidence. Fix meaningful findings and review the fixes again. Same-thread
self-review is useful but does not satisfy the clean-context review gate.

## Close

After checks and the review loop:

```text
python scripts/agentkit.py close --review-complete
```

If continuing would require an unsupported assumption:

```text
python scripts/agentkit.py close --blocked-question "<human question>"
```

Do not commit `.agentkit/` task state or receipts.
