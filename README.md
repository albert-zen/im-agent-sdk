# IM Agent SDK

IM Agent SDK defines a common control and event model between IM channels and
coding-agent applications.

It standardizes five things:

- application, project, and thread resources;
- messages;
- operations such as creating, switching, deleting, and listing threads;
- unified Agent events;
- Channel and Agent application adapter capabilities.

```text
IM Channel
    ↓ ChannelAdapter
Message / Operation
    ↓
Gateway + ConversationBinding
    ↓ AgentApplicationAdapter
Zen / T3 Code / Codex / Claude Code / other Agent applications
    ↑
Unified Agent Events
```

The SDK is not an Agent runtime and does not own a second transcript. Each
Agent application remains authoritative for its projects, threads, history,
turns, approvals, and execution status.

## Status

The design baseline has been accepted and Milestone 1 is in progress.
Language-neutral JSON Schemas are paired with a dependency-free Python
reference package and contract test kit. No production Channel or Agent
application adapter is included yet.

The initial review set is:

- [Vision](docs/VISION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Domain model](docs/DOMAIN_MODEL.md)
- [Messages, operations, and events](docs/PROTOCOL.md)
- [Adapter contracts](docs/ADAPTERS.md)
- [Accepted decisions](docs/DECISIONS.md)
- [Reuse and provenance policy](docs/REUSE.md)
- [Roadmap and open decisions](docs/ROADMAP.md)

## Development

Python 3.11 or newer is required.

```sh
python -m pip install -e '.[dev]'
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests
python scripts/validate_schemas.py
ruff check src tests scripts
pyright src tests scripts
```
