# App Server client testing

## Current evidence

The current client evidence remains in `tests/test_appserver_client.py`
(`AppServerClientTests`) and the client/lifecycle portions of
`tests/test_appserver_transport.py`. It covers:

- stdio and external WebSocket composition, target parsing, supervisor child
  lifecycle, and local-image capability verification;
- bounded request timeout/retry and explicit connection errors;
- notification and server-request lane capacity, reset, and handler failure;
- immutable dispatch-position ordering across both lanes and response fences;
- connection-epoch reset, stale transport-bound calls, and reconnect behavior;
- bounded diagnostic facts without endpoint/path/native-payload leakage.

The test fixtures exercise the typed client and transport seams directly; they
do not stand up Gateway or introduce a second subscription.

## Target evidence

The future mirrored suite is
`tests/applications/adapters/appserver/test_client.py`. It must preserve
the current assertions and add no alternate client implementation. Transport
framing remains in the sibling transport suite. Client tests must continue to
prove that a later response cannot widen its immutable admission fence, that
the fence resets with the connection epoch, and that an ambiguous native
mutation is not retry permission.

## Verification

Run the focused current suites with:

```sh
uv run python -m unittest tests.test_appserver_client -v
uv run python -m unittest tests.test_appserver_transport -v
```

The full Applications adapter block also requires the repository unittest,
compile, schema, documentation-link, ruff, format, pyright, component-map,
AgentKit, and clean-wheel checks recorded by the parent navigation and
`AGENTS.md`.

## Authority

- [Client design](design.md)
- [App Server block](../README.md)
- [Cross-adapter testing context](../../../../application-adapters/testing.md)
