# App Server mapping testing

## Current evidence

`tests/test_appserver_mapping.py` (`AppServerMappingTests`) is the focused
normalization suite. It covers native containers, status aliases, unknown
shapes, Thread/Turn/item content, errors, and datetime normalization.
`tests/test_appserver_input.py` supplies affected adapter evidence for Thread
profiles, identity failures, local-image epochs, and Codex/Zen dispatch policy.

The tests currently cover many malformed shapes but do not establish general
text/collection bounds or strict failure for every missing event identity.
The target suite must add those cases. Unknown protocol methods must not become
supported Application operations, and raw `AppServerEvent` payloads must never
be exposed through Gateway.

## Target evidence and gates

The future mirrored suite is
`tests/applications/adapters/appserver/test_mapping.py`. Until the physical
move, run:

```sh
uv run python -m unittest tests.test_appserver_mapping -v
uv run python -m unittest tests.test_appserver_input -v
```

The full adapter block also runs unittest discovery, compile/schema/doc-link,
ruff/format/pyright, component-map, AgentKit, and clean-wheel verification.

## Authority

- [Mapping design](design.md)
- [App Server testing context](../../../../application-adapters/testing.md)
- [Application events design](../../../events/design.md)
