# App Server mapping testing

## Current evidence

`tests/applications/adapters/appserver/test_mapping.py`
(`AppServerMappingTests`) is the focused normalization suite. It covers native
containers, status aliases, unknown shapes, Thread/Turn/item content, errors,
and datetime normalization.
`tests/test_appserver_input.py` supplies affected adapter evidence for Thread
profiles, identity failures, local-image epochs, and Codex/Zen dispatch policy.

The tests currently cover many malformed shapes but do not establish general
text/collection bounds or strict failure for every missing event identity.
Those missing bounds and identity cases remain a later hardening slice.
Unknown protocol methods must not become supported Application operations, and
raw `AppServerEvent` payloads must never be exposed through Gateway.

## Target evidence and gates

Run the target pure-mapping suite and the affected adapter/input evidence with:

```sh
PYTHONPATH=src uv run python -m unittest tests.applications.adapters.appserver.test_mapping -v
uv run python -m unittest tests.test_appserver_input -v
```

The full adapter block also runs unittest discovery, compile/schema/doc-link,
ruff/format/pyright, component-map, AgentKit, and clean-wheel verification.

## Authority

- [Mapping design](design.md)
- [App Server testing context](../../../../application-adapters/testing.md)
- [Application events design](../../../events/design.md)
