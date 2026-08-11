# Common protocol

This document defines semantic contracts, not one transport encoding. JSON,
Python, TypeScript, JSON-RPC, HTTP, WebSocket, or in-process calls may carry
the same objects. `schemas/v1/` is the precise language-neutral shape.

## Identity and resource scope

Every externally visible mutation and event has a stable identifier. Text and
timestamps never define identity or deduplication.

```text
ApplicationRef = applicationInstanceId
ProjectRef = (applicationInstanceId, projectId)
ThreadRef = (projectRef, threadId)
TurnRef = (threadRef, turnId)
ConversationRef = (channelInstanceId, nativeConversationId)
```

Native IDs are opaque and scoped by one configured Application or Channel
instance. A native Thread ID from one Application instance cannot be used with
another.

The Python reference owner for `ApplicationRef`, `ProjectRef`, `ThreadRef`, `TurnRef`,
the Project/Thread/Turn summaries and statuses, input/history values, and
the workspace identity/fingerprint values, and their validators are
[`applications.application-contract`](../applications/application-contract/design.md).
The complete Application contract family is exposed by that owner and the
finite `imagent.applications` facade. `imagent.contracts` retains only the
exact `ApplicationInputOutcomeUnknown` alias from this Applications block;
capability, operation, request, and all other Application model names are not
compatibility exports.

The full organization model is:

```text
AgentApplication
  └── Project
        └── Thread
              └── Turn
```

An Application declares its real Project-management shape while every runtime
Thread remains Project/Workspace scoped:

- `managed`: Projects are authoritative selectable resources.
- `flat`: one stable adapter workspace Project represents the native
  execution context; native Project management is unsupported.
- `fixed`: one stable configured workspace Project represents the configured
  workspace/CWD; native Project management and switching are unsupported.

Fixed/flat discovery and reading are declared adapter projections rather than
native management. They return exactly one honest `ProjectSummary` whose
`ProjectRef.projectId` is the configured immutable `workspaceId` and
whose `workspaceRootFingerprint` is the lowercase SHA-256 digest of the UTF-8
canonical execution-root text, bounded to 4096 encoded bytes. The adapter canonicalizes the configured root
before hashing and keeps both values stable for its lifetime. Reusing one
workspace ID with a different fingerprint is a Gateway-startup conflict once
block B supplies the coherent store; an intentional replacement uses a new
workspace ID. The fingerprint is typed identity evidence, not Metadata, and
Gateway persistence never needs the root path itself.

Every `ThreadRef` requires its `ProjectRef`, and both references must name the
same configured Application instance. Consequently all Thread, Turn, event,
history, request, binding, route, checkpoint, and correlation identity reaches
the Project ancestor through the same required reference. No Project-less
wire or Python branch remains.

## Resources and bridge state

Agent Applications own Project and Thread resources, transcript items, Turns,
requests, execution, archival, and retention.

A `ConversationBinding` selects the destination of future input:

```text
conversationRef
applicationRef?
projectRef?
threadRef?
generation
updatedAt
```

A `ThreadProjectionRoute` selects a possible IM output destination:

```text
routeId
threadRef
conversationRef
replyToMessageId?
updatedAt
```

Bindings and routes are distinct. Neither contains transcript, Turn, request,
or execution truth. Native Thread activation is a third, optional mutation.

## Message envelopes

Message objects carry content, not control intent.

```text
InboundMessage {
  messageId
  conversationRef
  sender
  content[]
  replyTo?
  createdAt
  metadata
}

OutboundMessage {
  deliveryId
  conversationRef
  content[]
  replyTo?
  createdAt
  metadata
}

AgentMessage {
  agentItemId
  threadRef
  role
  content[]
  clientMessageId?
  createdAt
  metadata
}
```

`AgentMessage` is an Applications-owned immutable item with strong
`ThreadRef` scope. Interaction owns only the `Content`, `MessageRole`, and
`Metadata` values that it composes; Events consumes the item while owning only
the event envelope, ordering, fan-out, and gap semantics.

Inbound native identity, an outbound delivery request, and an authoritative
Agent item are deliberately different envelopes. A Channel returns native
delivery identity in `DeliveryReceipt`.

A logical outbound message may require several Channel-compatible sends.
`DeliveryReceipt.segments` records every planned segment's stable delivery ID,
source content indexes, and accepted, rejected, retryable, unknown, or skipped
outcome. `DeliveryReceipt.items` aggregates those outcomes back to the logical
content. If one source item spans an accepted prefix and a failed or skipped
suffix, its item status is `unknown`; the segment receipts retain the exact
boundary. This is delivery evidence, not transcript or Agent execution truth.

Initial content parts are `TextContent` and `AttachmentContent`.

