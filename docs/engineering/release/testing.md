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

The profile matrix verifies that `appserver` supplies remote App Server
WebSocket support, each named Channel extra supplies only its protocol's native
dependencies, and `channels` remains their declared union. Clean import and
construction must succeed without `imcodex`, network connection, or real
credential validation; any installation-time I/O is a release-boundary
failure.

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

## Build-constraint rotation

`build-constraints.txt` pins the complete build closure of the one authorized
backend, `hatchling==1.32.0`, with SHA-256 hashes. Rotate it by resolving that
build closure with hashes via uv and replacing the file with the result:

```sh
printf 'hatchling==1.32.0\n' > build-constraints.in
uv pip compile build-constraints.in --generate-hashes --no-header --no-annotate \
  -o build-constraints.txt
rm build-constraints.in
```

A yanked or otherwise unavailable pinned build artifact is the only expected
maintenance trigger, and the response is this re-resolution plus the resulting
digest changes. Rotating the closure does not authorize a new backend version
by itself: the `pyproject.toml` `build-system.requires` pin, the release
mirror's exact-graph assertions, and a clean constrained rebuild must all move
together.

## GitHub prerelease evidence

For the authorized tag `v0.1.0a1`, `.github/workflows/release.yml` repeats
every post-test repository gate that `ci.yml` runs — including the three
AgentKit gates `./scripts/agentkit doctor`,
`./scripts/agentkit lint-architecture`, and `./scripts/agentkit check` — plus
the hash-constrained wheel build and the clean-install smoke, all from the
tagged commit. It must prove the tag commit is reachable from `main` using
only the checkout-fetched history, reject an existing release, validate the
wheel metadata and required packaged files, generate `SHA256SUMS` and
source-bound release notes, then create a GitHub prerelease with
`--verify-tag`.

The focused release mirror rejects a workflow that omits tag/main/version
fencing, overwrite protection, checksum generation, prerelease marking, or the
explicit absence of a package-registry publish step. The workflow actions use
immutable full commit SHAs, checkout does not persist credentials, and the
dependency sync skips the local project while later commands disable automatic
sync; the wheel build is the only local backend execution and consumes the
hashed build constraint graph. Because credential persistence is disabled, the
workflow performs no `git fetch` or `git push` at all: any later remote Git
operation would run unauthenticated against this private repository, so every
history proof must consume the refs the checkout already fetched. Immediately
before `gh release create`, the workflow uses the authenticated GitHub API to
resolve the fixed tag, handles both lightweight and bounded annotated tags,
and requires the peeled commit to equal the source `$GITHUB_SHA` exactly.
Focused static and behavior evidence locks that ordering and rejects a removed
or weakened comparison. Publication is verified afterward by downloading the
private asset through an authenticated GitHub session, checking its digest,
installing it in a fresh Python 3.13 environment, and comparing
`imagent.__version__` with the release version.

## License notice

A current wheel must report `License-Expression: MIT` and `License-File: LICENSE`
in METADATA and contain the exact root notice at
`im_agent_sdk-<version>.dist-info/licenses/LICENSE`. Check the archive bytes
against the tracked file. Historical released assets are not rewritten by a
repository license/visibility change.
