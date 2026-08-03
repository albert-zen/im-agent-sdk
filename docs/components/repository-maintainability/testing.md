# Repository maintainability testing

Required evidence:

- `python scripts/agentkit.py doctor`;
- `python scripts/agentkit.py check`;
- `python scripts/agentkit.py lint-architecture`;
- `python scripts/validate_component_map.py`, proving every internal Python
  import resolves through mapped ownership and an explicit direct dependency;
- `python scripts/agentkit.py lint-maintainability` with zero warnings;
- `python scripts/check_doc_links.py`;
- representative `orient` and `docs-impact --path` routes;
- global Vision/ADR reverse-impact routes;
- link checking for tracked Markdown;
- `git check-ignore .agentkit/...`;
- clean temporary checkout launcher smoke on the available OS;
- unit test proving every supported runtime, test, schema, documentation,
  script, CI, and durable AgentKit path has an owner, with precise
  representative route assertions and only narrow documented shared-path
  exceptions.

Component-map tests must include forbidden reverse-layer edges, split
candidates without an owner/dependency pairing, undeclared facade re-exports,
unknown internal modules, and unused exact current-import exceptions. Both
AgentKit architecture commands must invoke the validator in an isolated base
environment before delegating to AgentKit; `agentkit.yml` must not restore a
parallel import graph.

When a warning is resolved by extraction, focused behavior suites must cover
the new seam. Direct App Server mapping tests cover native shape and fallback
branches, while stdio/WebSocket lifecycle behavior remains covered by App
Server client tests.

The portable launcher is `scripts/agentkit.py`, pinned to one AgentKit commit.
`scripts/agentkit` and `scripts/agentkit.cmd` are thin POSIX and Windows
entrypoints. None may depend on a sibling checkout or global `agentkit`.

Runtime `.agentkit/` contents must remain ignored. Durable configuration is
limited to `agentkit.yml`, `.agents/`, `plugins/agentkit/`, the launcher, and
repository docs.

Until AgentKit
[#5](https://github.com/albert-zen/AgentKit/issues/5) is fixed, deleted paths
reported as unmapped must be checked against the base manifest and replacement
mapping during review. Do not retain stale manifest entries or compatibility
files merely to suppress that warning. The durable inventory test remains the
authority for the post-change tree.