```text
AttachmentSource =
  LocalPath { path }
  | RemoteUrl { url }
  | AttachmentHandle { handleId }
```

Attachment location never travels through Metadata. `LocalPath` requires
deployment-configured shared-filesystem trust. `RemoteUrl` requires
adapter-owned network, redirect, size, and media policy. `AttachmentHandle` is
reserved until a resolver is configured.

The Gateway derives or preserves a stable client message ID from:

```text
(channelInstanceId, conversationId, channelMessageId)
  -> clientMessageId
```

The authoritative user-item event repeats that ID so clients can deduplicate
optimistic local echo.

## Application input acceptance

`InputContinuationPreference` is `prefer_active_turn` by default or explicitly
`start_new_turn`. The default is a preference: an adapter returns the actual
disposition supported by its native Application.

Immediately before native mutation the adapter supplies:

```text
ApplicationInputDispatch {
  threadRef
  clientMessageId
  disposition = started | steered
  correlationPolicy = create_new | preserve_existing
  expectedTurnRef?
}
```

`started` requires `create_new` and no expected Turn. `steered` requires
`preserve_existing` and an expected `TurnRef` nested under the same Thread.
The accepted result carries that exact native `TurnRef`, client-message
identity, disposition, and correlation policy. These values describe native
acceptance and bridge routing policy; they do not transfer Turn authority into
the SDK.

## Typed operations

Application operations mutate or read one native Agent Application:

| Operation | Success result | Meaning |
|---|---|---|
| `project.list` | `ProjectsListed` | list authoritative Projects |
| `project.get` | `ProjectRead` | validate/read one Project |
| `project.create` | `ProjectCreated` | create one managed native Project from a bounded CWD |
| `project.delete` | `ProjectDeleted` | delete one empty managed native Project without changing any Conversation binding |
| `thread.create` | `ThreadCreated` | create a native Thread |
| `thread.list` | `ThreadsListed` | list native Threads |
| `thread.get` | `ThreadRead` | validate/read one Thread |
| `thread.activate_native` | `NativeThreadActivated` | optionally change native UI selection |
| `thread.delete` | `ThreadDeleted` | archive/delete with actual mode |
| `thread.status` | `ThreadStatusRead` | read projected native status |
| `thread.history` | `ThreadHistoryRead` | read authoritative Turn history |
| `turn.catchup` | `TurnCatchupRead` | read latest Turn progress |
| `turn.interrupt` | `TurnInterrupted` | interrupt a native Turn |
| `request.respond` | `RequestResponded` | answer a native request |

Gateway operations mutate only Gateway-owned selection or routing:

| Operation | Success result | Meaning |
|---|---|---|
| `application.list` | `ApplicationsListed` | list configured Application instances |
| `application.select` | `ConversationBound` | select Application and clear narrower selection |
| `conversation.bind_project` | `ConversationBound` | validate/select Project and clear Thread |
| `conversation.bind_thread` | `ConversationBound` | select future input destination; under `foreground_only`, atomically prepare its policy-required output edge before binding CAS |
| `conversation.clear_thread` | `ConversationBound` | clear selected Thread |
| `conversation.clear_project` | `ConversationBound` | clear selected Project and Thread while retaining Application |
| `conversation.clear_application` | `ConversationBound` | clear all selected Application resources |
| `thread.observe` | `ThreadObserved` | establish/refresh output route |
| `thread.clear_observation` | `ThreadObservationCleared` | remove one output projection route without changing input binding |
| `conversation.respond_request` | `RequestResponseRouted` | validate one delivered destination and route a native response |

Application and Gateway operation unions have discriminated variants with
typed arguments. Success results repeat the operation ID and discriminant and
use named fields. Failures use `ApplicationOperationFailed` or
`GatewayOperationFailed` with a stable `ContractError`.

Listing is side-effect free and paginated. Thread creation does not bind a
Conversation. Binding a Thread does not activate native UI state. Under
`foreground_only`, binding also prepares the matching additive projection route
because binding equality is that policy's output authority; before the bind or
after a switch, the route is inactive. Other projection policies retain
explicit observation. Observing a Thread never binds input or activates native
state. A product may compose these operations explicitly where those separate
effects are intended.

Thread deletion declares `unsupported`, `archive`, or `permanent`. Destructive
semantics are never silently approximated.

## History and Turn lifecycle

`turn.catchup` and `thread.history` are authoritative context restoration, not
token streaming. A history entry contains the user goal, every ordered
completed Agent message, terminal status, error, and compaction marker.

`agentMessages` is plural because one Turn may emit several completed messages.
Application-specific phases such as commentary or final answer may stay in
namespaced Metadata.

