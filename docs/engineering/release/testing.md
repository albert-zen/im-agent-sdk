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
every retired `imagent.adapters`/`imagent.contracts` Channel name. The base
case also verifies the formal `imagent.interaction.controllers` facade
identities, runtime type hints, exact Codex/Zen adapter owner identities, and
clean-process absence/unimportability of `imagent.controllers` and the
historical shared App Server module. Every case checks expected public imports, native
dependency boundaries, and absence of the consumer package. A clean base
install must not discover optional integration dependencies that were not
requested. The aggregate `channels` extra and wheel contents such as
`py.typed` remain separate release checks; the smoke script does not claim to
cover them.

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
`DeliverySubmissionOrigin` and all unrelated Application/Gateway/passive-state
names remain available, and that the focused Channel and Gateway facades
expose exact owner identities.

## Failure handling

A missing public export, malformed schema, unexpected optional import,
unowned file, broken local documentation link, or non-reproducible clean
install blocks the candidate. Do not repair a release failure by adding a
compatibility copy, global registry, hidden dependency, durable spool, or
product-specific package behavior. Fix the owning runtime or engineering leaf
and rerun the complete evidence set.
