# Applications: the native boundary

The Applications layer normalizes one external Agent Application into the
public `AgentApplicationAdapter` contract. The external Application remains
authoritative for Projects, Threads, Turns, transcript items, requests, and
execution. The adapter must not create a second runtime, transcript, event
journal, or subscription for Gateway consumers.

The neutral adapter is
[`ReferenceApplication`](../../examples/reference_consumer/application.py).
It implements the minimum useful surface for the example:

- managed Project discovery/read/create, with explicit caller-provided CWD,
  stable operation IDs, canonical-root fingerprint evidence, and no default
  workspace;
- stable Project-scoped `ThreadRef`/`TurnRef` values and native Thread creation;
- typed `send_input()` with truthful `started` acceptance and a
  `before_dispatch` fence;
- canonical `AgentEvent` values published through independent bounded fan-out;
- `thread.history` and `turn.catchup` reads from bounded local history that is
  authoritative only for this demo Application’s lifetime;
- opaque replay cursors and stable event/item IDs; and
- optional redacted `ApplicationDiagnosticFacts`.

The local history is part of this adapter’s simulated native authority. It is
bounded, is not durable, is not persisted by Gateway, and is not exposed as an
SDK transcript. A real adapter replaces this class with native resource,
input, event, and history calls while preserving the same typed contracts.

All Application modes expose Project list/read truth. Managed adapters return
native Projects. Fixed and flat adapters return exactly one stable workspace
Project with `fallback` discovery/reading and typed unsupported management.
Callers must pass a Project explicitly when creating or listing Threads. The
reference flow discovers and reads its Application through the two public
action factories, then calls Conversation-scoped
`create_and_select_project(...)` with an explicit managed CWD. There is no
Project-less constructor, implicit single-workspace selection, or adapter-owned
binding.

`max_projects`, `max_threads`, `max_turns_per_thread`, and
`max_events_per_thread` are positive explicit bounds. Project creation, Thread
creation, a new Turn, and the three replay events required by a Turn are
rejected before native mutation or dispatch when their bound would be
exceeded. The bounded event fan-out also keeps pending subscription work
finite; cursor failures remain explicit.

## Event and recovery requirements

Every event has a stable `event_id`; sequence and cursor values are included
only because this deterministic adapter can truthfully replay them. A real
adapter must omit unsupported ordering guarantees and surface cursor expiry
or gaps explicitly.

`message.completed` is an item observation, not a terminal Turn event. The
adapter emits an explicit `turn.completed` event and returns a typed
`AcceptedTurn`. Gateway uses the acceptance result to preserve reply
correlation; later durable-recovery acceptance uses `thread.history` and
`turn.catchup` without introducing an SDK transcript.

The Application adapter has no knowledge of Conversations, Channel delivery,
projection routes, or product commands. Those concerns stay in the consumer’s
Gateway and Interaction composition.
