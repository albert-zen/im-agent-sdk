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

## Recovery guarantees

The adapter advertises only native replay/order guarantees. Without native
replay, recovery uses a fresh subscription plus authoritative Thread/Turn
history. No SDK transcript or synthetic sequence is created.

The current Zen adapter reuses the App Server client transport, but repository
evidence does not prove that Zen emits Codex request methods or accepts Codex
response payloads. It therefore advertises interactive requests as
unsupported. Shared transport code is not treated as a second independent
native protocol proof.

## Product boundary

Zen-specific provider, model, runtime mode, workspace UI, tools, approval
policy, and orchestration remain outside common Core. A Zen-only requirement
must not enter a shared contract without a second real Application proof.
