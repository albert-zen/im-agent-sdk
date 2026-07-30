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

The current Python composition imports the pinned IMCodex App Server
client/supervisor. Issue #9 transfers the reusable client, fixtures, tests,
license/provenance, and lifecycle into this SDK. IMCodex-specific supervision
and product configuration remain consumer decisions.