When an `AgentMessage` is projected to an `OutboundMessage`, its normalized
Metadata is validated as at most 16 scalar facts, with 64-character keys,
256-character text values, finite numbers, and signed 64-bit integers. The
fresh mapping is immutable. Core does not interpret native phase/kind keys,
synthesize native payloads, or change delivery, destination, reply, and
checkpoint identity because Metadata is present. Unsupported nested, oversized,
or non-finite values fail explicitly before Channel delivery.

`message.completed` never means `turn.completed`. Only explicit
`turn.completed`, `turn.failed`, or `turn.interrupted` events terminate a
Turn.

## Agent events and ordering

Every `AgentEvent` has one required Project ancestor. When `threadRef` is
present, its required Project must equal `projectRef`; request events inherit
the same ancestry through their required Thread.

```text
eventId
applicationInstanceId
projectRef
threadRef?
turnRef?
sequence?
sequenceEpoch?
cursor?
type
data
createdAt
```

Initial event families:

- message: `message.created`, `message.delta`, `message.completed`;
- Thread: `thread.created`, `thread.updated`, `thread.deleted`;
- Turn: `turn.started`, `turn.completed`, `turn.failed`,
  `turn.interrupted`;
- status/request: `status.changed`, `request.opened`,
  `request.resolved`;
- Gateway: `binding.changed`.

Ordering guarantees are honest:

- `eventId` is required and stable within the producer's declared window.
- `sequence` is optional and requires `sequenceEpoch`.
- `cursor` is optional, opaque, and appears only with real replay support.
- cursor expiration and detected gaps are explicit.
- timestamps are descriptive, never authoritative order.
- adapters without native replay or restart-safe sequence omit those fields.

Recovery falls back to a live subscription plus authoritative
history/catch-up reconciliation. The SDK never presents an in-memory counter
as restart-safe recovery.

## Interactive requests

`request.opened` carries one typed request:

```text
ApprovalRequest {
  requestRef{applicationRef, nativeRequestId}, turnRef, prompt,
  choices[{choiceId, label, description?}], expiresAt?, metadata
}

UserInputRequest {
  requestRef{applicationRef, nativeRequestId}, turnRef, prompt?,
  questions[{ questionId, prompt, header?, choices[], allowsOther, secret,
              minAnswers, maxAnswers }],
  expiresAt?, metadata
}
```

`request.resolved` carries a typed resolution with the same `RequestRef`, the
exact originating `TurnRef`, and one of `resolved` or `stale`. `stale` means the adapter can prove that the response
handle is no longer usable, including a transport reset without a native
pending-request snapshot. It does not claim that the native Turn or request
was otherwise deleted.

Approval responses return one stable `choiceId` from the native choices
projected with that request. Core does not interpret once/session/cancel scope
or choose among them. Session grants, persistent command/network policies,
sandbox profiles, and Full Access remain native Application or consumer
policy. User-input responses map question IDs to string-answer tuples and are
validated against explicit minimum/maximum cardinality, available choice IDs,
and `allowsOther`.

For finite bridge validation and persisted response routing evidence, one
interactive request and response shape contain at most 32 questions, and one
approval or question contains at most 64 choice IDs. A proactive submission
contains at most 64 immutable destination snapshots. These are rejected before
proportional fingerprinting, reservation, SQL, or native side effects; they do
not evict retained evidence or introduce a spool/outbox.

`secret` is a sensitivity requirement, not a claim that any presenter or
Channel can collect the answer securely. A presenter without an evidenced
secure-input capability must refuse response collection and must not create a
plain-text answer route.

`RequestRef` prevents two Application instances with the same native request
ID from sharing authorization or state. Native IDs are opaque but must be
stable within their Application instance; an adapter whose transport reuses
IDs across reconnects namespaces the epoch into `nativeRequestId`.

The Conversation operation includes only `RequestRef` and typed response.
Gateway looks up Application/Thread scope from a correlation created after
that Conversation actually received the prompt. It never trusts caller
Metadata for native request routing.

## Capabilities and failures

Capabilities distinguish native support, declared fallback, and unsupported
behavior. Channel delivery fields use `DeliverySupportLevel`; Application
resource/runtime capability fields use the nominally distinct `SupportLevel`.
Their v1 string values intentionally match, but schema consumers must not treat
the two ownership domains as one type. Project mode, Thread deletion, native
activation, attachment source kinds, replay, gap detection, and sequence scope
are separate facts.
The v1 `ChannelCapabilities` wire/Python constructor remains flat.
`ChannelCapabilities.delivery` is a derived typed `DeliveryProfile` covering
the same text format/length units, attachment source/media/grouping limits,
and reply scope; it describes what the common planner may produce, not
credentials or a native API contract. New v1 planning properties and
`DeliveryReceipt.segments` are optional extensions, so documents valid before
ADR 0010 remain valid. Existing positional constructor fields also retain
their original order; new planning fields are appended after the legacy v1
surface.

