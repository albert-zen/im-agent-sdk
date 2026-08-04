# App Server transport design

Component ID: `applications.adapters.appserver.transport`

Parent: `applications.adapters.appserver`

## Purpose and ownership

This leaf owns JSON framing over an SDK-managed stdio process or an externally
supplied WebSocket, including one configured inbound-frame byte bound, close
detection, and translation to a typed `AppServerError`. `AppServerTransport`
is the narrow async transport protocol; `StdioAppServerTransport` and
`WebSocketAppServerTransport` are its current concrete implementations.

It does not own JSON-RPC IDs or dispatch, notification/server-request lanes,
retry/reconnect policy, connection epochs, resource mapping, request mapping,
diagnostics policy, Gateway recovery, or product configuration. It neither
persists frames nor claims replay/cursor semantics.

## Typed boundary and public facade

Inputs are JSON mappings, a configured process/WebSocket endpoint, and one
positive non-boolean `max_inbound_frame_bytes` value. The finite default is 64
MiB so complete native Thread/resume responses remain compatible while every
connection has an explicit bound. Outputs are decoded JSON mappings,
successful close completion, or `AppServerError`.
The target formal export owned here is `AppServerTransport` from
`imagent.applications.adapters.appserver.transport`; concrete transport classes
remain implementation positions. `AppServerError` is defined by this transport
module and re-exported by the historical client facade; that exact object
remains stable without creating a second exception hierarchy.

## Dependencies, state, and recovery

The transport uses only standard-library framing/introspection plus the
provided endpoint object. Each instance represents one connection and has no
replay log, native cursor, or Application checkpoint. Closure and oversize
failure are surfaced to the client, which owns epoch reset, pending-call
invalidation, bounded queues, retry, and observation-gap translation.

For stdio JSONL, the bound applies to the current frame payload bytes and does
not include the newline delimiter. The reader always examines the earliest
newline first, so one 64 KiB read containing several individually valid frames
is not rejected merely because the total buffered bytes exceed one frame's
limit. Exact-limit payloads are valid. The same per-frame rule applies across
multiple chunks, to blank lines before they are skipped, to a trailing frame
already buffered after a valid predecessor, and to a final EOF payload without
a newline.

For WebSocket text, the bound applies to the UTF-8 encoding; for binary
messages it applies directly to the received bytes. Both checks precede UTF-8
decoding and JSON parsing. The SDK's default TCP and Unix WebSocket connectors
also receive this same value as their native `max_size`; a custom or supplied
WebSocket remains independently rechecked by the transport rather than
becoming a second trust path.

An oversized frame raises the fixed redacted `AppServerError` message
`app-server inbound frame exceeds configured byte limit`. It contains no
native content, endpoint, ID, actual length, or configured length. The
transport clears its stdio buffer and becomes poisoned before raising; later
send or receive attempts on that instance raise the same failure and cannot
parse a suffix. The client handles that ordinary transport failure through its
existing connection reset/recovery path.

## Current, target, and structural gap

The implementation lives at
`src/imagent/applications/adapters/appserver/transport.py`; the historical
transport module is removed. Direct framing, capacity, poison, and closure
evidence lives in `tests/applications/adapters/appserver/test_transport.py`.
There is one inbound-frame limit and no alternate decoder, durable frame
buffer, or product-owned framing path.

## Authority

- [App Server block](../README.md)
- [Applications adapter overview](../../../../application-adapters/design.md)
- [ADR 0004](../../../../../decisions/0004-event-fanout-and-recovery.md)
