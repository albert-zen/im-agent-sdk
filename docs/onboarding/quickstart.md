# Quickstart: one inbound → Agent → outbound round trip

This 10–20 minute quickstart runs one production-shaped SDK composition: a
Channel admits an inbound message, a bound Agent Application accepts it, and
the Application's authoritative event is projected back through the Channel.
It uses only public SDK imports.

The local Channel and echo Application make the path runnable without product
credentials. They are seams to replace, not hidden runtimes: the Application
owns Project, Thread, Turn, and transcript truth; the SDK owns only IM bridge
state and rebuildable projections.

## 1. Create an environment and install the release

Python 3.13 or newer is required. From a checkout of this repository:

```powershell
py -3.13 -m venv .venv
New-Item -ItemType Directory -Force .quickstart-download | Out-Null
gh release download v0.1.0a1 --repo albert-zen/im-agent-sdk --pattern '*.whl' --pattern SHA256SUMS --dir .quickstart-download
$expected = (Get-Content .quickstart-download/SHA256SUMS).Split()[0]
$actual = (Get-FileHash .quickstart-download/im_agent_sdk-0.1.0a1-py3-none-any.whl -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw 'GitHub Release wheel checksum mismatch' }
.venv\Scripts\python -m pip install .quickstart-download/im_agent_sdk-0.1.0a1-py3-none-any.whl
```

The repository is private, so `gh auth status` must show an authenticated
GitHub CLI session with repository read access. On macOS or Linux, use
`mkdir -p .quickstart-download`, `sha256sum -c .quickstart-download/SHA256SUMS`,
and `.venv/bin/python` for the equivalent lines. This deliberately downloads the
immutable [GitHub Release](https://github.com/albert-zen/im-agent-sdk/releases/tag/v0.1.0a1)
asset, then installs it by a repository-relative path—not from PyPI or an
absolute machine-local wheel.

## 2. Run the vertical

Choose both ownership locations explicitly. `workspace` is execution context
owned by the demo Application; `state` contains the SDK-owned SQLite bridge
database.

```powershell
New-Item -ItemType Directory -Force workspace, state | Out-Null
.venv\Scripts\python -m examples.quickstart.main --workspace workspace --state-dir state
```

macOS/Linux users can run the equivalent `mkdir -p workspace state` and use
`.venv/bin/python`. The result is:

```text
Echo: hello from IM
bridge state: <your checkout>/state/bridge.sqlite3
Agent transcript/state owner: LocalEchoApplication (process-local demo)
```

The executable composition is
[`examples/quickstart/main.py`](../../examples/quickstart/main.py); its two
small public-port implementations are in
[`examples/quickstart/adapters.py`](../../examples/quickstart/adapters.py).
The important choices are visible in that code:

- `SQLiteGatewayStore` is constructed by the consumer and passed to exactly
  one `Gateway` namespace. It stores bindings, routes, checkpoints, and
  idempotency/effect receipts—not messages or Agent execution truth.
- `async with gateway` owns startup, lease renewal, admission, observation,
  and joined shutdown. There is no second runtime.
- `create_and_select_project` and `create_and_bind_thread` explicitly establish
  the hierarchical binding before ordinary input. An unbound message would
  fail; Gateway never chooses a workspace or creates resources implicitly.
- `ProjectionPolicy.FOREGROUND_ONLY` makes the current Thread binding the
  output authority for this Conversation. Binding, native activation, and
  other observation policies remain separate concepts.

The demo Application is intentionally process-local. The completed SQLite
database remains inspectable, but rerunning with it and a fresh demo
Application fails explicitly because the SDK did not persist the Application's
resources or transcript. A production Application adapter reconnects to its
own durable native authority; it does not move that truth into Gateway.

## 3. Replace the local seams

Keep the composition root and replace `LocalChannel` with a real authenticated
Channel adapter and `LocalEchoApplication` with a native Agent Application
adapter. Supply credentials, access rules, workspace selection, product
commands, failure presentation, and operational policy in your consumer.

For the complete contracts and restart/fan-out/request/media evidence, follow
the [reference consumer](README.md), especially the focused
[Applications](applications.md), [Interaction](interaction.md),
[Gateway](gateway.md), and [production checklist](production-checklist.md)
guides. Runtime ownership remains authoritative in the
[v1 design](../V1_DESIGN.md) and [architecture](../ARCHITECTURE.md).
