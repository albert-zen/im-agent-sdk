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
| `diagnostics` | bounded redacted connection/queue facts plus internal summaries (not ADR-0014 facts) | [design](diagnostics/design.md) | [testing](diagnostics/testing.md) |

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

The transport leaf now lives at
`src/imagent/applications/adapters/appserver/transport.py`. The client leaf
now lives at `src/imagent/applications/adapters/appserver/client/**`; the
request leaf is co-located at
`src/imagent/applications/adapters/appserver/requests.py`. The shared Codex/Zen
implementation is split into
`src/imagent/applications/adapters/codex.py` and
`src/imagent/applications/adapters/zen.py`. Their private common base lives at
`src/imagent/applications/adapters/appserver/_base.py` as an explicitly mapped
two-owner split candidate; it is not an aggregate facade. The base owns typed
App Server input preparation, verified local-image epochs, the common typed
fence, `STARTED`/`CREATE_NEW` classification with no expected Turn ID,
native `turn/start`, and common event/resource normalization. Codex alone
owns steer enablement, active-Turn selection, `STEERED`/
`PRESERVE_EXISTING` classification with the active Turn ID, and native
`turn/steer`; Zen never inherits those positions.
The mapping leaf now lives at
`src/imagent/applications/adapters/appserver/mapping.py`; the remaining target
leaves are reserved for their own focused mechanical slices. App Server
diagnostic facts and internal summaries now live at
`src/imagent/applications/adapters/appserver/diagnostics.py`; the shared
`src/imagent/diagnostics.py` vocabulary remains in place. No aggregate package
facade is introduced.
