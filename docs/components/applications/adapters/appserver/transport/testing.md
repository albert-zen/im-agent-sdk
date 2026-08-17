# App Server transport testing

## Current evidence

`tests/applications/adapters/appserver/test_transport.py` is the authoritative
direct transport suite. It tests stdio/WebSocket send/receive framing, the one
configured inbound-frame byte bound, poisoned oversize failure, and
closed-stream translation. The broader client lifecycle evidence remains in
`tests/applications/adapters/appserver/test_client.py` and
`tests/applications/adapters/appserver/test_transport_lifecycle.py`; queue policy and connection reset behavior
remain owned by the client leaf.

The direct suite must prove:

- the default is finite and explicit constructor values accept only a positive
  non-boolean integer;
- stdio accepts an exact-limit payload and rejects limit-plus-one before JSON
  decoding for both one chunk and multiple chunks;
- several legal frames in one 64 KiB read remain independent, a valid frame
  immediately before an oversized trailing frame is still returned, and the
  next receive rejects without parsing any suffix;
- blank lines and EOF without a newline use the same payload-byte rule;
- rejection clears the stdio buffer, poisons that transport, and repeats one
  fixed error containing no content, endpoint, ID, actual size, or configured
  size;
- WebSocket text counts encoded UTF-8 bytes, binary counts raw bytes, and both
  exact-limit and limit-plus-one cases are decided before decode/JSON parse;
- valid send/framing and close translation remain unchanged.

Client/reconnect evidence must prove the identical configured value is used
for stdio, supplied/custom WebSocket wrappers, and default TCP/Unix WebSocket
`max_size`; an oversize `AppServerError` reaches no callback or client
dispatch, resets the failed connection once, and a later connection can start
normally with a new epoch. Queue, retry, and Application/Gateway recovery
semantics do not move into this suite.

## Target evidence and gates

The suite preserves existing behavior and keeps client dispatch/diagnostics
assertions in their sibling leaves. Run it with:

```sh
PYTHONPATH=src uv run python -m unittest tests.applications.adapters.appserver.test_transport -v
```

Run the full AGENTS verification, component-map and AgentKit checks, and the
clean-wheel smoke from the parent adapter block.

## Authority

- [Transport design](design.md)
- [Client design](../client/design.md)
