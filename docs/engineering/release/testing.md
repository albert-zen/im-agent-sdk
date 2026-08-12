# Release testing

## Focused release mirror

Run the release suite through its canonical engineering owner:

```sh
PYTHONPATH=src uv run --no-sync python -m unittest tests.engineering.test_release -v
PYTHONPATH=src uv run --no-sync python -m unittest discover -s tests/engineering -p 'test_release.py' -v
```

The historical root test module is absent. These tests lock package metadata,
the finite value-only top-level facade, optional-dependency isolation, the
typed marker, console entry point, the single packaged reference consumer, and
the fail-closed GitHub-only release workflow.

## Build and installed-wheel evidence

```sh
uv sync --extra dev --extra channels --extra appserver --locked --no-install-project
uv build --wheel --build-constraint build-constraints.txt --require-hashes
uv run --no-sync python scripts/smoke_clean_install.py
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
PYTHONPATH=src uv run --no-sync python -m unittest discover -s tests -v
uv run --no-sync python -m compileall -q src tests scripts
uv run --no-sync python scripts/validate_schemas.py
uv run --no-sync python scripts/check_doc_links.py
uv run --no-sync ruff check src tests scripts
uv run --no-sync ruff format --check src tests scripts
uv run --no-sync pyright src tests scripts
python3 scripts/agentkit.py check
```

Record the built wheel SHA-256. A missing export, resurrected compatibility
module, unexpected optional import, unowned file, broken documentation link,
type error, or failed clean profile blocks the candidate. Fix the owning leaf;
do not add an import shim, duplicate model, service locator, consumer policy,
transcript store, or hidden runtime dependency.

## GitHub prerelease evidence

For the authorized tag `v0.1.0a1`, `.github/workflows/release.yml` repeats every
repository and clean-wheel gate from the tagged commit. It must prove the tag
commit is reachable from `main`, reject an existing release, validate the wheel
metadata and required packaged files, generate `SHA256SUMS` and source-bound
release notes, then create a GitHub prerelease with `--verify-tag`.

The focused release mirror rejects a workflow that omits tag/main/version
fencing, overwrite protection, checksum generation, prerelease marking, or the
explicit absence of a package-registry publish step. The workflow actions use
immutable full commit SHAs, checkout does not persist credentials, and the
dependency sync skips the local project while later commands disable automatic
sync; the wheel build is the only local backend execution and consumes the
hashed build constraint graph. Publication is verified
afterward by downloading the private asset through an authenticated GitHub
session, checking its digest, installing it in a fresh Python 3.13 environment,
and comparing `imagent.__version__` with the release version.
