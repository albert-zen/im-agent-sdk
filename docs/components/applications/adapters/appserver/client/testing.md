# App Server client testing

## Current evidence

The focused client evidence now lives in
`tests/applications/adapters/appserver/test_client.py`
(`AppServerClientTests`); the client/lifecycle portions of
`tests/test_appserver_transport.py` remain cross-component evidence. It covers:

- stdio and external WebSocket composition, target parsing, supervisor child
  lifecycle, and local-image capability verification;
- one transport-owned frame limit is validated by the factory/client and
  forwarded unchanged to stdio, supplied/custom WebSocket wrappers, and the
  default TCP/Unix WebSocket connector `max_size`;
- bounded request timeout/retry and explicit connection errors;
- notification and server-request lane capacity, reset, and handler failure;
- immutable dispatch-position ordering across both lanes and response fences;
- dispatch preserves an exact 64-key native `params` mapping for both lanes,
  rejects the 65th key in mapping, and keeps client connection context outside
  that native payload;
- connection-epoch reset, stale transport-bound calls, and reconnect behavior;
- oversized transport input reaches no response or callback dispatch, resets
  the poisoned connection, and permits only a fresh later connection/epoch;
- bounded diagnostic facts without endpoint/path/native-payload leakage.
- protocol, stderr, reconnect, supervisor, and health logging have one fixed
  bounded redacted `appserver.debug.v1` shape; arbitrary native or health
  values are never forwarded unchanged.

The test fixtures exercise the typed client and transport seams directly; they
do not stand up Gateway or introduce a second subscription.

## Target evidence

The target suite is
`tests/applications/adapters/appserver/test_client.py`. It preserves the
current assertions and adds no alternate client implementation. Transport
framing remains in the sibling transport suite. Client tests continue to
prove that a later response cannot widen its immutable admission fence, that
the fence resets with the connection epoch, and that an ambiguous native
mutation is not retry permission.

## Verification

Run the focused current suites with:

```sh
PYTHONPATH=src uv run python -m unittest tests.applications.adapters.appserver.test_client -v
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
