# Quickstart: one inbound -> Agent -> outbound round trip

This 10-20 minute path runs the SDK's one packaged neutral consumer. It admits
ordinary Channel input, explicitly creates and binds an Application Project
and Thread, dispatches the input to the Application, and projects the
authoritative response back through the Channel. The executable imports only
public SDK surfaces and needs no product credentials or native service.

## 1. Download and verify v0.1.0a1

Python 3.13 or newer and the GitHub CLI are required. While repository access
is restricted, `gh auth status` must show an account with repository read
access. The existing alpha is the historical `a72b24a` artifact, not current
`main`; see [release provenance](../engineering/release/design.md#v010a1-artifact-provenance).
For a current-source build, follow the [development commands](../../README.md#development).
From an empty working directory:

```powershell
py -3.13 -m venv .venv
New-Item -ItemType Directory -Force .quickstart-download | Out-Null
gh release download v0.1.0a1 --repo albert-zen/im-agent-sdk --pattern '*.whl' --pattern SHA256SUMS --dir .quickstart-download
$expected = (Get-Content .quickstart-download/SHA256SUMS).Split()[0]
$actual = (Get-FileHash .quickstart-download/im_agent_sdk-0.1.0a1-py3-none-any.whl -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw 'GitHub Release wheel checksum mismatch' }
.venv\Scripts\python -m pip install .quickstart-download/im_agent_sdk-0.1.0a1-py3-none-any.whl
```

On macOS or Linux, use `.venv/bin/python` and verify the downloaded files from
their directory:

```sh
python3.13 -m venv .venv
mkdir -p .quickstart-download
gh release download v0.1.0a1 --repo albert-zen/im-agent-sdk --pattern '*.whl' --pattern SHA256SUMS --dir .quickstart-download
(cd .quickstart-download && shasum -a 256 -c SHA256SUMS)
.venv/bin/python -m pip install .quickstart-download/im_agent_sdk-0.1.0a1-py3-none-any.whl
```

These commands install the checksummed
[GitHub Release](https://github.com/albert-zen/im-agent-sdk/releases/tag/v0.1.0a1),
not PyPI or an absolute machine-local wheel.

## 2. Run the installed vertical on POSIX

The complete reference consumer currently requires POSIX descriptor flags
(`O_NOFOLLOW` and `O_DIRECTORY`) for its consumer-owned artifact ledger. Run
the vertical on Linux or macOS from the same working directory:

```sh
.venv/bin/python -m examples.reference_consumer.main
```

A successful run prints exactly one bounded line:

```text
reference consumer OK: projects=1 threads=2 conversations=2 max_workers=1 diagnostics=bounded sqlite_recovery=true shutdown=true
```

Windows can download, verify, install, and import the wheel, but it cannot run
this canonical vertical yet because Python on Windows does not expose those
descriptor flags. The consumer fails closed rather than weakening its
filesystem trust boundary. Use WSL or another Linux/macOS environment for this
step; do not treat an import-only Windows smoke as inbound -> Agent -> outbound
evidence.

This is the packaged release artifact, executed outside the SDK checkout with
no repository `PYTHONPATH`. It constructs one explicit managed-CWD
Application, Channel, frozen local command registry, `SQLiteGatewayStore`, and
`Gateway`. The Gateway async context owns startup, admission, observation,
lease renewal, and joined shutdown.

The consumer uses scoped `create_and_select_project` and
`create_and_bind_thread` actions before sending ordinary input. Gateway never
chooses a workspace or creates resources implicitly. Its
`foreground_only` binding is output authority for the current Conversation;
binding, native activation, and other observation policies remain separate.

The SQLite store contains only bridge bindings, routes, checkpoints,
idempotency, and minimal effect receipts. The Application remains authoritative
for Projects, Threads, Turns, requests, execution, and transcript. The run
closes its first object graph and reconstructs fresh Gateway, Channel, and store
objects to prove that bridge projections recover without turning SQLite into a
second Agent history.

## 3. Replace the neutral seams

In a product, replace the deterministic Channel and Application with native
adapters while keeping the same explicit composition and ownership. Your
consumer supplies credentials, IM access policy, workspace selection, product
commands, failure presentation, and operational policy.

The canonical implementation is
[`examples/reference_consumer`](../../examples/reference_consumer/). For the
details needed during replacement, continue with the focused
[Applications](applications.md), [Interaction](interaction.md),
[Gateway](gateway.md), and [production checklist](production-checklist.md)
guides rather than copying the acceptance consumer. Runtime ownership remains
authoritative in the [v1 design](../V1_DESIGN.md) and
[architecture](../ARCHITECTURE.md).
