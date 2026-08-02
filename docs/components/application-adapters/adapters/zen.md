# Zen Application mapping

## Native authority

Zen owns its native Thread/ItemList, Turns, items, requests, execution,
history, and retention.

## Mapping

- SDK Thread maps to the native Zen App Server Thread.
- Project is exposed only by a real outer workspace/project registry; it is not
  forced into Zen's append-only runtime.
- the shared App Server adapter maps native resource operations, input,
  history/catch-up, interruption, and notifications.
- every completed Agent item is preserved before the explicit terminal Turn
  event.
- because the shared transport has no native input-idempotency key, a lost
  `turn/start` acceptance response is reported as an unknown input outcome and
  must not be retried automatically.
- local images use the shared client-proven connection epoch and fail closed
  when the configured filesystem is not verified for that connection.
- deployment-owned `thread_start_options` may supply Zen-native sandbox and
  approval defaults for new Threads; the adapter copies them, owns `cwd`, and
  does not persist them as SDK Thread state.
- a consumer with per-Conversation presets may use the concrete
  `create_thread_with_options` seam and bind its returned native Thread through
  Gateway; the preset remains client selection/configuration, not Zen or SDK
  runtime state.

Zen accepts the SDK's default continuation preference but truthfully returns
`started/create_new`. Codex active-Turn steering is not inferred for Zen from a
shared transport; Zen remains start-only until its own native behavior proves
an equivalent native policy.

## Recovery guarantees

The adapter advertises only native replay/order guarantees. Without native
replay, recovery uses a fresh subscription plus authoritative Thread/Turn
history. `event_buffer_max_pending` bounds each live subscriber and overflow
enters that authoritative recovery path. No SDK transcript or synthetic
sequence is created.

The IMZen integration provides native Zen evidence for command approval: Zen
emits the App Server command-approval method and accepts its decision payload.
The adapter therefore uses the shared typed App Server request runtime and
advertises interactive requests as native. Other request methods remain
unevidenced for Zen and are explicit protocol errors; approval policy and Full
Access selection remain consumer/Zen policy rather than SDK policy. Zen still
does not expose an authoritative pending-request snapshot, so reconnect makes
transport-bound request handles stale instead of manufacturing recovery state.

Zen receives the shared stable App Server diagnostic provider: connection
state/epoch, reconnect count, worker state, bounded dispatch queues, and fixed
failure classifications. Native IDs, content, endpoints, paths, and exception
text are excluded just as they are for Codex.

Zen does not expose the Codex live-activity presenter merely because it shares
the App Server transport. No independently evidenced Zen live activity shape
is added in the non-artifact A1 slice, and default Zen events/history remain
unchanged.

## Product boundary

Zen-specific provider, model, runtime mode, workspace UI, tools, approval
policy, and orchestration remain outside common Core. A Zen-only requirement
must not enter a shared contract without a second real Application proof.
