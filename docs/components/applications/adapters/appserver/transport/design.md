# App Server transport design

Component ID: `applications.adapters.appserver.transport`

Parent: `applications.adapters.appserver`

## Purpose and ownership

This leaf owns only JSON framing over an SDK-managed stdio process or an
externally supplied WebSocket, including close detection and translation to a
typed `AppServerError`. `AppServerTransport` is the narrow async transport
protocol; `StdioAppServerTransport` and `WebSocketAppServerTransport` are its
current concrete implementations.

It does not own JSON-RPC IDs or dispatch, notification/server-request lanes,
retry/reconnect policy, connection epochs, resource mapping, request mapping,
diagnostics policy, Gateway recovery, or product configuration. It neither
persists frames nor claims replay/cursor semantics.

## Typed boundary and public facade

Inputs are JSON mappings and a configured process/WebSocket endpoint. Outputs
are decoded JSON mappings, successful close completion, or `AppServerError`.
The target formal export owned here is `AppServerTransport` from
`imagent.applications.adapters.appserver.transport`; concrete transport classes
remain implementation positions. `AppServerError` is defined by this transport
module and re-exported by the historical client facade; that exact object
remains stable without creating a second exception hierarchy.

## Dependencies, state, and recovery

The transport uses only standard-library framing/introspection plus the
provided endpoint object. Each instance represents one connection and has no
replay log, native cursor, or Application checkpoint. Closure is surfaced to
the client, which owns epoch reset, pending-call invalidation, bounded queues,
retry, and observation-gap translation. The current stdio implementation
accumulates bytes until a newline without enforcing a frame-size limit, and an
externally supplied WebSocket is not size-fenced by this layer.

## Current, target, and structural gap

The implementation now lives at
`src/imagent/applications/adapters/appserver/transport.py`, preserving the
exact protocol and concrete behavior; the historical transport module is
removed. Direct framing/closure evidence now lives in
`tests/applications/adapters/appserver/test_transport.py`. The transport still
requires a separate explicit finite frame-size limit for both stdio and
supplied WebSocket receives, fixed oversize failure semantics, and focused
tests proving that excess input is rejected without unbounded buffering or
leaking native content. Those capacity/security changes are outside this
mechanical move.

## Authority

- [App Server block](../README.md)
- [Applications adapter overview](../../../../application-adapters/design.md)
- [ADR 0004](../../../../../decisions/0004-event-fanout-and-recovery.md)
