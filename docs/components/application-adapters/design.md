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

Metadata intended for outbound projection contains only bounded non-secret
scalar presentation facts. Before an App Server resource, history item,
notification, or server request can create those facts, its single mapping
owner validates finite native text/collections/keys/content aggregation and
the method-required stable identity. The owner copies only validated values;
oversize or malformed input cannot be truncated into a different identity or
reach canonical events, history, request mapping, or A1 consumer code. Current
App Server keys are `phase`,
`native_application`, the fixed `native_method`/`kind`, and the fixed
`live_only` marker; current T3 keys are
`kind`, `native_application`, `source`, and `streaming`. Thread/Turn/item IDs
remain in canonical fields, not Metadata. Raw native payloads, credentials,
paths, content copies, sender identity, and exception text are excluded before
the canonical `AgentMessage` crosses the adapter boundary.

ADR 0015 A1 permits a concrete adapter to expose bounded typed presentation or
artifact-candidate facts inside this same ordered normalization path. It never
exposes raw notifications or creates a second subscriber. A presentation
claimed as recoverable must produce the same association from authoritative
history; live-only output remains explicitly non-replayable. Candidate bytes,
filesystem trust, leases, quotas, and durable spool state remain consumer
policy outside the adapter and Core.

The non-artifact A1 surface is deliberately split by native evidence.
`CodexLiveActivityPresenter` receives a frozen `CodexLiveActivityFacts` value
whose kind, scalar summary, plan entries, changed-file count, and status were already
allowlisted and bounded by the Codex adapter. `T3ActivityPresenter` receives a
frozen `T3ActivityFacts` value whose stable activity/Thread/Turn identity,
namespaced kind, summary, detail, and native creation time were already
bounded by the T3 adapter. These are separate concrete-adapter protocols, not
a common `AgentApplicationAdapter` method, stage-enum callback, raw event
callback, or context bag.

Both presenters return only `ApplicationTextPresentation`: a non-empty finite
tuple of typed text values under a total-character bound. They cannot choose
item/event identity, role, Thread, Turn, recoverability, event type, native
metadata, checkpoint behavior, attachments, paths, or delivery destination.
`None` means that native fact has no canonical presentation; destination-
specific visibility, suppression, decoration, and branding remain O1 or
consumer policy.

Presenter invocation is async with finite lifetime and concurrency. Active
tasks, including cancellation overruns, retain capacity and receive bounded
adapter shutdown cleanup. Codex invocation inherits the existing bounded App
Server notification dispatch lane and therefore never runs on its socket read
loop. T3 invocation runs in its existing polling/history normalization flow;
it creates no subscriber or polling lane. Live deduplication identity windows
are finite; recoverable T3 history may reinvoke a replay-safe presenter after
an identity leaves that process-local window. Failure is explicit through the
owning adapter path and fixed process-local presentation diagnostics retain no
facts, output, identity, or exception text.

T3 returns known native acceptance before optional A1 work: when a presenter is
configured, post-send observation is left to the already-owned polling/history
path. Presenter failure therefore cannot erase the `AcceptedTurn` or delay its
reply correlation. A polling failure terminates that Thread's live subscription
with an explicit recoverable gap; fixed diagnostics remain redacted and later
poll/history recovery may retry the replay-safe presenter.

Codex A1 output is live-only. A stable native event ID is preserved when
available (otherwise a process-local generated identity is honest), the
adapter emits `message.created`, and reconnect/overflow may lose it. It is not
read from history and never advances a completion checkpoint. T3 A1 output is
recoverable: the adapter invokes the same presenter over the same normalized
activity facts in polling, catch-up, and history, fixes the stable activity ID
as `AgentMessage.agent_item_id`, and emits `message.completed`. Presenter
implementations must be replay-safe; SDK persistence stores neither facts nor
rendered output.

App Server artifact A1 is separate from both non-artifact presenters. An
optional `AppServerArtifactMaterializer` receives frozen
`AppServerCompletedItemFacts` for every completed native item and one
`AppServerTurnTerminalFacts` after all items of a terminal Turn. Facts contain
only bounded stable Thread/Turn/item identity, fixed native item kind/phase,
bounded presentation scalars, and a finite tuple of typed untrusted artifact
candidates. No raw mapping, client, credential, byte value, or trusted path is
exposed. Native Turn and item IDs are required: missing IDs fail observation
or history explicitly and are never replaced by random values, text, locator
content, or timestamps. Codex supplies the concrete image-generation/dynamic-tool evidence;
Zen is the shared App Server transport counterexample and changes only when a
materializer is explicitly configured.

The materializer returns `ApplicationArtifactMaterialization` or `None`.
Output contains only a finite tuple of validated typed `AttachmentContent`;
it cannot choose Agent message/event identity, role, Thread, Turn, terminal
status, checkpoint behavior, or destination. For an ordinary item the adapter
appends returned attachments to that item's canonical message, if any. At a
completed/interrupted/failed terminal it may emit one adapter-identified
artifact-only fallback immediately before the terminal event. This supports a
replay-safe consumer that accumulates candidates by stable identity and
associates them with a final answer or terminal fallback without transferring
that state to SDK persistence.

Invocation uses a distinct async finite runtime but stays in the App Server
adapter's existing ordered notification/history lane, off the socket reader.
Duplicate live completed-item identities are suppressed within a finite
process-local window. Authoritative history deliberately may reinvoke the
materializer; consumers must make candidate processing idempotent and return
the same association. A live facts, timeout, capacity, cancellation, output,
or consumer failure explicitly terminates that Thread's current subscription
with the fixed `application_artifact_materialization_failed` recovery gap,
preserving already queued events before the gap and preventing later native
notifications from crossing it. The adapter establishes this gap itself
because the production App Server dispatcher contains handler exceptions.
Authoritative history fails the read attempt instead. Facts, materialized
attachments, consumer state, and cleanup work are never stored or replayed by
the SDK. O2 may release a clean-process consumer lease after delivery, while
crash-safe cleanup remains a consumer ledger/startup sweep.

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

Before either App Server callback lane, its transport applies one positive
non-boolean byte limit to every stdio or WebSocket JSON frame. Oversize fails
with a fixed redacted transport error, poisons that connection, and enters the
same epoch reset without JSON decoding, callback dispatch, or suffix parsing.
The 64 MiB default preserves complete native Thread/resume frames while making
the former unbounded receive path finite; consumers may configure only this
single transport value, not a second product framing path.

An Application may optionally expose stable redacted `diagnostic_facts()`.
This structural seam is not part of the required Application port: App Server
has a meaningful connection epoch and bounded dispatch queues, while T3's HTTP
request/response transport has no equivalent long-lived connection. Exporters,
health endpoints, polling, and operator presentation remain consumer policy.

App Server's internal protocol/debug logging is a separate adapter-local
security boundary. It uses its fixed `appserver.debug.v1` structural vocabulary
and never widens the ADR-0014 fact surface: native IDs, text, paths, commands,
questions, permissions, credentials, endpoints, and arbitrary payload values
do not cross it. The client may retain only fixed categories, bounded counts
and lengths, and documented SHA-256 fingerprints.

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
