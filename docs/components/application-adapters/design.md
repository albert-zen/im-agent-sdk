# Agent Application adapters component design

## Purpose

An Agent Application adapter translates one configured native control endpoint
into the common Application Port while leaving resource and execution truth in
the native Application.

## Ownership

Application adapters own:

- native authentication/connection/client lifecycle;
- native project mode and resource mapping;
- typed Application operation translation;
- input encoding and stable client-message ID round-trip;
- truthful pre-dispatch versus dispatched/unknown input outcomes;
- native event-to-`AgentEvent` mapping and fan-out publication;
- authoritative history/catch-up reads;
- explicit request, interruption, activation, attachment, replay, and ordering
  capabilities.

They do not own:

- native Projects, Threads, transcript, Turns, requests, execution, or
  retention;
- Conversation binding or Thread projection route;
- IM delivery, Markdown, bot credentials, or native Conversation IDs;
- common permission/product policy;
- a synthetic event journal or replay sequence.

## Resource and operation surface

Each adapter exposes a summary, `start`, `stop`, typed `execute`, `send_input`,
and fan-out-safe `subscribe_thread`. `send_input` accepts the SDK's default
`prefer_active_turn` preference and returns the actual `started` or `steered`
disposition plus its correlation policy. An adapter without evidenced native
continuation returns `started`; it does not imitate another protocol.

An adapter may report an ordinary failure only while it knows native input was
not dispatched. After dispatch, an absent acceptance response is an
`ApplicationInputOutcomeUnknown` unless the native API proves retries
idempotent. This is transport outcome classification, not bridge-owned Turn
truth.

The common continuation preference is an SDK input rule; the native mechanism
remains adapter-specific. Codex `turn/steer` is an optional native capability
that its concrete adapter enables by default and deployments may disable. A
discovery read is not authority over a later mutation: the adapter must accept
the native mutation response as the result, preserve an ambiguous outcome,
and never mask a native rejection or automatically start a second Turn after a
read/mutation race.

After local validation and native candidate discovery, every adapter calls the
typed pre-dispatch hook exactly once immediately before mutation. A planned
start declares `create_new`; a planned steer declares `preserve_existing` with
the expected Turn ID. No adapter may dispatch when that hook rejects. The
returned result must match the authorized plan; native replacement after a
steer is accepted native truth but an explicit post-acceptance bridge
degradation, never permission to retarget or retry.

Managed Applications expose real Projects. Flat/fixed Applications omit them.
Thread lookup is independent of Conversation selection. Native activation is
an explicit optional operation.

An App Server adapter may receive an immutable, deployment-supplied native
Thread-start option mapping. This is an adapter configuration seam for native
sandbox/approval defaults, not a common policy contract: `CreateThread` keeps
the shared control intent, the adapter-owned `cwd` cannot be overridden, and
the mapping is neither persisted nor exposed as Agent state.

A consumer whose conversation UX selects among several native profiles may
call the concrete adapter's `create_thread_with_options` seam, then bind the
returned authoritative `ThreadSummary` through the ordinary Gateway operation.
This remains outside the common Application Port because the option vocabulary
is native and the selection policy belongs to the consumer.

History contains all completed Agent messages in a Turn. Application-native
message phases remain namespaced Metadata until common reuse is proven.

## Events and recovery

Native notification producers publish into independent, bounded subscriber
queues without awaiting Channel delivery. Filling one subscriber terminates
only that live subscription with an explicit overflow; Gateway resubscribes
and reconciles native-authoritative state. The adapter emits a separate
explicit terminal Turn event after any number of completed messages.

App Server notification and server-request dispatch use separate bounded
queues whose overflow resets that connection. The adapter translates the
reset into an explicit gap for every current Application subscription; Gateway
then performs authoritative completed-output recovery and pending-request
snapshot recovery, or reports degraded request recovery when no snapshot
exists. T3 polling uses the shared bounded per-subscriber fan-out and does not
keep polling solely for an overflowed subscriber. Queue capacity is injectable
adapter infrastructure; product retry and degraded UX remain consumer policy.

An Application may optionally expose stable redacted `diagnostic_facts()`.
This structural seam is not part of the required Application port: App Server
has a meaningful connection epoch and bounded dispatch queues, while T3's HTTP
request/response transport has no equivalent long-lived connection. Exporters,
health endpoints, polling, and operator presentation remain consumer policy.

The App Server client also exposes an adapter-only ordered admission fence
across those two lanes. Earlier non-response frames are admitted before a
later JSON-RPC response completes; callback payloads carry a public
`AppServerDispatchPosition`. A caller that needs the exact response fence uses
`call_with_dispatch_position`, which returns an immutable `AppServerResponse`.
Frames admitted after that response cannot widen its fence; the mutable
last-admitted position is diagnostic/compatibility state only. Handler
completion may still be out of order. The position resets with the connection
epoch and is neither a Core `AgentEvent.sequence` nor a replay cursor. Gateway
projection uses its acceptance buffer and authoritative recovery rather than
persisting or interpreting this transport fence; products with an IM-specific
immediate-response gate may consume it as adapter policy.

Replay, gap detection, and sequence scope are separate capabilities. When the
native endpoint lacks replay or restart-safe sequence, fields are omitted and
Gateway recovers from authoritative history/catch-up.

## Attachments and requests

Adapters accept only declared typed sources and apply the
[attachments/media trust boundary](../attachments-and-media/design.md).
Workspace roots, URL policy, native upload, and media encoding remain
adapter/deployment behavior.

Approval and user-input request truth remains native. The common adapter may
translate request/response semantics; it never decides Full Access, sandbox,
or consumer permission policy.

An adapter advertises interactive requests only when it can translate both an
open request and its response without guessing a native wire shape. It maps a
native terminal or an invalidated response handle to typed resolution, while
the native Application remains first-writer authority. Pending-request
recovery is declared separately in adapter documentation and is performed only
from an authoritative native pending set.

## Native pages

- [Codex](adapters/codex.md)
- [Zen](adapters/zen.md)
- [T3](adapters/t3.md)

## Change obligations

Every change requires focused native tests, the reusable adapter contract
suite, event/recovery tests, and a capability honesty review.

The Codex App Server client ownership transfer is scoped by the
[Issue #9 SDK-side map](../../migrations/issue-9-imcodex-owner-transfer.md);
product supervision/configuration outside that map remains downstream.
