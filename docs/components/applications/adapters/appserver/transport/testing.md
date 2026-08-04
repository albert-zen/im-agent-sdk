# App Server transport testing

## Current evidence

`tests/test_appserver_transport.py` is the current authoritative focused
suite. It tests stdio/WebSocket send and receive framing, malformed/closed
streams, close lifecycle, transport substitution, and the client-facing
connection reset behavior. Its lifecycle fixtures also cover the bounded
notification and server-request queues, but queue policy remains owned by the
client leaf.

The tests assert decoded JSON values and typed closure; they do not inspect a
raw native event outside the App Server adapter path, persist wire frames, or
claim replay. They do not yet prove a finite inbound frame-size bound. The
target suite must cover oversized stdio and WebSocket frames and the fixed,
redacted rejection result once that capacity gap is implemented.

## Target evidence and gates

The future mirrored path is
`tests/applications/adapters/appserver/test_transport.py`. It must preserve
the exact current behavior and keep client dispatch/diagnostics assertions in
their sibling leaves. Run the focused suite with:

```sh
uv run python -m unittest tests.test_appserver_transport -v
```

Before a physical move, run the full AGENTS verification, component-map and
AgentKit checks, and the clean-wheel smoke from the parent adapter block.

## Authority

- [Transport design](design.md)
- [Client design](../client/design.md)
- [App Server testing context](../../../../application-adapters/testing.md)
