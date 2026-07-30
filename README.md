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

- SDK-owned QQ, Telegram, Feishu, and Weixin native adapters, transferred with
  pinned provenance and optional protocol extras;
- distinct Codex, Zen, and T3 Code application adapters;
- slash-command project/thread navigation, `/catchup`, `/history`, and normal
  Agent input;
- Markdown-first outbound messages and image attachment mapping;
- durable SQLite conversation bindings and delivery/inbound idempotency.

Language-neutral JSON Schemas remain paired with the Python reference package
and adapter contract test kit.

Start design/maintenance work from:

- [documentation map](docs/README.md);
- [Vision](docs/VISION.md);
- [Architecture](docs/ARCHITECTURE.md);
- [accepted decisions](docs/decisions/README.md);
- the affected component under `docs/components/`.

## Development

Python 3.13 or newer and
[uv](https://docs.astral.sh/uv/getting-started/installation/) are required.
CI pins uv 0.12.0; use that version when regenerating `uv.lock`.

```sh
uv sync --extra dev --extra channels --extra appserver --locked
PYTHONPATH=src uv run python -m unittest discover -s tests -v
uv run python -m compileall -q src tests scripts
uv run python scripts/validate_schemas.py
uv run python scripts/check_doc_links.py
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pyright src tests scripts
uv build --wheel
uv run python scripts/smoke_clean_install.py
python scripts/agentkit.py check
```
