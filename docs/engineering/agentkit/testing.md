# AgentKit testing

## Focused lifecycle checks

Use the checked-in launcher, never an assumed global executable:

```sh
uv run python scripts/agentkit.py doctor
uv run python scripts/agentkit.py orient --path docs/engineering/agentkit/design.md
uv run python scripts/agentkit.py intent-guidance --component agentkit --change-type docs
uv run python scripts/agentkit.py docs-impact --path docs/ARCHITECTURE.md
uv run python scripts/agentkit.py check
uv run python scripts/agentkit.py lint-architecture
uv run python scripts/agentkit.py review-guidance
```

The `check` and `lint-architecture` commands must show the isolated
component-map validation before AgentKit is delegated to. Run the mapping tests
when changing `agentkit.yml`, the launcher, the component map, or routing docs:

```sh
PYTHONPATH=src uv run python -m unittest tests.engineering.test_agentkit tests.engineering.test_repository_maintainability -v
PYTHONPATH=src uv run python -m unittest discover -s tests/engineering -p 'test_agentkit.py' -v
```

Both focused invocations must discover the complete AgentKit mapping suite.
The historical `tests/test_agentkit_mapping.py` path is absent, with no
compatibility or import shim; the launcher and its component-map pre-gate
semantics are unchanged.

## Lifecycle evidence

A substantial change records:

- the exact `start --task` intent;
- the durable intent sources and affected components selected by orientation;
- focused tests and all applicable repository gates;
- `check` and `review-guidance` output;
- review completion or the explicit human-directed reason a clean-context
  review is deferred; and
- a close receipt without committing `.agentkit/` state.

Mapping tests must keep the component map authoritative: no second import
graph in `agentkit.yml`, no broad source catch-all, and no durable file without
an owner. Launcher failures must be explicit when `uv`/`uvx` or the pinned
source cannot be used.

## Launcher and clean-checkout evidence

The portable launcher is `scripts/agentkit.py`, pinned to one AgentKit commit;
the POSIX and Windows wrappers are thin entrypoints. No launcher may depend on
a sibling checkout or globally installed `agentkit`. Both architecture
commands run the isolated component-map validator before delegation.

Verify the operational boundary and use a temporary clean checkout when the
host supports it:

```sh
git check-ignore .agentkit/task.json
uv run python scripts/agentkit.py orient --path docs/engineering/agentkit/design.md
uv run python scripts/agentkit.py docs-impact --path docs/ARCHITECTURE.md
```

## Deletions and moves

AgentKit v1 reports deleted paths against the post-change manifest. Until its
deletion/rename support is fixed, review a deletion against the base manifest
and replacement mapping instead of retaining stale mappings or empty shims.
The durable component inventory remains authoritative for the post-change tree.

## No-behavior guarantee

AgentKit checks are repository diagnostics. They must not call native Channel
or Application APIs, create runtime subscriptions, persist transcript or
execution state, or change admission/delivery behavior. A change to the
launcher or routing config is complete only when the normal repository checks
and a clean-checkout smoke are still reproducible.
