# Zen Application adapter design

Component ID: `applications.adapters.zen`

Parent: `applications.adapters`

## Purpose and ownership

This leaf owns the Zen concrete Application adapter over the shared App Server
protocol: native Thread/Turn resources, history/catch-up, start/steer dispatch,
event normalization/publication, and the native request surface actually
evidenced by Zen (currently command approval). Zen native state remains
authoritative.

It does not own Codex live-activity semantics,
Zen product policy, Gateway binding/delivery/correlation, persistence, raw
native events, or a second runtime/subscription/transcript. Sharing the App
Server transport does not make an unevidenced Zen capability supported.

## Typed boundary and public facade

Inputs are a typed App Server client, fixed Zen adapter configuration, common
Application operations/inputs, and optional typed artifact materialization.
Outputs are common typed summaries/results, truthful `started/create_new` or `steered/preserve_existing`
input outcomes, canonical `AgentEvent` values, and bounded artifact facts when
configured. Native request and capability gaps fail explicitly.

Fixed configuration includes required immutable `workspace_id` and `cwd`.
The adapter exposes one listable/readable workspace Project with its typed
canonical-root fingerprint and uses its ProjectRef for all Thread/event/
history/request identity. Native Project management remains unsupported.
It exposes or mutates a native Thread under that Project only after an
authoritative native read supplies the same Thread ID and matching canonical
`cwd`; missing or foreign scope evidence fails closed, including live and
interactive-request publication.

Deployment-owned `thread_start_options` may supply Zen-native sandbox and
approval defaults for new Threads; the adapter copies them, owns `cwd`, and
does not persist them as SDK Thread state. A consumer with per-Conversation
presets may use the concrete `create_thread_with_options` seam and bind its
returned native Thread through Gateway; the preset remains client
selection/configuration, not Zen or SDK runtime state.

The current formal export is `ZenApplicationAdapter` from the lazy
`imagent.applications` facade, implemented at the exact target
`imagent.applications.adapters.zen:ZenApplicationAdapter`; the top facade
preserves the same object identity and lazy import behavior. The private
shared App Server base is an explicitly mapped two-owner split candidate under
`imagent.applications.adapters.appserver._base`; it is not a public aggregate
adapter API. Its input path is truthful start-only behavior: the shared base
owns typed text/image preparation, verified local-image epochs, the common
typed pre-dispatch fence, `STARTED`/`CREATE_NEW` classification with no expected
Turn ID, and native `turn/start`. The shared base has no steer configuration, active-Turn read,
`STEERED`/`PRESERVE_EXISTING` classification, or native `turn/steer`; Zen owns
its evidenced steering locally.

## Dependencies, state, and recovery

Zen depends on Interaction messages/operations/media, common Applications
contract/capabilities/events/operations/requests, the direct canonical
`applications.diagnostics` owner for Application diagnostic facts, four direct
App Server leaves (client, mapping, requests, and the separate adapter
diagnostics state owner), and optional artifact materialization. Transport
remains transitive through the App Server client.
`prefer_active_turn` discovers the active Turn through a workspace-verified
native Thread read and uses Zen `turn/steer` with `expectedTurnId`. The concrete
`steer_active_turn=False` opt-out and `start_new_turn` preference use native
start. The typed pre-dispatch callback authorizes the exact Turn and immutable
reply correlation before mutation. A rejected steer never falls back to start.
Missing acceptance identity is unknown; a returned different Turn is handled
by the existing Gateway post-acceptance mismatch fence. A dispatched start with lost response is unknown, not an automatic
retry. History is native recovery authority; connection reset and
observation/materialization failure are explicit gaps.

## Current, target, and structural gap

Current code is `src/imagent/applications/adapters/zen.py` plus the explicitly
mapped private shared base
`src/imagent/applications/adapters/appserver/_base.py`. Adapter-owned evidence
is in `tests/applications/adapters/test_zen.py`,
`tests/applications/adapters/appserver/test_client.py`,
`tests/applications/adapters/appserver/test_requests.py`, the artifact
presentation suite, and
`tests/gateway/test_vertical_slice.py` for distinct Zen/Codex behavior. The
retained `tests/applications/adapters/appserver/test_input_integration.py` remains affected cross-component
evidence. Zen owns the concrete facade and has no dependency on Codex.

## Authority

- [Adapter block](../README.md)
- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)
- [ADR 0016](../../../../decisions/0016-uniform-workspace-and-consumer-actions.md)

## Evidenced Zen input capability restoration

