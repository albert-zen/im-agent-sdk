# Gateway projection observation testing

Observation conformance must prove:

- concurrent first routes for one Application/Thread create and register one
  worker, while different Threads remain isolated;
- a positive active-Thread capacity admits the final distinct `ThreadRef`,
  lets same-Thread starters/waiters join that worker, and rejects only another
  Thread before subscription, authoritative reconciliation/checkpoint,
  presentation, or Channel delivery; task termination between same-Thread
  admission and ensure must retain that identity until the caller completes;
- cancellation of a same-Thread waiter leaves the shared worker and its slot
  intact; terminal, cancelled, and start-failed workers release worker-owned
  capacity and health entries; a worker terminal during pending acceptance must
  retain the dispatch-owned acceptance-ordering gate, lock, and buffer until the final dispatcher
  performs required correlation work and drains it; the established restore
  boundary invokes the dispatch-owned reset before route/checkpoint recovery;
- every live subscriber has an independent finite queue, and a slow or
  overflowed subscriber neither steals from nor blocks another subscriber or
  an Application socket/read callback;
- a subscriber overflow creates a typed recovery gap instead of an SDK event
  log, unbounded buffer, or silent sequence claim;
- `message.completed` does not end a multi-message Turn, and only explicit
  terminal Turn events do;
- baseline delivery precedes live draining for each route without creating a
  second content queue;
- an adapter-authoritative empty baseline for an exact newly created
  pre-input Thread still subscribes before baseline, opens no live-before-
  baseline window, and delivers/checkpoints the first live output exactly
  once, including when that Turn event arrives during the empty baseline's
  scope read; non-new and checkpointed routes remain strict;
- the public action-route reconciliation method reads current route/binding
  authority, activates and baselines a successful or terminally replayed route,
  ignores a replayed route removed by later intent, stops newly unauthorized
  workers, and surfaces capacity/activation failure instead of false success;
- stop closes action-route admission before worker cancellation; reconciliation
  racing stop, beginning after stop, or losing its worker between ensure and
  baseline cannot report success, leak worker/capacity/barrier/lock state, or
  open an incomplete route, while restart and terminal replay converge it;
- stop and post-preflight route commit are deterministically ordered by the
  lifecycle generation fence, and pre-entry cancellation retires its barrier,
  action lock, and commit capacity without weakening post-entry fencing;
- same-route action-lock waiters released by shutdown return typed stale
  outcomes with empty waiter/lock maps, and a terminal stale-binding workflow's
  explicit route-absent fact retires its post-entry generation;
- stop during an actual multi-item Channel baseline is checked at every
  delivery/checkpoint suspension: the in-flight action returns typed partial,
  no later item crosses shutdown, and an unknown native outcome is not retried
  or converted to false success on restart replay;
- scoped observe/foreground-bind composition installs this owner's sole route
  barrier before durable visibility, releases it after successful baseline,
  retains it after baseline failure/cancellation, and releases it when later
  explicit replay converges;
- generation-specific action leases serialize same-route lifecycles, prevent a
  concurrent or stale completion from releasing another action's barrier, and
  survive route removal/re-add without an ABA release;
- delivery queued behind a route lock rechecks the current barrier generation,
  while removal retires the closed generation, wakes waiters, cancels retries,
  and cancellation while acquiring an action lease releases its Thread-start
  reservation;
- retryable and terminal typed destination decisions do not restart the Thread
  worker or block other routes, whereas an injected checkpoint/repository
  failure enters only the affected Thread's existing recovery worker and
  converges under bounded backoff;
- live-only presentation shares route ordering but has a distinct stable event
  identity, never enters authoritative recovery, and never advances a
  checkpoint; and
- a buffered event accepted before Turn-correlation persistence stays bounded;
  overflow enters recovery without changing claim phase: known pre-native-fence
  failure still releases, while native-side-effect-fence-unknown or accepted input remains
  non-redeliverable.

Current evidence: `tests/gateway/projection/test_observation.py`,
`tests/gateway/projection/test_hardening.py`, and
`tests/gateway/routing/test_projection_integration.py`. The focused suite also
proves exact finite projection-facade identity and that the historical
observation runtime/value modules fail in a clean process. The target mirrored
suite is `tests/gateway/projection/test_observation.py`.

Fresh-object SQLite acceptance is not satisfied by stopping and restarting one
runtime object. The public reference-consumer suite must prove that a newly
constructed Gateway/store pair restores the durable route into exactly one new
worker/subscription for that lifecycle, while the prior lifecycle has no
active worker and the shared authoritative Application never has two active
subscriptions for the stable Thread.
