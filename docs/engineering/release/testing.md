# Release testing

## Focused release mirror

Run the complete release test mirror both through its module name and through
focused unittest discovery:

```sh
PYTHONPATH=src uv run python -m unittest tests.engineering.test_release -v
PYTHONPATH=src uv run python -m unittest discover -s tests/engineering -p 'test_release.py' -v
```

Both commands must discover the same complete suite. The historical
`tests/test_package_independence.py` path is absent, with no compatibility or
import shim; wheel build/install behavior, public exports, and dependency
boundary semantics remain unchanged.

## Build and install checks

Validate the package from a clean dependency-complete environment:

```sh
uv sync --extra dev --extra channels --extra appserver --locked
uv build --wheel
uv run python scripts/smoke_clean_install.py
```

The smoke script installs exactly one built wheel into six isolated environments:
the base package and the provider-specific optional-extra cases listed by the
script (`qq`, `telegram`, `feishu`, `weixin`, and `appserver`). All six cases
exercise the retained `imagent.channels` adapter facade and exact owner
identity; the base case additionally verifies the focused
`imagent.interaction.channels` contract facade and clean-process absence of
every retired `imagent.adapters`/`imagent.contracts` Channel name. It also
proves that only `ApplicationInputOutcomeUnknown` from the Applications block
remains on `imagent.contracts`, that the retired Application names fail from
both historical facades in multiple import orders, and that importing the
Applications root does not eagerly load concrete adapters or Gateway modules.
The base fingerprint separately starts from a cold top-level `import imagent`:
it locks the six finite root resolver names, verifies their exact canonical
module identities and package-root cache, rejects unknown names, and confirms
that the cold import has not loaded Gateway or optional native dependencies.
The base case also verifies the formal `imagent.interaction.controllers` and Application
event facade identities, runtime type hints, exact Codex/Zen adapter owner identities, and
clean-process absence/unimportability of `imagent.controllers` and the
historical shared App Server module. Every case checks expected public imports, native
dependency boundaries, and absence of the consumer package. A clean base
install must not discover optional integration dependencies that were not
requested. The aggregate `channels` extra and wheel contents such as
`py.typed` remain separate release checks; the smoke script does not claim to
cover them.
The base case also resolves `Gateway`, `GatewayLimits`, `GatewayStore`,
`MemoryGatewayStore`, `SQLiteGatewayStore`, `ProjectionPolicy`, and closed
outcomes from the installed top-level facade and proves their identity with
the canonical owners. It resolves `MissingBindingError` and
`StaleBindingError` through both installed Gateway facades, proves exact
identity with the input-dispatch owner and their stable classifications, and
proves that those Gateway-specific types do not escape through either the
top-level package or language-neutral contracts facade.
The same base environment executes the installed
`examples.reference_consumer.main` module from a temporary working directory
with no repository `PYTHONPATH`. The bounded success line is accepted only
after the complete public-path scenario has asserted its managed resource
identities, Conversation isolation, one-worker fan-out, diagnostics, and
shutdown invariants. That same installed executable also closes its first
Gateway/store, creates fresh runtime objects over the same SQLite database,
reconstructs bindings/routes/checkpoints/terminal receipts from bridge state,
recovers one authoritative missed output without duplicate delivery or native
redispatch, preserves public binding generations and completed idempotency,
suppresses a duplicate prior Channel identity, and validates the database and
all sidecars through one consistent WAL-aware read snapshot, the exact
`sqlite_schema` object/definition and column allowlist, bounded value-shape
validation, stable descriptor path identities, and encoded or fragmented
authority-data counterexamples.
The base case also proves, in the original transition-first clean process,
that `imagent.diagnostics` preserves exact identity for the canonical
Interaction, Channel, and Application diagnostics objects. It additionally
imports the canonical Gateway owner first and the transition facade first in
separate clean processes, proving exact Gateway identity in both orders. The
canonical Interaction and Application leaves remain free of Gateway imports.
The same base fingerprint proves that the permanent finite
`imagent.contracts:ConversationBinding` delegate is the exact Gateway
persistence owner value used by the Controller `get_binding` return hint, not
a compatibility copy.
Every one of the six cases also resolves the installed `imagent-send` console
entry point to `imagent.interaction.client_tools.send:main`, imports that
canonical owner without loading Gateway or Applications implementations, and
proves `imagent.cli` is physically absent.

## Required repository evidence

Before a release candidate is handed off, run:

```sh
PYTHONPATH=src uv run python -m unittest discover -s tests -v
uv run python -m compileall -q src tests scripts
uv run python scripts/validate_schemas.py
uv run python scripts/check_doc_links.py
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pyright src tests scripts
uv run python scripts/agentkit.py check
```

Confirm that the wheel contains no duplicate implementation hidden behind a
facade, that `imagent` imports without optional extras, and that `__version__`
and `py.typed` remain available from the documented public package. Check the
wheel in an isolated temporary environment rather than relying on the source
checkout's import path. The six clean-install cases must also prove that the
retired Channel names and the retired proactive vocabulary/four
submission-identity helpers are absent from their historical facades, while
`DeliverySubmissionOrigin` and all unrelated Application/Gateway names remain
available through their Gateway persistence/delivery owners, and that the focused Channel and Gateway facades
expose exact owner identities.

## Failure handling

A missing public export, malformed schema, unexpected optional import,
unowned file, broken local documentation link, or non-reproducible clean
install blocks the candidate. Do not repair a release failure by adding a
compatibility copy, global registry, hidden dependency, durable spool, or
product-specific package behavior. Fix the owning runtime or engineering leaf
and rerun the complete evidence set.
