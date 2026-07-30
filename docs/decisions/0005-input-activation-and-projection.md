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

No one implies another. Default UX may compose them explicitly.

Current routes persist only stable Thread/Conversation references, optional
destination reply context, and update time. They never store transcript, Turn,
request, or execution truth.

Gateway supports `foreground_only`, `remembered_last_recipient`, and
`all_observers`. Projection workers are Thread-scoped and rebuild from routes
plus authoritative history/catch-up.

## Consequences

Cross-client selection stays isolated. Output observation can survive input
switches according to policy. Per-Turn reply correlation must not be inferred
from the last inbound message stored on a long-lived route. The current
non-yield, single-event-loop one-worker invariant needs direct regression
coverage and explicit lifecycle ownership; synchronization is required only
if that execution model changes. Per-Turn correlation, route reclamation,
supervised recovery, and any future content-free projection checkpoint are
target follow-up work in
[Issue #14](https://github.com/albert-zen/im-agent-sdk/issues/14), not claims
about the current baseline.
