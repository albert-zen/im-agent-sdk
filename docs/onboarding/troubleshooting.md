# Troubleshooting

## A command does not run

Confirm that the consumer constructs a bounded `CommandRegistry`, registers at
least one command, freezes it, and passes it as `Gateway(controller=...)`.
Gateway startup validates the Controller lifecycle. A missing or unfrozen
registry fails explicitly; it is not silently replaced by global state.

`/help` and `/about` are handled before normal Application input. A non-command
message returns `None` from the Controller and continues through Gateway
binding and Application dispatch.

## A Conversation receives no Thread output

Check the binding returned by the scoped `bind_thread` action and the selected
`ProjectionPolicy`. With `foreground_only`, the route is active only while
the stored binding’s Thread equals the route’s Thread. Binding alone does not
activate native UI state, and observing a Thread does not select future input.

Use `gateway.diagnostics()` for redacted aggregate and projection-health facts.
The stable snapshot intentionally omits Conversation, route, Thread, native
message, content, path, endpoint, and error-text identities.

## A switched Conversation still sees the old Thread

Verify that the switch used the Conversation-scoped `bind_thread` action and
the current binding generation. Under `foreground_only`, the old route becomes
inactive after the binding changes. A different Conversation that remains
bound to that Thread must continue to receive its output. If both stop, check
that the Application worker was not replaced by consumer-side subscription
code.

## Output is missing after restart

The Application must retain authoritative history and expose `thread.history`
and `turn.catchup`. Gateway restores persisted routes, subscribes before
reconciliation, and reads a bounded baseline or checkpoint window. A missing
or expired checkpoint is reported as degraded recovery; it is never repaired
by reading an SDK transcript or guessing order from timestamps.

The in-memory reference run does not claim durable restart. Use the executable
specification's fresh-object SQLite scenario: restore durable Application
history and one `SQLiteGatewayStore`, and keep stable Thread and Agent item IDs
unchanged.

If the reference Application reports a capacity error, increase its explicit
positive limits or replace it with a production Application. Do not add
unbounded maps, silent event eviction, or a second transcript to make recovery
appear to work.

## Shutdown hangs or accepts late input

Always close the Gateway from a `finally` block. Its lifecycle closes the
admission gate, observation workers, delivery coordination, Channels,
Controllers, extension runtimes, and Applications in order. A late Channel
callback must fail or release its owned pre-side-effect admission; it must not
reach a stopping Application. Inspect task ownership in the consumer, not by
adding a second SDK runtime or queue.

If shutdown reports a lifecycle owner timeout, identify the fixed owner class
in the bounded failure note and repair that adapter or consumer lifecycle. Do
not raise the limit indefinitely, retry the owner, or start a replacement
Gateway before the old store lease has expired or closed. `Gateway` instances
are terminal after stop; reconstruct a fresh object graph for restart.

## Startup reports a workspace or lease conflict

A workspace fingerprint conflict means a configured stable workspace ID now
points at a different canonical execution root. Restore the intended root or
assign a new workspace ID so retained bindings become explicitly stale. Do not
edit bridge rows or expose the root path through diagnostics.

A lease conflict means another Gateway currently owns the same
`gateway_id`/store namespace. Stop that owner or wait for store-authoritative
expiry; do not bypass fencing or mix Memory and SQLite repository owners.

## Diagnostics look incomplete

Diagnostics are synchronous, process-local, redacted aggregate facts. A
stopped/cold Gateway has no live snapshot, T3 has no synthetic App Server
connection epoch, and Telegram/Weixin have no invented inbound queue. Use the
native Application for Thread, Turn, request, transcript, and execution truth.
Export schedules, labels, alerts, and operator wording belong to the consumer;
never add content, IDs, paths, endpoints, credentials, or exception text to
the SDK snapshot.
