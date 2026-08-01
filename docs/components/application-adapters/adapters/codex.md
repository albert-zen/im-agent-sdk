# Codex Application mapping

## Native authority

The Codex App Server owns native Threads, Turns, items, requests, execution,
history, interruption, and retention. The SDK adapter is a projection over
that control surface.

## Mapping

- SDK Thread maps to a native Codex Thread/session.
- Fixed workspace/cwd is adapter configuration, not a synthetic Project.
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

New input starts a Turn by default. A consumer may opt this Codex adapter into
active-Turn continuation. The adapter then reads the authoritative native Turn
list immediately before dispatch and steers the observed active Turn. That
read is only a candidate selection, not cached truth: the native steer response
supplies the accepted Turn identity. A definitive native rejection is itself
the authoritative reconciliation result and is re-raised unchanged without a
second read or `turn/start` fallback. An ambiguous steer remains unknown.
Zen does not inherit this Codex-only opt-in merely because it shares the
transport implementation.

`message.completed` does not terminate a Codex Turn. The adapter emits the
native terminal Turn event separately.

## Recovery guarantees

The current App Server seam does not claim native replay when unavailable. It
omits cursor/sequence fields and uses authoritative Thread/Turn reads plus a
fresh live subscription.

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

Local images also carry the connection epoch that proved shared-filesystem
access. A reconnect between verification and dispatch therefore fails closed.
App Server exposes no formal generic-file input item, so generic files are
explicitly unsupported by default. A future downstream encoding/exposure
policy must be explicit; the adapter does not invent a prompt template or
silently disclose an absolute host path.

## Current client ownership

The SDK owns the reusable JSON-RPC client, stdio/WebSocket transports, target
model, bounded retry, protocol classification, redacted diagnostics, and
supervisor under `applications/appserver_client/`. Local paths are exposed
only for stdio/Unix-socket transports or an explicitly verified shared
filesystem.

IMCodex-specific configuration loading, launcher behavior, branding, command
surface, and product supervision remain consumer decisions. Its later
composition migration and duplicate removal are still required to complete
Issue #9; see the
[owner transfer map](../../../migrations/issue-9-imcodex-owner-transfer.md).
