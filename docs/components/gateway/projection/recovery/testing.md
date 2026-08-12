# Gateway projection recovery testing

Recovery conformance must prove:

- replay-capable and no-replay Applications take their distinct documented
  ordering paths, with live observation installed before history when replay is
  absent;
- cursor expiry, sequence gaps, missing checkpoints, and bounded page/item
  exhaustion remain explicit degraded facts rather than synthetic success or an
  unbounded archive read;
- a new route gets only a bounded baseline, while an existing route reconciles
  toward its own checkpoint without weakening other routes;
- exact adapter-owned newly-created/pre-input evidence may produce an empty
  checkpoint-free baseline without a native history call; its first dispatch
  or allowlisted turn-bearing event retires that evidence while a thread-only
  or unknown event with an extraneous Turn ID does not; stop/reset, native
  epoch/session/non-authorized revision change, same-ID recreation, and foreign activity restore
  strict recovery; a racing event does not invalidate the captured baseline
  and remains queued behind it; first input avoids only the unavailable turn-bearing
  continuation read while retaining ordinary Thread scope validation;
  its exact evidence generation is revalidated after the dispatch fence, so a
  reset/reconnect, allowlisted Turn notification/request, or same-ID
  replacement prevents native start and cannot retire successor evidence;
  a delayed partial `thread/started` preserves the empty baseline only after a
  locked authoritative no-turn scope read and may authorize the bounded all-
  equal creation-clock revision family, while non-creation revision,
  foreign session/native identity, scope failure, and reset restore strict
  recovery, and stale epochs and concurrent same-ID successors remain
  generation-safe;
  non-new, reconstructed, ambiguous, and
  checkpointed history failures stay explicit;
- baseline/recovery completes before that route drains live events, and a
  failure keeps its bootstrap fence closed;
- scoped-action recovery validates the same projection lifecycle and live
  worker around authoritative suspension points; stop or worker loss cannot
  complete its fence or report activation success, while ordinary worker
  recovery remains on the unchanged bounded path;
- completed idempotency can give the checkpoint owner bounded authoritative
  evidence to converge a lagging checkpoint, while `in_flight` or live-only
  output cannot;
- subscription/recovery failure uses bounded retry and is isolated per Thread;
  one typed permanent or retryable Channel destination failure does not restart
  observation and does not block a healthy destination, while an injected
  checkpoint/repository failure restarts only the affected Thread's existing
  worker and converges through authoritative recovery;
- an acceptance-buffer overflow or mid-drain ordered-event failure injects one
  typed external gap into that same supervisor for only its Thread; it keeps
  the accepted input terminal, preserves the one worker, uses the supervisor's
  capped classification/backoff, and leaves no stale cancellation marker after
  terminal-worker or no-longer-active-route recovery;
- the focused supervisor returns capped retry and typed gap/degradation facts
  while observation remains the sole owner of subscription open, consumption,
  close, and resubscription;
- count limits reject zero, negative, boolean, and non-integer values, while
  retry bounds reject boolean, non-numeric, non-finite, negative, and reversed
  ranges without leaking a generic type error;
- native request snapshots are reconciled only for the affected Thread and
  only when the Application advertises authoritative support; and
- no-snapshot reconnect/restart leaves request recovery explicitly degraded
  and never manufactures pending requests from correlations.

Focused recovery evidence is `tests/gateway/projection/test_recovery.py`,
including bounded route reads, supervisor classification/backoff, and typed
request-snapshot coordination. Genuine worker, route-delivery, acceptance,
and checkpoint integration evidence remains in
`tests/gateway/projection/test_hardening.py` and `tests/gateway/routing/test_projection_integration.py`.
The focused suite also proves that the Gateway projection facade re-exports
the exact owner objects and that the historical `imagent.recovery` module is
unavailable in a clean process.

The canonical public restart acceptance additionally runs through
`tests/gateway/test_reference_consumer.py` and the installed
`examples.reference_consumer.main`. It must close and discard the original
Gateway, Channel, and `SQLiteGatewayStore`, reuse only the example Application
whose authoritative history survives, and construct fresh objects on the same
database. The second lifecycle restores one active worker for the stable
Thread, delivers only output missed while absent, replays terminal action
receipts before native work, and exposes the restored binding through scoped
public actions with its original generation. It must also compare the active
destination checkpoint identities before shutdown and after recovery, retain
completed idempotency evidence, and suppress a duplicate prior Channel input
without another Application call or delivery. Direct SQLite
schema/database/sidecar inspection must use a WAL-aware read snapshot and fail
closed on unknown or changed tables, indexes, triggers, views, columns, SQLite
value types, row cardinalities, oversized or malformed values, and encoded or
fragmented content/native-authority evidence. The bounded sidecar entry set and
path identities must remain stable throughout descriptor inspection.
