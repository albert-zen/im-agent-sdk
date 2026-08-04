# Applications adapter component block

This subtree owns the concrete Application-side translation between an
external Agent Application and the typed Applications boundary. The external
Application remains authoritative for native Projects, Threads, Turns,
transcript/history, requests, and execution. These adapters normalize native
facts, own native dispatch classification, and expose only the finite typed
Application contract to Gateway and Controllers.

An adapter never imports Gateway implementation, exposes a raw native event,
creates a second Agent runtime/subscription/transcript, or turns a live-only
observation into recoverable history. Unsupported native capabilities fail
explicitly. Product commands, route correlation, persistence, presentation
destination policy, and consumer materialization remain outside this block.

## Leaves

| Leaf | Responsibility | Design | Testing |
|---|---|---|---|
| `applications.adapters.appserver.client` | bounded JSON-RPC client, targets, retry, supervision, and admission fence | [design](appserver/client/design.md) | [testing](appserver/client/testing.md) |
| `applications.adapters.appserver.transport` | stdio/WebSocket JSON framing and close translation | [design](appserver/transport/design.md) | [testing](appserver/transport/testing.md) |
| `applications.adapters.appserver.mapping` | protocol/resource/item normalization into internal typed facts | [design](appserver/mapping/design.md) | [testing](appserver/mapping/testing.md) |
| `applications.adapters.appserver.requests` | evidenced server-request mapping and bounded response runtime | [design](appserver/requests/design.md) | [testing](appserver/requests/testing.md) |
| `applications.adapters.appserver.diagnostics` | redacted bounded connection, queue, and protocol diagnostics | [design](appserver/diagnostics/design.md) | [testing](appserver/diagnostics/testing.md) |
| `applications.adapters.codex` | Codex native Application resources, input, events, history, and live facts | [design](codex/design.md) | [testing](codex/testing.md) |
| `applications.adapters.zen` | Zen native Application resources, input, events, history, and evidenced requests | [design](zen/design.md) | [testing](zen/testing.md) |
| `applications.adapters.t3` | T3 HTTP resources, dispatch, polling, events, and history | [design](t3/design.md) | [testing](t3/testing.md) |

## Fixed ownership and dependency order

The App Server leaves form one boundary, not a generic plugin pipeline:

```text
transport -> client -> mapping / requests / diagnostics
                         \
                          -> Codex or Zen concrete adapter
```

`transport` knows only framing and connection closure. `client` owns bounded
JSON-RPC calls, connection epochs, independent notification/server-request
lanes, retry/supervision, and the immutable callback-admission fence.
`mapping` owns stateless native shape normalization. `requests` owns only the
evidenced interactive request/response surface and its pending-handle
lifecycle. `diagnostics` owns fixed redacted facts and summaries. Codex and Zen
consume those shared positions but remain distinct native adapters: Codex may
use its evidenced steer/live-activity positions; Zen does not inherit them by
sharing transport.

T3 is a separate native adapter. Its HTTP request/response client has no
synthetic long-lived connection epoch. Its one finite polling flow and
authoritative native history determine recovery; interactive requests remain
unsupported until native evidence exists.

All input and output positions are bounded typed values. Stable native IDs,
connection epochs, and explicit outcome/gap discriminants drive idempotency
and recovery. Raw native payloads stop inside the adapter normalization path;
Gateway sees only Applications contracts and canonical events.

## Current and target layout

Issue #240 established this documentation authority before mechanical moves.
The App Server transport, client, mapping, request, and diagnostics leaves are
physically at their target paths. Codex and Zen have separate concrete modules
with one explicitly mapped private two-owner base under the App Server subtree;
no aggregate `imagent.applications.adapters.appserver` facade is introduced.
The private base shares typed start/input preparation, the common
`STARTED`/`CREATE_NEW` fence with no expected Turn ID, and common native
normalization; Codex owns steer selection, active-Turn discovery,
`STEERED`/`PRESERVE_EXISTING` classification with the active Turn ID, and
`turn/steer`, while Zen is start-only.
T3 remains reserved for its later one-issue/one-PR mechanical slice.

The cross-adapter overview remains available as [transition design
context](../../application-adapters/design.md) and [cross-adapter testing
context](../../application-adapters/testing.md); the leaf pages above are the
authoritative ownership and structural-gap records for this block.
