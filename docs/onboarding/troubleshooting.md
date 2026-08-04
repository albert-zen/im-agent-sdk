# Troubleshooting

## A command does not run

Confirm that the consumer constructs a `CommandRegistry`, registers at least
one command, freezes it, and passes it as `GatewayExtensions(controller=...)`.
Gateway startup validates the Controller lifecycle. A missing or unfrozen
registry fails explicitly; it is not silently replaced by global state.

`/help` and `/about` are handled before normal Application input. A non-command
message returns `None` from the Controller and continues through Gateway
binding and Application dispatch.

## A Conversation receives no Thread output

Check the binding returned by `BindConversationToThread` and the selected
`ProjectionPolicy`. With `foreground_only`, the route is active only while
the stored binding’s Thread equals the route’s Thread. Binding alone does not
activate native UI state, and observing a Thread does not select future input.

Use `gateway.list_projection_health()` for per-Thread troubleshooting and
`gateway.diagnostics_snapshot()` for redacted aggregate facts. The stable
snapshot intentionally omits Conversation, route, Thread, native message,
content, path, endpoint, and error-text identities.

## A switched Conversation still sees the old Thread

Verify that the switch used a typed `BindConversationToThread` operation and
the current binding revision. Under `foreground_only`, the old route becomes
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

The reference sample intentionally reuses the same process-local Application
and Gateway repository objects across stop/start. A production restart must
instead restore durable Application history and durable Gateway repositories;
stable Thread and Agent item IDs must remain unchanged.

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
