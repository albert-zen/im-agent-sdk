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

`message.completed` does not terminate a Codex Turn. The adapter emits the
native terminal Turn event separately.

## Recovery guarantees

The current App Server seam does not claim native replay when unavailable. It
omits cursor/sequence fields and uses authoritative Thread/Turn reads plus a
fresh live subscription.

## Attachments

Local paths require an explicitly configured shared root. Remote endpoints that
cannot access or upload a source reject it. Codex workspace, sandbox, approval,
and Full Access semantics are not chosen by the SDK.

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
