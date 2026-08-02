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
- when Zen emits the shared App Server command/file or live plan/status shapes,
  they use the same bounded completed/created message mapping; no Codex product
  visibility default is inferred.

That additional native activity projection is disabled by default and must be
enabled explicitly by a consumer that owns its visibility policy.
The shared optional App Server presentation hook is also available for typed
artifact-candidate materialization, but Zen does not infer Codex artifact
shapes or storage policy when its native items provide no such candidates.
- because the shared transport has no native input-idempotency key, a lost
  `turn/start` acceptance response is reported as an unknown input outcome and
  must not be retried automatically.
- local images use the shared client-proven connection epoch and fail closed
  when the configured filesystem is not verified for that connection.

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

The current Zen adapter reuses the App Server client transport, but repository
evidence does not prove that Zen emits Codex request methods or accepts Codex
response payloads. It therefore advertises interactive requests as
unsupported. Shared transport code is not treated as a second independent
native protocol proof.

Zen receives the shared stable App Server diagnostic provider: connection
state/epoch, reconnect count, worker state, bounded dispatch queues, and fixed
failure classifications. Native IDs, content, endpoints, paths, and exception
text are excluded just as they are for Codex.

## Product boundary

Zen-specific provider, model, runtime mode, workspace UI, tools, approval
policy, and orchestration remain outside common Core. A Zen-only requirement
must not enter a shared contract without a second real Application proof.
