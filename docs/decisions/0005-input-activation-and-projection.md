# ADR 0005: Input selection, native activation, and output projection

Status: Accepted

## Context

Selecting where the next IM input goes, changing a native application's active
Thread, and deciding where a Thread's output is delivered are different
authorities. Treating them as one mutation causes cross-client interference
and loses background output.

## Decision

- `ConversationBinding` selects future input.
- `thread.activate_native` optionally changes native Application UI state.
- `ThreadProjectionRoute` selects IM output destinations.

No one activates another, and observing never selects input. The narrow
`foreground_only` consistency exception is that `conversation.bind_thread`
prepares the matching `ThreadProjectionRoute` before binding CAS: binding
equality is already that policy's sole output authority, so the route is
inactive before the bind and after a later switch. This does not activate
native UI state or apply to `remembered_last_recipient`/`all_observers`;
products may compose explicit observation for those policies and to refresh
destination reply context.

Routes persist only stable Thread/Conversation references, optional
destination reply context, a per-destination delivery checkpoint, and update
times. Separate bounded-lifetime correlation links an accepted IM-originated
Turn to its originating reply target. Neither stores transcript, Turn status,
request, or execution truth.

Gateway supports `foreground_only`, `remembered_last_recipient`, and
`all_observers`. Projection workers are Thread-scoped and rebuild from routes
plus authoritative history/catch-up.

## Consequences

Cross-client selection stays isolated. Output observation survives or is
reclaimed according to policy. Per-Turn reply correlation comes from
`AcceptedTurn`, never the last inbound message stored on a long-lived route.
The tested non-yield, single-event-loop one-worker invariant remains explicit;
synchronization is required only if that execution model changes. ADR 0007
defines checkpoint, bootstrap, recovery, and failure-domain details.
