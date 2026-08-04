# Repository maintainability testing

## Required evidence

Run the focused repository-maintainability mirror both through its module name
and through focused unittest discovery:

```sh
PYTHONPATH=src uv run python -m unittest tests.engineering.test_repository_maintainability -v
PYTHONPATH=src uv run python -m unittest discover -s tests/engineering -p 'test_repository_maintainability.py' -v
```

Both commands must discover the same complete test mirror. The focused module
must prove that the component-map and documentation-link test bodies remain
present, that exactly one engineering mirror owns them, and that the two
historical root paths are absent. Importing the new module must not recreate
either deleted path or alter the behavior of the scripts it exercises.
On this slice, each focused invocation reports 31 tests: 24 component-map
checks and 7 documentation-link checks.

The mirror also asserts that `tests/` has no direct `test_*.py` modules.
Every runtime test module belongs in one of the three layer mirrors; no
root-level compatibility shim may recreate the retired paths.

After a physical test move, compare the complete `unittest discover -s tests`
test-ID sets from latest main and the candidate branch. Normalize only the
eight documented root-module renames into their mirror paths; the normalized
comparison must have zero lost IDs. The three-layer mirror also keeps
`tests/gateway/routing/__init__.py` as an explicit package marker so standard
discovery reaches its canonical routing suites. For this layout, the only
allowed added IDs are the root-empty/layout assertion and the 34 existing
routing tests that the missing marker had left undiscovered: 8
`BindingOwnerTests`, 9 `BindingRuntimeTests`, 9
`GatewayOperationsOwnerTests`, 3 `ProjectionRouteAuthorityTests`, and 5
`ProjectionRouteContractOwnershipTests`.

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
