# Applications: the native boundary

The Applications layer normalizes one external Agent Application into the
public `AgentApplicationAdapter` contract. The external Application remains
authoritative for Projects, Threads, Turns, transcript items, requests, and
execution. The adapter must not create a second runtime, transcript, event
journal, or subscription for Gateway consumers.

The neutral adapter is
[`ReferenceApplication`](../../examples/reference_consumer/application.py).
It implements the minimum useful surface for the example:

- a flat Application with stable `ApplicationRef` and `ThreadRef` values;
- typed `send_input()` with truthful `started` acceptance and a
  `before_dispatch` fence;
- canonical `AgentEvent` values published through independent bounded fan-out;
- `thread.history` and `turn.catchup` reads from bounded local history that is
  authoritative only for this demo Application’s lifetime;
- opaque replay cursors and stable event/item IDs; and
- optional redacted `ApplicationDiagnosticFacts`.

The local history is part of this adapter’s simulated native authority. It is
bounded, is not durable, is not persisted by Gateway, and is not exposed as an
SDK transcript. The sample’s stop/start recovery therefore reuses this same
Application object. A real adapter replaces this class with native resource,
input, event, and durable history calls while preserving the same typed
contracts.

`max_threads`, `max_turns_per_thread`, and `max_events_per_thread` are
positive explicit bounds. Thread creation, a new Turn, and the three replay
events required by a Turn are rejected before dispatch or native mutation when
their bound would be exceeded. The bounded event fan-out also keeps pending
subscription work finite; cursor failures remain explicit.

## Event and recovery requirements

Every event has a stable `event_id`; sequence and cursor values are included
only because this deterministic adapter can truthfully replay them. A real
adapter must omit unsupported ordering guarantees and surface cursor expiry
or gaps explicitly.

`message.completed` is an item observation, not a terminal Turn event. The
adapter emits an explicit `turn.completed` event and returns a typed
`AcceptedTurn`. Gateway uses the acceptance result to preserve reply
correlation and uses `thread.history`/`turn.catchup` to rebuild projection
state after a restart.

The Application adapter has no knowledge of Conversations, Channel delivery,
projection routes, or product commands. Those concerns stay in the consumer’s
Gateway and Interaction composition.
