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
| `diagnostics` | bounded redacted connection/queue facts plus fixed `appserver.debug.v1` internal summaries | [design](diagnostics/design.md) | [testing](diagnostics/testing.md) |

Codex and Zen are separate concrete owners outside this shared protocol
subtree: [Codex](../codex/design.md), [Zen](../zen/design.md). Sharing an App
Server transport does not make their native capabilities interchangeable.

## Boundaries

The transport receives and emits JSON values, enforces the one configured
finite inbound-frame byte bound before decode, poisons oversized connections,
and translates closure; it does not decide retry, resource identity, request
policy, or recovery. The client
serializes JSON-RPC calls, admits notification and server-request callbacks
through independent finite lanes, attaches an immutable
`AppServerDispatchPosition` fence to admitted callbacks/responses, and resets
transport-bound handles on a new connection epoch. Mapping is stateless and
validates bounded native text, collections, keys, content aggregation, and
method-required stable identities before copying facts into the adapter path.
Aliases for one identity must agree exactly. It fails closed with fixed
redacted errors; a bad notification produces an explicit recovery gap and a
bad server request cannot open typed request state.
Requests map only evidenced Codex/Zen server requests and validate typed
response shape before native writeback. Diagnostics are read-only, bounded,
and redacted. Their internal debug path has one fixed `appserver.debug.v1`
vocabulary: it records only allowlisted categories, bounded type/count/length
facts, and documented SHA-256 fingerprints. It never logs native IDs, native
text, endpoints, paths, credentials, arbitrary payload keys, or arbitrary
payload values.

The fixed workspace Project is also an admission boundary. Before any native
Thread read, history/catch-up, input or control mutation, notification, or
server-request publication, the adapter authoritatively reads that native
Thread and requires its stable ID plus canonical `cwd` to match the configured
immutable workspace. Missing or foreign `cwd` evidence fails closed. Native
Thread listing filters those entries without discarding an otherwise valid
page, while native creation validates the returned Thread before exposing it.
This check does not claim native Project management or introduce persisted
workspace truth.

App Server may expose a newly created, scope-valid Thread before its native
turn-history resource exists. The shared adapter retains a finite typed set of
exact Thread creations, bound to the native connection epoch, stable session
identity, and a finite set of revisions authorized by create or allowlisted
non-Turn initialization notifications.
While one of those Threads has
produced neither an allowlisted turn-bearing native event nor crossed its first native-input dispatch fence, history and
catch-up return an empty typed baseline without calling the native turn-list
method. This is not error recovery: no provider error is inspected or
swallowed. Thread-only or unknown notifications do not prove history
materialization merely by carrying a Turn ID. The dispatch fence or an
allowlisted scoped turn-bearing event retires the evidence; stop, connection
reset/epoch change, native session or non-authorized revision change, same-ID
recreation, bounded
eviction, or adapter reconstruction also removes the exception and
therefore restores strict native history behavior. Checkpointed and all other
Threads always use the ordinary history path.

Codex's first-input continuation choice consumes the same exact evidence as a
no-active-Turn fact, so it does not request an unavailable turn-bearing Thread
read before materialization. The shared ordinary scope read still validates
the native Thread before dispatch.

History/catch-up captures eligible evidence before its scope read. If an
allowlisted Turn event arrives during that read, it retires future eligibility
but the captured baseline completes empty; subscription already exists, so the
live event remains queued behind the barrier. A turn-bearing event retires
evidence before its scope verification, so even a failed verification cannot
re-enable a synthetic empty result.

Evidence-validation reads and allowlisted non-Turn initialization revision
refreshes serialize only on that exact bounded evidence record. This prevents
benign initialization from racing a baseline or first-input scope read without
serializing native Turn-event retirement.

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
`src/imagent/applications/adapters/appserver/diagnostics.py`; common
connection/queue values are owned by `src/imagent/interaction/diagnostics.py`,
and Application diagnostic facts are owned by
`src/imagent/applications/diagnostics.py`. The historical cross-layer
`imagent.diagnostics` module is absent. No aggregate package facade is
introduced.
