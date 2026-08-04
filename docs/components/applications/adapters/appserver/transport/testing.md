# App Server transport testing

## Current evidence

`tests/applications/adapters/appserver/test_transport.py` is the current
authoritative direct transport suite. It tests the stdio send/receive framing
and closed-stream translation. The broader client lifecycle evidence remains
in `tests/applications/adapters/appserver/test_client.py` and
`tests/test_appserver_transport.py`;
queue policy and connection reset behavior remain owned by the client leaf.

The tests assert decoded JSON values and typed closure; they do not inspect a
raw native event outside the App Server adapter path, persist wire frames, or
claim replay. They do not yet prove a finite inbound frame-size bound. The
target suite will cover oversized stdio and WebSocket frames and the fixed,
redacted rejection result only when that separate capacity gap is implemented.

## Target evidence and gates

The target suite must preserve the exact current behavior and keep client
dispatch/diagnostics assertions in their sibling leaves. Run it with:

```sh
PYTHONPATH=src uv run python -m unittest tests.applications.adapters.appserver.test_transport -v
```

Run the full AGENTS verification, component-map and AgentKit checks, and the
clean-wheel smoke from the parent adapter block.

## Authority

- [Transport design](design.md)
- [Client design](../client/design.md)
- [App Server testing context](../../../../application-adapters/testing.md)