Unsupported, stale-reference, authentication, access, binding, unavailable,
rejected-operation, delivery, gap, and cursor-expired outcomes remain explicit.
Products may retry safe idempotent work, but Core does not invent a hidden
self-healing workflow.

## Delivery correlation

Completed Agent messages are projected using current routes. One authoritative
item may become several native segments. Stable delivery identity derives
from:

```text
(destination, authoritativeMessageItemId, segmentIndex)
```

A receipt distinguishes platform acceptance, rejection, explicit retryable
failure, and unknown outcome.
Platform acceptance does not claim device display or read.
Retryable top-level, item, and segment evidence cannot carry a native message
identity: observed native acceptance makes automatic replay unsafe.

`DeliveryReceipt` and its capability/profile values are owned and imported
from `imagent.interaction.channels`; they are not part of the historical
`imagent.contracts` facade. `DeliveryReceipt.items` carries typed per-content
outcomes when a native delivery can partially succeed. A completed Channel call may therefore have
aggregate platform acceptance while one attachment is rejected; Gateway
projects that destination as `partial` instead of hiding the artifact failure
in Metadata.

`ThreadProjectionRoute.checkpointAgentItemId` and `checkpointedAt` are a
nullable pair. They identify one destination's last completed ordered
delivery decision; the Agent item ID is opaque and not a sortable SDK
sequence.

`TurnReplyCorrelation` contains one nested `TurnRef` plus client-message identity and the
originating Conversation, reply ID, and creation time. It is minimal bridge
state, not a copy of Turn status or request truth. It applies only when the
projection destination matches its Conversation.

`RequestRouteCorrelation` is also per destination. It contains only the
application-scoped request, nested `TurnRef`, Conversation, delivery, expiry, and bridge
projection-state identity plus only the response shape required to validate a
choice/cardinality. This response shape is routing-validation state, not a
copy or assertion of native pending-request truth. It never contains the
prompt, requested permissions, or response. `open`, `responded`, `resolved`, and `stale` describe
whether this bridge may route another response; the native Application remains
request authority.

## Proactive delivery

`DeliveryIntent` is the common semantic request for output that was not caused
by a new inbound message. It contains a caller-stable delivery ID, content,
and either:

- an explicit `ConversationDeliveryTarget`; or
- a `ThreadRouteDeliveryTarget`, optionally narrowed to one route.

The typed proactive vocabulary and validator are implemented only in
`gateway.delivery.proactive`; their finite public facade is
`imagent.gateway.delivery`, and the eight historical proactive names are not
exported from `imagent.contracts`. The closed identity/fingerprint helpers
belong only to `imagent.gateway.delivery.submissions`. `DeliverySubmissionOrigin`
and the passive delivery state/record/reservation values are owned by
`imagent.gateway.persistence` and re-exported by the Gateway delivery facade;
they are no longer part of `imagent.contracts`.

A Thread target reuses the configured projection policy. It may therefore
resolve to several destinations under `all_observers`. Gateway's
proactive-authorization leaf authenticates
an opaque credential into a `DeliveryPrincipal` and checks the requested
Thread or explicit Conversation scope before route resolution.

The first resolved route set is stored as immutable
`DeliveryRouteSnapshot` values. Delivery identity is namespaced by an
SDK-controlled external/internal origin and the caller delivery ID; the
admitting trusted principal remains durable reservation evidence but credential
rotation cannot create a second execution for that stable ID. External callers
cannot impersonate the Gateway-internal domain. Reusing a delivery ID with a
different target or payload is a conflict. A terminal or unknown retry returns
the stored result before current authentication or target/route work; only an
explicitly retryable destination requires fresh authorization. A retry returns the stored
accepted, retryable, rejected, partial, in-flight, or unknown result; it never
silently follows a route that moved after the first submission. Unknown
remains ambiguous and is not treated as permission to resend.

Internal persistence retains the resolved Conversation snapshot. A
Thread-targeted public result exposes only its route ID, state, and sanitized
receipt without native message IDs or free-form Channel diagnostics; only an
explicit Conversation caller receives the Conversation it already supplied.

External proactive `LocalPath` content requires lowercase `metadata.sha256` as its
logical content identity. The accepting Channel verifies that digest while
reading its configured trusted spool through a descriptor-relative no-follow
chain; it hashes and uploads the same acquired bytes. Temporary paths therefore do not define
retry identity, and changing bytes without changing the authoritative digest
cannot become a new send.

The optional JSON ingress is not the semantic protocol. It is a safe adapter
for local tools: inline base64 artifacts are authorized before decoding,
materialized beneath a configured private staging root, synchronously
submitted as `LocalPath`, and then removed. Its response deliberately omits
Channel-native Conversation IDs for Thread-scoped callers.
