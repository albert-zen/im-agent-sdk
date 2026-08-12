# Codex Application mapping

## Native authority

The Codex App Server owns native Threads, Turns, items, requests, execution,
history, interruption, and retention. The SDK adapter is a projection over
that control surface.

## Mapping

- SDK Thread maps to a native Codex Thread/session.
- Fixed workspace/CWD is adapter configuration exposed as one honest stable
  workspace Project. Its required immutable workspace ID and canonical-root
  fingerprint do not claim that App Server implements native Project
  management.
- `thread.list/get/create/delete/status`, history, catch-up, interruption, and
  input map to native App Server calls when supported.
- native user and Agent items become canonical completed message events;
  commentary/final-answer phases remain Metadata.
- native notification IDs are stable event IDs.

`turn/start` and `turn/steer` have no SDK-controlled native idempotency key.
The client therefore distinguishes failure before transport dispatch from
cancellation, timeout, disconnect, or response loss after dispatch begins. A
dispatched request without a response reports
`ApplicationInputOutcomeUnknown`; Gateway keeps the inbound key sticky rather
than risking a second native mutation. A successful response without a native
Turn identity is equally ambiguous and receives the same classification.

The SDK default input preference is active-Turn continuation, and the Codex
adapter enables its native implementation by default. A deployment may disable
native steering or a caller may explicitly request a new Turn. For the default
path the adapter reads the authoritative native Turn list immediately before
dispatch and steers the observed active Turn. That
read is only a candidate selection, not cached truth: the native steer response
supplies the accepted Turn identity. A definitive native rejection is itself
the authoritative reconciliation result and is re-raised unchanged without a
second read or `turn/start` fallback. Before dispatch it declares
`steered/preserve_existing`; Gateway rejects an uncorrelated Turn without
calling the native mutation. An ambiguous steer remains unknown. Zen accepts
the common preference but does not inherit this Codex wire implementation
merely because it shares the transport.

`message.completed` does not terminate a Codex Turn. The adapter emits the
native terminal Turn event separately.

## Optional non-artifact live presentation

`CodexApplicationAdapter` may be configured with the concrete
`CodexLiveActivityPresenter`. The adapter allowlists supported plan, diff,
Thread-status, compaction, and model-reroute notifications into bounded frozen
`CodexLiveActivityFacts` before invoking consumer code in the existing ordered
notification dispatch lane. Raw JSON-RPC, client access, credentials, paths,
and arbitrary payload fields never cross the seam. Diff facts expose only a
bounded changed-file count, never native file/path values.

The presenter returns bounded text only. The adapter fixes the original
Thread/Turn and event identity, system role, native method/kind metadata, and
`live_only=True`, then emits `message.created`. Gateway may deliver that live
event under stable outbound idempotency but never advances an authoritative
completion checkpoint. Reconnect or overflow may lose it; no SDK history or
replay is invented. Without the presenter, these notifications remain
invisible exactly as before. Zen does not inherit this Codex option.

## Optional artifact materialization

`CodexApplicationAdapter` may separately receive an
`AppServerArtifactMaterializer`. In the existing ordered completed-item and
authoritative-history path, image-generation `savedPath` and dynamic-tool
`file:`/`data:image/` values become finite frozen untrusted candidates with
stable candidate identity. The adapter never reads or trusts the locator. The
consumer validates and materializes it, returning only bounded typed
attachments; bytes, spool namespace, quotas, leases, cleanup ledger, startup
sweep, and error wording remain consumer policy.

Artifact facts require native Turn and item IDs. The adapter fails the live
observation/history read when either is absent; it never synthesizes A1
identity from random values, text, locator content, or timestamps.

The adapter invokes the same replay-safe materializer over live and history
facts. Returned attachments are appended to the canonical item chosen by the
native ordering/phase association, or one fixed-identity artifact-only message
is emitted before a terminal Turn when the consumer returns a terminal
fallback. Duplicate live item identities are suppressed only within a finite
process-local window; history may reinvoke after reconnect/restart. Failure is
an explicit observation/history failure: live failure terminates the affected
Thread subscription with the fixed artifact-materialization recovery gap even
though native dispatch contains handler exceptions, and history failure ends
the read. It never creates a second native subscription or SDK spool. Without
the materializer, item/event/history output is exactly the existing mapping.

## Recovery guarantees

The current App Server seam does not claim native replay when unavailable. It
omits cursor/sequence fields and uses authoritative Thread/Turn reads plus a
fresh live subscription. `event_buffer_max_pending` bounds each live
subscriber. Overflow is an explicit completed-message recovery gap; because
the current protocol has no pending-request snapshot, projection health also
remains degraded for possibly missed transient requests.

