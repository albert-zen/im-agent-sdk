# Gateway routing bindings design

## Purpose and ownership

`gateway.routing.bindings` owns the one current Application, Project, and
optional Thread selection for a stable Conversation. A binding answers only
where the Conversation's next input goes. It is passive Gateway bridge state,
not native Application UI state and not a subscription.

This leaf owns:

- typed project/Thread bind and Thread-clear postconditions;
- the monotonically revised `ConversationBinding` value;
- optimistic compare-and-swap coordination;
- the binding-equality authority used by `foreground_only` projection.

It does not own Application Project or Thread truth, native Thread activation,
projection checkpoints, Application observation workers, product command
grammar, or a consumer's JSON display cache. Controllers may submit the public
typed actions, but product UX such as `/new`, `/pick`, CWD, or profile selection
stays outside Gateway routing.

## Contract and dependencies

A stable Conversation key has at most one current binding. Multiple
Conversations may independently bind the same Application Thread. Binding
operations validate referenced native resources through the Application
contract, then persist only stable references and the revision; they never
copy Project, Thread, transcript, Turn, or execution state.

The public operation/result contracts are `BindConversationToProject`,
`BindConversationToThread`, `ClearConversationThread`, and
`ConversationBound`. Their current and target exports are recorded in the
[component map](../../../component-map.yml). Repository contracts live in
Gateway persistence; concrete repositories do not become part of this leaf.

## State, concurrency, and recovery

Mutations for one Conversation are serialized and still use repository
revision comparison as the durable conflict boundary. A stale writer cannot
overwrite a later selection. After a crash or an ambiguous repository result,
a same-target foreground bind may converge only when the stored binding equals
the complete requested target **and** its supplied revision guard is absent,
the current revision, or the immediately preceding revision. Any other guard
fails before route preparation and cannot release an existing recovery fence.
A different later binding remains authoritative and fails the stale attempt
explicitly.

For `foreground_only`, binding a Thread prepares the matching additive route
and its projection bootstrap ordering before the binding compare-and-swap.
That prepared route has no delivery authority until the binding equals its
Thread, and it immediately loses authority after a later binding switch. This
narrow ordering rule prevents output gaps without merging binding and route
state. Switching Conversation A away from Thread 1 stops Thread 1 projection
to A; other Conversations still bound to Thread 1 are unaffected.

After restart, persisted binding state selects foreground observation and
delivery authority. Authoritative Thread existence and history are reconciled
through the Application boundary; the binding never acts as a transcript or a
second Thread registry.

## Current structural gap

The operation values currently live in the broad contracts package while
validation and execution are coordinated from the Gateway package root. A
later behavior-preserving slice will move them to the target routing module
and mirror their tests without retaining parallel internal implementations.