Zen's Codex-protocol implementation at commit `1702ca4` exposes `turn/steer`
with `expectedTurnId` and `image` with a base64 data URI. The Zen owner adopts
those native capabilities independently of Codex; it never imports the Codex
adapter or restores the retired aggregate adapter. The executable interop
scenario must run the public SDK adapter/client against the real Zen protocol.

When a trusted root is configured, Zen advertises `LocalPath` image sources.
It validates every image's finite declared byte size and supported media type,
acquires bytes through Interaction's no-follow descriptor chain, and sends
those exact bytes as an inline data URI. Per-input item and aggregate byte
bounds prevent unbounded acquisition. No native pathname is transmitted and
no shared-filesystem connection epoch is needed for inline bytes. Root, file,
size, or type failures precede native dispatch. A pathname replacement after
acquisition cannot change submitted bytes. The common base and Codex retain
the path-only rejection; the old pathname race is never restored.

## Explicit Thread creation profiles

`ThreadCreationProfile` is an optional bounded capability descriptor containing
only a profile ID and SHA-256 configuration fingerprint. Concrete App Server
adapters freeze configured `thread_creation_profiles` JSON options and advertise
their descriptors. Zen and Codex support this mapping; T3 does not silently
accept profiles. Native policy names remain adapter/consumer-owned.

Scoped `create_thread` and `create_and_bind_thread(creation_profile=...)` resolve
the ID from validated capabilities and include the exact descriptor in both the
`CreateThread` operation and durable action fingerprint. The adapter checks the
exact descriptor before native creation. Unknown selections fail closed; same
action ID with changed selection or changed configured content conflicts,
including after SQLite reconstruction. No native options enter receipt storage.
Omission preserves existing default behavior and legacy action fingerprints.

Profile definitions are complete per-call options rather than merged mutable
defaults. They are copied, limited to 32 profiles/128-character IDs/64 KiB of
canonical JSON per profile, and reject non-JSON/NaN/reserved workspace overrides.
The selected profile does not mutate an existing Thread.

## Native queued-input contract

The Zen-owned `zen_queue.py` translates strict native admission and bounded
canonical Items into `QueuedInputReceipt`. `read_queued_input_receipt` validates
workspace/Thread and scans at most 10,000 items from the full `zen/thread/read`
snapshot; it never resends, calls Resume, or fabricates a past queue failure.
Host/Agent items cannot prove human input even when client IDs collide. Conflicting
lineage, terminal state, delivery mode, scope or a truncated/oversized response
fails closed. The real original `user_message.turnId` is execution authority.

Direct adapter queue input negotiates `zen/initialize.capabilities` each attempt,
requires `queued-input-admission-v1` and image-specific `inline-image-input-v1`,
and fences the resulting connection epoch across the pre-dispatch callback.
Rooted input preparation is reused; canonical data-URI bytes become explicit
wire-only `image_bytes` fields with no path transfer. The fingerprint binds the
ordered canonical prepared envelope (including the exact encoded acquired bytes).
Native `queue_zen_input` has no automatic retry, including overload responses.
A missing/conflicting successful receipt is unknown. Until native negative codes
are independently proved, response errors remain conservative unknown.

The minimum native protocol in [ADR 0019](../../../../decisions/0019-queued-input-admission.md)
is now independently evidenced against Core `b8d16bfed194e003bcf1238458f43c1cba365f95`
(tree `71c1a6ff`) using the production SDK client and authenticated disposable
native fixture. Older Hosts without the negotiated capability still reject
before mutation. Application lifecycle evidence is separate from the Gateway
end-to-end release gate.

### Recoverable input fingerprint

The receipt carries the original admitted payload fingerprint, not content.
Hash domain is UTF-8 `imagent:zen-queued-input:v1` followed by one NUL byte, then
UTF-8 canonical JSON (sorted object keys, no extra separators/whitespace, no
Unicode normalization) of the ordered typed-part array. Text parts preserve
exact text and boundaries. Image parts normalize to `type=image`, lowercase
actual byte `sha256`, exact closed `mediaType` and integer `byteLength`.
Acquired inline bytes and canonical AttachmentRef therefore yield the same
digest; path, base64 spelling and derived dimensions are excluded. Changed
order, text boundary, bytes, MIME or length changes identity. Recovery always
hashes the original `user_message_queued.input`, even after an edit or admission
whose execution content differs. The future Gateway receipt owner compares
this evidence with its original pre-dispatch fingerprint; it does not reopen
input or create a new receipt when the native queue is edited.

