# Repository maintainability testing

## Required evidence

Run the repository-level checks from a clean, dependency-complete checkout:

```sh
uv run python scripts/agentkit.py doctor
uv run python scripts/agentkit.py check
uv run python scripts/agentkit.py lint-architecture
uv run python scripts/agentkit.py lint-maintainability
uv run python scripts/validate_component_map.py
uv run python scripts/check_doc_links.py
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
```

The repository test suite must also prove that every supported runtime, test,
schema, documentation, script, CI, and durable AgentKit path has an owner.
Representative routes should cover both a runtime leaf and each engineering
leaf. Global Vision and ADR changes must route to their reverse-impact
components. Component-map tests cover forbidden reverse-layer edges, split
candidates without an explicit owner edge, undeclared facade re-exports,
unknown internal modules, and unused exact import exceptions.

Run representative `orient` and `docs-impact --path` routes, including a
global Vision or ADR reverse-impact route, in addition to the focused
engineering routes. Link checking covers all tracked Markdown, including
`docs/engineering/`.

Verify concrete engineering-owner routes rather than a directory path that has
no component owner:

```sh
uv run python scripts/agentkit.py orient --path docs/engineering/testing-and-conformance/design.md
uv run python scripts/agentkit.py orient --path docs/engineering/schema-conformance/design.md
uv run python scripts/agentkit.py orient --path docs/engineering/repository-maintainability/design.md
uv run python scripts/agentkit.py orient --path docs/engineering/agentkit/design.md
uv run python scripts/agentkit.py orient --path docs/engineering/release/design.md
uv run python scripts/agentkit.py docs-impact --path docs/ARCHITECTURE.md
```

Launcher, clean-checkout, lifecycle-state, and deleted-path evidence is owned by
[AgentKit testing](../agentkit/testing.md). Package and optional-dependency
independence is owned by [release testing](../release/testing.md).

When a maintainability warning is resolved by extraction, add focused behavior
coverage for the new seam and include before/after metrics in the change
evidence. Pure native mapping tests cover native shape and fallback branches;
stdio/WebSocket lifecycle behavior remains covered by the App Server client
tests. Do not hide a growing responsibility behind a broad `src/**` catch-all
or an ignored path.
