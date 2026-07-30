# Repository maintainability testing

Required evidence:

- `python scripts/agentkit.py doctor`;
- `python scripts/agentkit.py check`;
- `python scripts/agentkit.py lint-architecture`;
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

The portable launcher is `scripts/agentkit.py`, pinned to one AgentKit commit.
`scripts/agentkit` and `scripts/agentkit.cmd` are thin POSIX and Windows
entrypoints. None may depend on a sibling checkout or global `agentkit`.

Runtime `.agentkit/` contents must remain ignored. Durable configuration is
limited to `agentkit.yml`, `.agents/`, `plugins/agentkit/`, the launcher, and
repository docs.
