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

The accepted design now has runnable vertical slices:

- production QQ, Telegram, Feishu, and Weixin adapters reuse the pinned
  IMCodex implementations;
- distinct Codex, Zen, and T3 Code application adapters;
- slash-command project/thread navigation and normal Agent input;
- Markdown-first outbound messages and image attachment mapping;
- durable SQLite conversation bindings and delivery/inbound idempotency.

Language-neutral JSON Schemas remain paired with the Python reference package
and adapter contract test kit.

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

Python 3.13 or newer is required.

```sh
python -m pip install -e '.[dev,imcodex]'
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests
python scripts/validate_schemas.py
ruff check src tests scripts
pyright src tests scripts
```
