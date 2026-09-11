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
| `applications.adapters.appserver.transport` | bounded stdio/WebSocket JSON framing, poison, and close translation | [design](appserver/transport/design.md) | [testing](appserver/transport/testing.md) |
| `applications.adapters.appserver.mapping` | protocol/resource/item normalization into internal typed facts | [design](appserver/mapping/design.md) | [testing](appserver/mapping/testing.md) |
| `applications.adapters.appserver.requests` | evidenced server-request mapping and bounded response runtime | [design](appserver/requests/design.md) | [testing](appserver/requests/testing.md) |
| `applications.adapters.appserver.diagnostics` | redacted bounded connection, queue, and protocol diagnostics | [design](appserver/diagnostics/design.md) | [testing](appserver/diagnostics/testing.md) |
| `applications.adapters.codex` | Codex native Application resources, input, events, history, and live facts | [design](codex/design.md) | [testing](codex/testing.md) |
| `applications.adapters.zen` | Zen native Application resources, input, events, history, and evidenced requests | [design](zen/design.md) | [testing](zen/testing.md) |
| `applications.adapters.deepseek-harness` | DeepSeek Harness Web Host workspaces/sessions, input, polling, events, and history | [design](deepseek-harness/design.md) | [testing](deepseek-harness/testing.md) |
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

Codex and Zen are fixed-workspace adapters. At construction they canonicalize
their configured execution root once and combine its fingerprint with the
configured immutable workspace ID. They answer Project list/get locally with
one stable projected `ProjectSummary`, reject Project management explicitly,
and reject foreign Project/Thread scopes before native I/O. T3 is managed: its
native Project list/get is authoritative, and a native Thread without
`projectId` fails closed.

All input and output positions are bounded typed values. Stable native IDs,
connection epochs, and explicit outcome/gap discriminants drive idempotency
and recovery. Raw native payloads stop inside the adapter normalization path;
Gateway sees only Applications contracts and canonical events.

## Native Thread-start option seam

An App Server adapter may receive an immutable, deployment-supplied native
Thread-start option mapping through its `thread_start_options` construction
argument. This is an adapter configuration seam for native sandbox/approval
defaults, not a common policy contract: the common `CreateThread` operation
keeps the shared control intent and is not widened with a native option
vocabulary. The adapter validates and deep-copies the configured default and
every per-call mapping before use: keys must be non-empty strings, the
adapter-owned `cwd` and the reserved native client
`params` field cannot be overridden, evidenced snake/camel aliases
(`approval_policy`/`approvalPolicy`, `approvals_reviewer`/
`approvalsReviewer`, `sandbox_policy`/`sandboxPolicy`, `service_name`/
`serviceName`, `thread_id`/`threadId`) normalize to one native field, and
aliases that collide after normalization fail before any native creation. The
native client receives a fresh deep copy of the validated mapping. The
mapping is neither persisted nor exposed as Agent state.

A consumer whose conversation UX selects among several native profiles may
call the concrete adapter's `create_thread_with_options` seam, then bind the
returned authoritative `ThreadSummary` through the ordinary Gateway
operation. This remains outside the common Application Port because the
option vocabulary is native and the selection policy belongs to the consumer;
adapters without evidenced native options expose no synthetic profile API.

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
T3 is co-located at `src/imagent/applications/adapters/t3.py` with its focused
owner tests. It remains a separate native adapter: its HTTP request/response
client has no synthetic long-lived connection epoch, and its bounded process
state recovers through authoritative native history rather than a local
transcript or spool.

The leaf pages above are the sole ownership and structural-gap records for this
block.
