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

The repository is currently **design-first**. No implementation language or
wire encoding has been selected yet.

The initial review set is:

- [Vision](docs/VISION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Domain model](docs/DOMAIN_MODEL.md)
- [Messages, operations, and events](docs/PROTOCOL.md)
- [Adapter contracts](docs/ADAPTERS.md)
- [Roadmap and open decisions](docs/ROADMAP.md)

Implementation starts only after these boundaries are reviewed.
