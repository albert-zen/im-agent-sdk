# Applications App Server adapter block

The App Server block is the Applications-owned protocol boundary for the
Codex and Zen native integrations. It translates bounded JSON-RPC traffic into
typed Application resources, events, requests, outcomes, and diagnostics.
The native App Server remains the authority for Thread, Turn, transcript,
request, and execution state; the SDK does not persist or replay the wire
stream.

## Leaves

| Leaf | Owns | Design | Testing |
|---|---|---|---|
| `client` | calls, target/supervisor, retry, dispatch lanes, response fence | [design](client/design.md) | [testing](client/testing.md) |
| `transport` | stdio/WebSocket framing and close errors | [design](transport/design.md) | [testing](transport/testing.md) |
| `mapping` | protocol classification and resource/item normalization | [design](mapping/design.md) | [testing](mapping/testing.md) |
| `requests` | request values, response wire mapping, pending/terminal lifecycle | [design](requests/design.md) | [testing](requests/testing.md) |
| `diagnostics` | bounded redacted connection/queue facts and summaries | [design](diagnostics/design.md) | [testing](diagnostics/testing.md) |

Codex and Zen are separate concrete owners outside this shared protocol
subtree: [Codex](../codex/design.md), [Zen](../zen/design.md). Sharing an App
Server transport does not make their native capabilities interchangeable.

## Boundaries

The transport receives and emits JSON values and translates closure; it does
not decide retry, resource identity, request policy, or recovery. The client
serializes JSON-RPC calls, admits notification and server-request callbacks
through independent finite lanes, attaches an immutable
`AppServerDispatchPosition` fence to admitted callbacks/responses, and resets
transport-bound handles on a new connection epoch. Mapping is stateless and
fails closed when required native identity is absent. Requests map only
evidenced Codex/Zen server requests and validate typed response shape before
native writeback. Diagnostics are read-only, bounded, and redacted.

No leaf owns Gateway request correlation, IM delivery, persistence, product
approval/command policy, raw native event exposure, durable spool/outbox,
checkpoint/replay state, or a second subscription. A missing authoritative
pending-request snapshot remains a live-only recovery limitation; a transport
reset stales request handles instead of fabricating recoverability.

## Current and target code

The current files are `src/imagent/applications/appserver_client/**`,
`appserver_mapping.py`, `appserver_requests.py`,
`appserver_request_runtime.py`, and the App Server portions of `appserver.py`,
plus the historical diagnostic provider files listed in the component map.
The target package is `src/imagent/applications/adapters/appserver/` with one
leaf owner per page. This documentation slice records the boundary; it does
not move or duplicate any implementation.