The `read_queued_input_receipt` uses the read-only native
`zen/thread/read` boundary through the existing client connection. It does not
call Codex `thread/resume`, create a subscription, or activate/drain the queue.
Live recovery is owned by the bounded queue observer below. It installs native
and CAS observation separately through the client's per-Thread ownership; a
bare read never claims either subscription.

Core verification: native `zen/thread/resume` owns a per-Thread non-reentrant
NativeConnection barrier, subscribes/buffers first, then builds a
Core.readThread + watermark/tail recovery snapshot. Native `zen/thread/read`
is only a Core snapshot and does not occupy that barrier. Neither is a shared
Core-wide lock with Codex `thread/resume`. Recovery uses client lifecycle ownership, prohibits concurrent second native
resume, and must
never present a bare read as an installed live subscription.


## Bounded queued lifecycle

`read_queued_input_receipts` accepts at most 4,096 requested IDs including
positional duplicates. One full bounded `zen/thread/read` snapshot validates all
item scope/unique IDs, then resolves each requested lineage in O(items + IDs).
An absent requested ID is independent of another ID's positive receipt. Native
edits never replace the original queue-append fingerprint. There is no outcome
cache, polling scheduler, implicit subscription, or read-triggered execution.

`cancel_queued_input_receipt` verifies canonical Thread/workspace, client,
queued-item identity and original fingerprint, then calls production
`cancel_zen_queued_input` exactly once with `zen/thread/queue/cancel` and
`{threadId, items:[{clientId, queuedItemId}]}`. Canonical reread must prove
cancelled or admitted. A native `already_started` reservation without a canonical
human message remains unknown. Already-terminal canonical input needs no new
mutation. Malformed acknowledgements, lost transport, cancellation, timeout and
native errors are unknown after dispatch; no retry or hard interruption occurs.

`zen_queue_observation.py` owns bounded transient redacted observation, while the
App Server client owns one native resume RPC task per connection-epoch/Thread.
Both generic `call` routes for native resume and the explicit resume method use
that owner. Shielding prevents caller cancellation from releasing the native
non-reentrant barrier prematurely. Client close fences entry, resets unresolved
transport requests, and joins resume tasks. This does not claim a Core-wide lock
or conflate native resume with Codex-compatible `thread/resume`.

Observer registration precedes resume. The complete snapshot establishes a
bounded original queued-identity index, exact process/connection epochs and a
watermark; its finite ordered tail and immediately arriving notifications become
typed notices. Live watermarks must advance continuously; malformed scope,
epoch change and overflow terminate observation with an explicit gap. The index
contains identities only, not cached outcomes or transcript. Canonical queued,
admitted, cancelled and edited events yield `CHANGED`; `queue_failed` yields
live-only `ATTEMPT_FAILED` with no raw reason/content. The consumer reconciles
once after installation and after each notice. Normal `aclose` unregisters and
wakes only that observer; adapter stop joins owned installation tasks.

The observer indexes all native human queued identities in the Thread, including
input from other native surfaces. It does not apply SDK admission-mode or payload
fingerprinting rules to that index: native `batch-next`, Host-prepared skill
text, audio, and canonical-only image envelopes may coexist with SDK inputs.
Snapshot scope, unique item IDs and unambiguous client/queued lineage remain
mandatory. A notice is only a redacted reconciliation wakeup, never a receipt.
Receipt reads validate only the exact requested client lineage against the SDK
queue contract, and Gateway reconciles its stored original IDs and fingerprints.
Thus unrelated native input cannot block startup, while an SDK-owned ID with
changed mode, original content, source or scope cannot become a valid receipt.

## CAS output subscription alongside native queue observation

Queue installation first registers native buffering and completes the native
snapshot/barrier, then performs option-free CAS `thread/resume` through the same
client. The second barrier installs normal output/request observation on a fresh
connection; it is read-only and does not execute native queue Resume. Exact
Thread/workspace and connection epoch remain mandatory. Pending native callbacks
are retained while CAS installs. This is required even when a failed first queue
attempt produced no CAS Turn: after Gateway restart an explicit user Resume must
still deliver its original Turn's final response.

CAS and native barriers have distinct Core owners. The client serializes all CAS
public entrypoints per Thread, retaining each task through caller cancellation;
fresh calls with different options are not coalesced. Client close seals entry,
resets transport and joins owned RPCs before propagating repeated cancellation.
Consumers need no duplicate lock/task owner.