A successful App Server Thread create may precede native turn-history
materialization. The shared adapter retains a bounded typed record of that
exact create, its connection epoch, stable native session identity, and finite
native revisions authorized by create or allowlisted non-Turn initialization
facts and, only before the first native-input dispatch fence or allowlisted
turn-bearing native event, returns empty history/catch-up without calling
`thread/turns/list`. This keeps subscribe-before-baseline and lets first input
materialize the Thread without an `includeTurns` continuation probe; the
ordinary scope read remains required. Stop/reset, epoch/session/non-authorized
revision change,
same-ID recreation, evidence loss, reconstruction, all non-created Threads,
and every later recovery use strict native history; provider error
text is never treated as empty evidence.

The first-input plan carries that exact evidence generation rather than a raw
native Thread snapshot. After the asynchronous dispatch hook, Codex re-reads
scope and validates the same epoch/session/authorized revision/generation at
the `turn/start` boundary. Reset/reconnect, an allowlisted Turn notification or
supported server request, and same-ID replacement invalidate the plan before
native mutation. Retirement is generation-specific, so an older plan cannot
remove newer evidence for a reused Thread ID.

Interactive requests use the locally installed Codex App Server generated
schema and SDK-owned transport tests as the wire authority. The common mapping
supports command/file approval choices, structured tool user input, and
permission-profile approvals.
Native `availableDecisions` become opaque stable choice IDs; Core does not
reinterpret once/session/amendment scope. Permission choices map back to the
native response payloads: grant returns the exact requested profile and
decline returns an empty profile. The raw profile stays adapter-local; only a
bounded readable summary crosses in the approval prompt.

Command, path, reason, and permission data are untrusted. The adapter produces
a bounded factual prompt, and the Markdown Presenter places it in an indented
code block. A native `isSecret` question remains typed as `secret=True`; the
Codex adapter does not reject it merely because the default Markdown presenter
cannot collect it securely.

The current Codex App Server protocol exposes no authoritative pending-request
snapshot. A connection reset therefore invalidates outstanding transport
response handles and emits a stale projection; the adapter does not reconstruct
the request from Gateway correlation. Transport notifications carry their
dispatch epoch; `serverRequest/resolved` uses the owning pending record for
Thread/Turn routing and cannot resolve a reused request ID from another epoch.
If resolution arrives while a response write is awaiting transport
acknowledgement, the native terminal state wins and cannot be overwritten back
to `responded`.
Recent responded/resolved/stale diagnostics are retained in a 256-entry LRU;
after eviction, an unknown response fails stale rather than growing
process-lifetime state. If an already-responded entry is evicted before the
native resolved notification arrives, the adapter first emits a stale
projection while it still owns the Thread/Turn scope. A later request-ID-only
notification is then honestly ignored outside the retained correlation
window instead of leaving Gateway state indefinitely `responded`.

## Attachments

Local paths require an explicitly configured shared root. Remote endpoints that
cannot access or upload a source reject it. Codex workspace, sandbox, approval,
and Full Access semantics are not chosen by the SDK.

Deployment-owned `thread_start_options` may provide App Server-native defaults
for newly created Threads. The adapter copies the mapping, rejects ambiguous
aliases and attempts to replace its configured `cwd`, and does not persist the
mapping as SDK Thread state.

Construction requires the deployment's stable `workspace_id` in addition to
`cwd`. `project.list` and `project.get` return that one workspace Project;
`project.create` and deletion/switching remain typed unsupported. Every mapped
Thread, event, history value, and request uses that ProjectRef. Reusing the ID
with a changed root yields a changed typed fingerprint for the block-B Gateway
startup check; intentional replacement uses a new ID.

The current App Server image input accepts only a pathname and opens it after
SDK validation, so it cannot bind descriptor-acquired bytes to the native
read. Codex and Zen therefore do not advertise attachment sources and reject
path-only image input before acquisition or native dispatch. A future
consumer-owned safe materialization or byte-taking native protocol must be
explicit. App Server also exposes no formal generic-file input item; the
adapter does not invent a prompt template or silently disclose an absolute
host path.

## Current client ownership

The SDK owns the reusable JSON-RPC client, target model, bounded retry, and
supervisor under `applications/adapters/appserver/client/`; stable redacted diagnostic
facts and internal App Server diagnostic helpers are owned by
`applications/adapters/appserver/diagnostics.py`; protocol/resource mapping is owned by
`applications/adapters/appserver/mapping.py`, while stdio/WebSocket framing is
owned by `applications/adapters/appserver/transport.py`. Local paths are exposed only
for stdio/Unix-socket transports or an explicitly verified shared filesystem.

The stable SDK diagnostic provider exposes only connection state/epoch,
reconnect count, dispatch worker state, fixed notification/server-request
queue facts, and bounded failure classification. Endpoint, local path,
protocol payload, native resource IDs, and exception text remain excluded.
Legacy debug summaries are internal and are not described as ADR-0014 facts.

IMCodex-specific configuration loading, launcher behavior, branding, command
surface, and product supervision remain consumer decisions. Its later
composition migration and duplicate removal are still required to complete
Issue #9; see the
[owner transfer map](../../../migrations/issue-9-imcodex-owner-transfer.md).
