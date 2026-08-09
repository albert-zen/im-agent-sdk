# AGENTS.md

IM Agent SDK is a thin semantic bridge between IM Channels and Agent
Applications. It is not an Agent runtime, transcript store, policy engine, or
general orchestrator.

Before changing behavior or boundaries, read the normative
[v1 design](docs/V1_DESIGN.md), its
[executable specification](docs/V1_EXECUTABLE_SPEC.md),
[docs/VISION.md](docs/VISION.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
any applicable accepted decision
in [docs/decisions/README.md](docs/decisions/README.md), and the affected
authority: runtime component docs under `docs/components/` or repository-support
docs under `docs/engineering/`. The human navigation map is
[docs/README.md](docs/README.md).

During the v1 transformation, pre-v1 code and documents are evidence rather
than constraints. When they conflict with the normative v1 design or ADR 0016,
change or remove them without adding a compatibility layer. Keep
[the repository audit and DAG](docs/V1_REPOSITORY_AUDIT.md) current as blocks
land.

Non-negotiable rules:

- Agent Applications own project, Thread, Turn, transcript, request, and
  execution truth.
- The SDK persists only IM bridge state and rebuildable projections.
- `Message` carries content; `Operation` carries control intent.
- Common semantics require evidence from two real integrations or a concrete
  counterexample, as defined by ADR 0006.
- Unsupported behavior fails explicitly; stable IDs, not text or timestamps,
  drive idempotency.
- Runtime semantics are designed in authoritative docs before implementation.

## AgentKit

<!-- agentkit:agents-section -->
Use `python scripts/agentkit.py`, never an assumed global `agentkit`. Start a
durable lifecycle for architecture, public contract, state-model,
cross-component, plugin/hook, or other substantial changes. Read-only work and
small self-contained low-risk edits may skip it; if scope expands, start before
continuing. Run `check` and `review-guidance`, complete clean-context review
when requested, then `close`. The full guide is
`plugins/agentkit/skills/agentkit/SKILL.md`.

## Verification

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests scripts
python scripts/validate_schemas.py
python scripts/check_doc_links.py
ruff check src tests scripts
ruff format --check src tests scripts
pyright src tests scripts
python scripts/agentkit.py check
```
