# Release design

## Purpose

Release support makes the Python package installable, typed, and reproducible
without changing the runtime ownership boundaries. It describes the public
package facade, build metadata, optional dependency groups, wheel contents, and
clean-install smoke.

## Ownership

This leaf owns:

- `pyproject.toml` package metadata and build configuration;
- the stable package/version facade and `py.typed` marker;
- optional dependency extras and their import boundary;
- wheel assembly and clean-install verification; and
- GitHub tag/release assembly, immutable asset evidence, and release-facing
  documentation for the installable artifact.

It does not own Interaction, Gateway, or Applications behavior, compatibility
implementations, a native runtime, a second package facade, or product
configuration. A public symbol has one implementation owner; a facade may
re-export it but must not retain a duplicate.

## Executable test owner

The complete package-independence and release-boundary test mirror lives in
`tests/engineering/test_release.py`. The historical root module
`tests/test_package_independence.py` is intentionally absent; it is not a
compatibility facade and must not be recreated as an import shim. The move
changes only the physical test owner and its repository-root calculation.

## Install boundary

The base package must import without optional native integrations. Optional
extras may add native transport dependencies, but the base artifact must not
silently import them or require a consumer package. The wheel contains the
typed SDK package and its marker; it does not contain transcripts, credentials,
bridge state, delivery jobs, or consumer configuration.

One distribution owns the exact protocol extras rather than separate adapter
packages:

- `appserver` adds WebSocket support for remote Codex App Server endpoints;
- `qq`, `telegram`, `feishu`, and `weixin` each add only that protocol's
  optional native dependencies; and
- `channels` is the union for deployments using all four Channel protocols.

Base Contracts, Ports, and Gateway imports require none of these extras.
Installing one relevant extra must allow its adapter to import and construct
in a clean environment with no `imcodex` package. Installation and import do
not connect to a network or perform real credential validation; those remain
explicit deployment smoke activities rather than package side effects.

The wheel also contains the one product-neutral executable acceptance consumer
under `examples/reference_consumer/`. It is installed so the base clean-wheel
case can run `python -m examples.reference_consumer.main` outside the source
tree. Inclusion does not make examples a second public SDK implementation:
their behavior is owned by the reference-consumer engineering leaf and every
SDK import resolves to the same installed public surfaces as downstream code.
No duplicate example, source-path fallback, or example-private copy of a
Gateway contract is packaged.

The exact same installed executable is run from a temporary working directory
in all six clean profiles (base, QQ, Telegram, Feishu, Weixin, and App Server).
An extra may add only its declared optional dependency; it cannot select a
different facade, example, lifecycle, or golden path.

The `imagent-send` console metadata resolves directly to the Interaction-owned
`imagent.interaction.client_tools.send:main` implementation. Packaging does
not own that behavior and must not preserve or synthesize the historical
`imagent.cli` package.

Release checks consume the three runtime layers' public exports and the
language-neutral schemas. They do not redefine those contracts. The historical
cross-layer `imagent.adapters`, `imagent.contracts`, `imagent.diagnostics`, and
`imagent.events` modules and package-root delivery module aliases are absent
from the wheel. Focused Interaction, Applications, Gateway routing,
persistence, delivery, and diagnostics owners are the only import surfaces for
those values.

The finite top-level `imagent` lazy resolver exposes only canonical values: the
`Gateway`, composition values, coherent store choices, projection policy,
closed outcomes, scoped actions, and Controller composition contracts by exact
owner identity. A cold `import imagent` loads neither Gateway nor optional native
dependencies. The separate finite `imagent.applications`
package-root lazy resolver follows the same non-compatibility rule for its own
bounded application exports. A cold `import imagent.applications` likewise
loads neither Gateway nor concrete optional dependencies.

## Version and publication boundary

The package version is build metadata, not runtime state. A release candidate
must be validated from the repository's locked dependency graph and an
isolated wheel installation.

The approved alpha publication boundary is the one GitHub prerelease
`v0.1.0a1`, whose package version is exactly `0.1.0a1` and whose commit is
reachable from `main`. The
release attaches exactly one universal wheel plus `SHA256SUMS`; its notes fix
the full source commit, package version, wheel filename, digest, and stable
asset URL. The repository-owned tag workflow refuses to overwrite an existing
release and never publishes to PyPI or another package registry. Immediately
before publication, the authenticated workflow resolves the fixed GitHub tag
reference, peels a bounded chain of annotated tags to its final commit, and
requires that commit to equal the workflow's original source commit exactly.
A missing, moved, over-nested, or non-commit tag fails closed without relying
on checkout-persisted Git credentials. The build backend and its transitive
graph are pinned with hashes in
`build-constraints.txt`; both CI and release assembly require those hashes.
Their initial dependency sync skips installing the local project, and later
repository commands run without automatic synchronization, so no unconstrained
local PEP 517 backend executes before the constrained wheel build.

Creating the tag remains the explicit publishing approval. A passing build or
pull request cannot infer that approval, publish a future version, announce a
release, modify a downstream repository, or approve consumer migration.

## v0.1.0a1 artifact provenance

The first `v0.1.0a1` artifact (SHA-256 prefix `123ebe9c`) was published
manually from a CRLF-contaminated working tree: every source member of the
published wheel carries CRLF terminators while the canonical repository
source is LF.
That artifact is therefore not reproducible from the pinned dependency and
build-constraint graph, and remains the existing historical alpha asset.
A workflow-built replacement
was planned, but has not been published. Repository opening does not authorize
moving the tag, deleting the release, or replacing its assets. Any replacement
still requires the recorded human approvals. A clean constrained rebuild of
the original source commit
produces SHA-256 prefix `e160c31d`, which is the reproducibility evidence for
the replacement artifact. The tag move and the release deletion remain explicit
human publishing approvals; recording this provenance does not authorize
either, and the replacement publication stays GitHub-only with no PyPI
publication.
