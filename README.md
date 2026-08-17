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

## Install the alpha release

Version `0.1.0a1` is distributed as a GitHub prerelease, not through PyPI.
The published `v0.1.0a1` artifact will be the workflow-built replacement of
the initial manual publication (see the
[release design provenance note](docs/engineering/release/design.md)). Because
this repository is private, download requires an authenticated GitHub session
with repository read access:

```sh
gh release download v0.1.0a1 \
  --repo albert-zen/im-agent-sdk \
  --pattern "*.whl" \
  --pattern SHA256SUMS \
  --dir im-agent-sdk-0.1.0a1
cd im-agent-sdk-0.1.0a1
sha256sum --check SHA256SUMS
uv pip install im_agent_sdk-0.1.0a1-py3-none-any.whl
```

Verify the downloaded wheel against `SHA256SUMS` before installing it. The
stable authenticated asset URL is:

```text
https://github.com/albert-zen/im-agent-sdk/releases/download/v0.1.0a1/im_agent_sdk-0.1.0a1-py3-none-any.whl
```

A bare requirement such as `im-agent-sdk==0.1.0a1` does not discover GitHub
Release assets because GitHub Releases is not a Python package index. A
consumer must download the wheel or use the authenticated asset URL explicitly.

The first formal SDK is governed by the
[v1 architecture and consumer contract](docs/V1_DESIGN.md) and its
[executable specification](docs/V1_EXECUTABLE_SPEC.md). The
[repository audit](docs/V1_REPOSITORY_AUDIT.md) records which pre-v1 behavior
must be reused, rewritten, or removed.

## Status

The accepted design now has runnable vertical slices:

- SDK-owned QQ, Telegram, Feishu, and Weixin native adapters, transferred with
  pinned provenance and optional protocol extras;
- distinct Codex, Zen, and T3 Code application adapters;
- slash-command project/thread navigation, `/catchup`, `/history`, and normal
  Agent input;
- Markdown-first outbound messages and image attachment mapping;
- durable SQLite conversation bindings and delivery/inbound idempotency.
- scoped proactive text/artifact delivery through Gateway, with an
  Interaction-owned loopback-only reference `imagent-send` client and typed
  partial results.

Language-neutral JSON Schemas remain paired with the Python reference package
and adapter contract test kit.

Consumers host the optional proactive JSON handler inside their existing local
authenticated service; the SDK intentionally does not start another web
server. A product launcher can wrap:

```sh
imagent-send \
  --endpoint http://127.0.0.1:8080/deliver \
  --credential-file /private/run/delivery-token \
  --delivery-id task-123-result-1 \
  --application codex-main \
  --thread thread-123 \
  --text "Build complete" \
  --artifact dist/result.zip
```

The scoped caller supplies a Thread identity and never needs bot credentials,
Gateway database access, or a Channel-native Conversation ID.
The console entry point resolves directly to the stateless
`imagent.interaction.client_tools.send` owner; the historical `imagent.cli`
package is intentionally absent.

Start design/maintenance work from:

- [documentation map](docs/README.md);
- [Vision](docs/VISION.md);
- [Architecture](docs/ARCHITECTURE.md);
- [accepted decisions](docs/decisions/README.md);
- the affected runtime component under `docs/components/`; or
- the affected repository-support leaf under `docs/engineering/`.

## Development

Python 3.13 or newer and
[uv](https://docs.astral.sh/uv/getting-started/installation/) are required.
CI pins uv 0.12.0; use that version when regenerating `uv.lock`.

```sh
uv sync --extra dev --extra channels --extra appserver --locked --no-install-project
PYTHONPATH=src uv run --no-sync python -m unittest discover -s tests -v
uv run --no-sync python -m compileall -q src tests scripts
uv run --no-sync python scripts/validate_schemas.py
uv run --no-sync python scripts/check_doc_links.py
uv run --no-sync ruff check src tests scripts
uv run --no-sync ruff format --check src tests scripts
uv run --no-sync pyright src tests scripts
uv build --wheel --build-constraint build-constraints.txt --require-hashes
uv run --no-sync python scripts/smoke_clean_install.py
python scripts/agentkit.py check
```
