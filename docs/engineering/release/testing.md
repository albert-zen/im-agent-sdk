# Release testing

## Focused release mirror

Run the release suite through its canonical engineering owner:

```sh
PYTHONPATH=src uv run python -m unittest tests.engineering.test_release -v
PYTHONPATH=src uv run python -m unittest discover -s tests/engineering -p 'test_release.py' -v
```

The historical root test module is absent. These tests lock package metadata,
the finite value-only top-level facade, optional-dependency isolation, the
typed marker, console entry point, and the single packaged reference consumer.

## Build and installed-wheel evidence

```sh
uv sync --extra dev --extra channels --extra appserver --locked
uv build --wheel
uv run python scripts/smoke_clean_install.py
```

The smoke installs exactly one built wheel into six isolated environments:
base, QQ, Telegram, Feishu, Weixin, and App Server. Every profile runs the same
public fingerprint and the same installed executable from a temporary working
directory without repository `PYTHONPATH`.

The fingerprint must prove:

- exact owner identity for the finite top-level values and `imagent.gateway`
  facade;
- physical absence of `imagent.adapters`, `imagent.contracts`,
  `imagent.diagnostics`, `imagent.events`, and the top-level delivery aliases;
- focused Application, Interaction Channel, Gateway routing, persistence,
  delivery, and diagnostics imports;
- base-install absence of optional native dependencies and consumer packages;
- real public construction/validation of each optional Channel or App Server
  entry point when its extra is installed; and
- `GatewayRepositories`, `ImAgentGateway`, aggregate operations, diagnostics,
  and proactive runtime internals do not escape through the Gateway facade.

The installed reference consumer must print its one bounded golden success
line only after proving the Application → Project → Thread → Turn hierarchy,
Conversation isolation, one-worker fan-out, coherent Memory/SQLite store
behavior, restart reconstruction, terminal receipt/idempotency replay,
request/media/artifact/proactive invariants, bounded diagnostics, and complete
shutdown. No profile may substitute a source-tree or private-API scenario.

## Required repository gates

```sh
PYTHONPATH=src uv run python -m unittest discover -s tests -v
uv run python -m compileall -q src tests scripts
uv run python scripts/validate_schemas.py
uv run python scripts/check_doc_links.py
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pyright src tests scripts
python3 scripts/agentkit.py check
```

Record the built wheel SHA-256. A missing export, resurrected compatibility
module, unexpected optional import, unowned file, broken documentation link,
type error, or failed clean profile blocks the candidate. Fix the owning leaf;
do not add an import shim, duplicate model, service locator, consumer policy,
transcript store, or hidden runtime dependency.
